"""Local immutable artifact store + atomic current pointer (§9).

Layout under ``AI_BRIEF_STORE_DIR``::

    snapshots/<snapshot_id>/<source>/<repo-relative-path>   # materialized sources
    candidates/<candidate_id>/work/                          # mutable build staging
    artifacts/<artifact_digest>.tar.gz                       # immutable published blobs
    provenance/<candidate_id>.json                           # per-candidate provenance
    published/<candidate_id>.json                            # per-candidate publish record
    publication-fence.json                                   # newest registered generation
    current.json                                             # the active pointer

Distinct concepts kept separate on purpose:
  - candidate id     : logical declared inputs/build spec       (candidate.py)
  - artifact digest  : content hash of the produced index files (this module)
  - publication gen  : ordering/freshness authority             (authority.py)
  - published version : immutable label exposed to consumers    (this module)

The authority mirrors its newest registered generation into a durable
publication fence before a registration completes. The fence update and the
``current`` compare/swap use the same filesystem lock. A stale or timed-out
Activity therefore cannot publish after a newer generation has registered, even
if that Activity keeps running after its Workflow handler has moved on. The
``os.replace`` swap remains atomic for readers. Artifacts are content-addressed
and never mutated in place.
"""
from __future__ import annotations

import fcntl
import json
import os
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from .canonical import sha256_bytes, sha256_json, short
from . import config


class StalePublicationError(RuntimeError):
    """A publication tried to move ``current`` to an older generation."""


class PublicationGenerationConflict(RuntimeError):
    """Two different publications claimed the same generation."""


# --- paths ------------------------------------------------------------------

def _root() -> Path:
    return config.store_dir()


def snapshots_dir() -> Path:
    return _root() / "snapshots"


def snapshot_path(snapshot_id: str) -> Path:
    return snapshots_dir() / snapshot_id


def candidate_dir(candidate_id: str) -> Path:
    return _root() / "candidates" / candidate_id


def work_dir(candidate_id: str) -> Path:
    return candidate_dir(candidate_id) / "work"


def artifact_path(artifact_digest: str) -> Path:
    return _root() / "artifacts" / f"{artifact_digest}.tar.gz"


def provenance_path(candidate_id: str) -> Path:
    return _root() / "provenance" / f"{candidate_id}.json"


def published_record_path(candidate_id: str) -> Path:
    return _root() / "published" / f"{candidate_id}.json"


def publication_fence_path() -> Path:
    return _root() / "publication-fence.json"


def current_path() -> Path:
    return _root() / "current.json"


def _publication_lock_path() -> Path:
    return _root() / ".publication.lock"


# --- atomic IO --------------------------------------------------------------

def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)  # atomic on POSIX
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def write_json(path: Path, value: Any) -> None:
    _atomic_write_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


# --- artifact packaging (content-addressed, idempotent) ---------------------

def content_digest(index_dir: Path, extra_files: list[Path] | None = None) -> str:
    """Digest of the *contents* of an index directory (order-independent).

    Deliberately independent of tar packing: we hash the sorted map of
    relative-path -> sha256(file bytes). Two builds with identical index files
    therefore share an artifact digest even if their tar bytes differ (Chroma
    files are not guaranteed byte-identical across builds — §9).
    """
    files: dict[str, str] = {}
    for path in sorted(p for p in index_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(index_dir).as_posix()
        files[f"index/{rel}"] = sha256_bytes(path.read_bytes())
    for path in extra_files or []:
        if path.is_file():
            files[path.name] = sha256_bytes(path.read_bytes())
    return short(sha256_json({"schema": config.INDEX_SCHEMA_VERSION, "files": files}), prefix="art_")


def _reproducible_tarinfo(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo:
    tarinfo.mtime = 0
    tarinfo.uid = tarinfo.gid = 0
    tarinfo.uname = tarinfo.gname = ""
    return tarinfo


def package(index_dir: Path, extra_files: list[Path] | None = None) -> tuple[str, Path]:
    """Package an index dir into an immutable, content-addressed tarball.

    Returns ``(artifact_digest, artifact_path)``. Idempotent: if the artifact
    already exists it is reused rather than rewritten, so a retry or resumed
    workflow never corrupts a published blob.
    """
    digest = content_digest(index_dir, extra_files)
    out = artifact_path(digest)
    if out.exists():
        return digest, out
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".tmp", dir=str(out.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with tarfile.open(tmp, "w:gz", format=tarfile.GNU_FORMAT) as tar:
            members = sorted(
                (p for p in index_dir.rglob("*") if p.is_file()),
                key=lambda p: p.relative_to(index_dir).as_posix(),
            )
            for path in members:
                arc = f"index/{path.relative_to(index_dir).as_posix()}"
                tar.add(path, arcname=arc, filter=_reproducible_tarinfo)
            for path in sorted(extra_files or [], key=lambda p: p.name):
                if path.is_file():
                    tar.add(path, arcname=path.name, filter=_reproducible_tarinfo)
        os.replace(tmp, out)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return digest, out


def unpack(artifact_digest: str, dest: Path) -> Path:
    """Extract a published artifact for verification/consumption."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(artifact_path(artifact_digest), "r:gz") as tar:
        tar.extractall(dest, filter="data")  # our own content-addressed artifacts
    return dest


# --- publication records + current pointer ----------------------------------

def is_published(candidate_id: str) -> dict | None:
    """Return the publish record if this candidate was ever promoted, else None."""
    return read_json(published_record_path(candidate_id), default=None)


def record_publication(entry: dict) -> None:
    """Persist a per-candidate publish record (idempotency anchor for retries)."""
    write_json(published_record_path(entry["candidate_id"]), entry)


def read_publication_fence() -> dict | None:
    return read_json(publication_fence_path(), default=None)


def fence_generation(generation: int, candidate_id: str) -> dict:
    """Durably fence publication to the newest registered generation.

    This is the external-side-effect half of registration. It shares a lock with
    :func:`advance_current`, which linearizes registration against publication:
    either an older promotion commits first, or the newer registration fence
    commits first and every late older promotion is rejected. Exact Activity
    retries are idempotent, and late retries can never move the fence backward.
    """
    if not isinstance(generation, int) or generation <= 0:
        raise ValueError("publication fence generation must be a positive integer")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("publication fence candidate_id must be a non-empty string")

    path = publication_fence_path()
    lock_path = _publication_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        current = read_publication_fence()
        if current is not None:
            current_generation = current.get("generation")
            current_candidate = current.get("candidate_id")
            if not isinstance(current_generation, int) or current_generation <= 0:
                raise ValueError("publication fence has an invalid generation")
            if not isinstance(current_candidate, str) or not current_candidate:
                raise ValueError("publication fence has an invalid candidate_id")
            if generation < current_generation:
                return current
            if generation == current_generation:
                if current_candidate != candidate_id:
                    raise PublicationGenerationConflict(
                        f"generation {generation} is fenced to {current_candidate!r}, "
                        f"not {candidate_id!r}"
                    )
                return current

        fenced = {"generation": generation, "candidate_id": candidate_id}
        write_json(path, fenced)
        return fenced


def advance_current(entry: dict) -> dict:
    """Atomically advance ``current`` subject to the durable registration fence.

    Temporal Activities are at-least-once and a timed-out attempt can outlive
    its Workflow handler. The shared publication lock makes the newest durable
    registration a fencing token: once generation N+1 is registered, any late
    promotion from N is rejected even if N+1 has not published yet. The same
    transaction also prevents rollback from the already-current generation. An
    exact retry of the active publication is a no-op and returns its canonical
    stored entry, including the original ``published_at`` timestamp.
    """
    generation = entry.get("generation")
    candidate_id = entry.get("candidate_id")
    if not isinstance(generation, int) or generation <= 0:
        raise ValueError("current entry generation must be a positive integer")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("current entry candidate_id must be a non-empty string")

    path = current_path()
    lock_path = _publication_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

        fence = read_publication_fence()
        if fence is None:
            raise StalePublicationError("cannot publish without a registration fence")
        desired_generation = fence.get("generation")
        desired_candidate = fence.get("candidate_id")
        if not isinstance(desired_generation, int) or desired_generation <= 0:
            raise ValueError("publication fence has an invalid generation")
        if not isinstance(desired_candidate, str) or not desired_candidate:
            raise ValueError("publication fence has an invalid candidate_id")
        if generation < desired_generation:
            raise StalePublicationError(
                f"generation {generation} was superseded by registered generation "
                f"{desired_generation}"
            )
        if generation > desired_generation:
            raise PublicationGenerationConflict(
                f"generation {generation} has not been registered; newest fence is "
                f"generation {desired_generation}"
            )
        if candidate_id != desired_candidate:
            raise StalePublicationError(
                f"generation {generation} is fenced to {desired_candidate!r}, "
                f"not {candidate_id!r}"
            )

        current = read_current()
        if current is not None:
            current_generation = current.get("generation")
            if not isinstance(current_generation, int) or current_generation <= 0:
                raise ValueError("current pointer has an invalid generation")
            if generation < current_generation:
                raise StalePublicationError(
                    f"generation {generation} cannot replace current generation "
                    f"{current_generation}"
                )
            if generation == current_generation:
                same_publication = (
                    current.get("candidate_id") == candidate_id
                    and current.get("artifact_digest") == entry.get("artifact_digest")
                )
                if not same_publication:
                    raise PublicationGenerationConflict(
                        f"generation {generation} is already owned by "
                        f"{current.get('candidate_id')!r}"
                    )
                return current

        write_json(path, entry)
        return entry


def read_current() -> dict | None:
    return read_json(current_path(), default=None)
