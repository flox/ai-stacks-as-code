"""Local immutable artifact store + atomic current pointer (§9).

Layout under ``AI_BRIEF_STORE_DIR``::

    snapshots/<snapshot_id>/<source>/<repo-relative-path>   # materialized sources
    candidates/<candidate_id>/work/                          # mutable build staging
    artifacts/<artifact_digest>.tar.gz                       # immutable published blobs
    provenance/<candidate_id>.json                           # per-candidate provenance
    published/<candidate_id>.json                            # per-candidate publish record
    current.json                                             # the active pointer

Distinct concepts kept separate on purpose:
  - candidate id     : logical declared inputs/build spec       (candidate.py)
  - artifact digest  : content hash of the produced index files (this module)
  - publication gen  : ordering/freshness authority             (authority.py)
  - published version : immutable label exposed to consumers    (this module)

The ``current`` pointer is advanced by a single writer (the authority workflow)
via ``os.replace`` — an atomic rename — so a consumer never observes a
half-written pointer. Artifacts are content-addressed and never mutated in place.
"""
from __future__ import annotations

import json
import os
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from .canonical import sha256_bytes, sha256_json, short
from . import config


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


def current_path() -> Path:
    return _root() / "current.json"


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


def advance_current(entry: dict) -> None:
    """Atomically point ``current`` at a published artifact.

    Single-writer (the authority workflow) + ``os.replace`` = a consumer always
    sees either the old or the new pointer, never a partial write.
    """
    write_json(current_path(), entry)


def read_current() -> dict | None:
    return read_json(current_path(), default=None)
