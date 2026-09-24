"""Immutable source snapshots (§2.1).

Each content repository is resolved to an exact commit *before* processing, and
the admitted files are read from that commit's git tree — never from a moving
branch checkout. Two builds that name the same commit therefore admit byte-for-
byte the same sources, even after the branch head has moved on.

A snapshot records, per source:
  - repository identity (remote url, or path basename as a fallback)
  - the exact resolved commit
  - the inclusion/exclusion rules
  - a deterministic manifest of the admitted files, each with its git blob id
    (a content hash)

``source_snapshot_id`` is a hash of that manifest; ``snapshot_id`` combines all
sources. Materialization streams each admitted blob out of git into an isolated
snapshot directory, so downstream stages read plain files with no git needed.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .canonical import sha256_json, short
from . import config, store


class DirtyWorkTreeError(RuntimeError):
    """Raised when a repo has uncommitted changes and dirty snapshots aren't allowed."""


class SnapshotError(RuntimeError):
    pass


def _git(repo: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SnapshotError(f"git {' '.join(args)} failed in {repo}: {proc.stderr.strip()}")
    return proc.stdout


def _is_dirty(repo: str) -> bool:
    return bool(_git(repo, "status", "--porcelain").strip())


def _resolve_commit(repo: str, ref: str) -> str:
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def _remote_identity(repo: str) -> str:
    try:
        url = _git(repo, "remote", "get-url", "origin").strip()
        if url:
            return url
    except SnapshotError:
        pass
    return PurePosixPath(repo).name


def _matches(path: str, patterns: list[str]) -> bool:
    p = PurePosixPath(path)
    return any(p.full_match(pat) for pat in patterns)  # '**' aware (Python 3.13+)


def _admitted_files(repo: str, commit: str, include: list[str], exclude: list[str]) -> list[dict[str, str]]:
    """List blobs in the commit tree that match include and not exclude."""
    out = _git(repo, "ls-tree", "-r", commit)
    files: list[dict[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) < 3 or parts[1] != "blob":
            continue
        blob = parts[2]
        if _matches(path, include) and not _matches(path, exclude):
            files.append({"path": path, "blob": blob})
    files.sort(key=lambda f: f["path"])
    return files


@dataclass(frozen=True)
class ResolvedSource:
    name: str
    repo: str
    commit: str
    manifest: dict[str, Any]

    @property
    def snapshot_id(self) -> str:
        return self.manifest["source_snapshot_id"]

    @property
    def manifest_digest(self) -> str:
        return self.manifest["manifest_digest"]


def resolve_source(spec: dict[str, Any], *, allow_dirty: bool = False) -> ResolvedSource:
    """Resolve one source spec to an immutable, hashed manifest.

    Rejects a dirty work tree by default: we snapshot the *committed* tree, so
    uncommitted edits would silently be excluded from an index that then could
    not be reconstructed from the recorded commit. Pass ``allow_dirty=True`` to
    snapshot the HEAD commit anyway (the fact is recorded in the manifest).
    """
    repo = spec["repo"]
    ref = spec.get("ref", "HEAD")
    dirty = _is_dirty(repo)
    if dirty and not allow_dirty:
        raise DirtyWorkTreeError(
            f"{spec['name']} repo {repo} has uncommitted changes; commit/stash it, "
            f"or pass allow_dirty to snapshot the {ref} commit (ignoring the changes)."
        )
    commit = _resolve_commit(repo, ref)
    include = list(spec.get("include", ["**/*"]))
    exclude = list(spec.get("exclude", []))
    files = _admitted_files(repo, commit, include, exclude)
    if not files:
        raise SnapshotError(
            f"{spec['name']} snapshot admitted 0 files at {commit[:12]} "
            f"(include={include}, exclude={exclude})"
        )

    # The manifest core determines identity; volatile fields (local repo path,
    # dirty flag) are recorded but excluded from the identity hash.
    core = {
        "schema_version": config.SNAPSHOT_SCHEMA_VERSION,
        "name": spec["name"],
        "repo_identity": _remote_identity(repo),
        "commit": commit,
        "include": sorted(include),
        "exclude": sorted(exclude),
        "files": files,
    }
    manifest_digest = sha256_json(core)
    manifest = {
        **core,
        "manifest_digest": manifest_digest,
        "source_snapshot_id": short(manifest_digest, prefix="src_"),
        "file_count": len(files),
        "dirty_worktree": dirty,
        "local_repo_path": repo,
    }
    return ResolvedSource(name=spec["name"], repo=repo, commit=commit, manifest=manifest)


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    sources: tuple[ResolvedSource, ...]
    manifest: dict[str, Any]

    @property
    def content_root(self) -> str:
        return str(store.snapshot_path(self.snapshot_id))

    def source_specs_for_candidate(self) -> list[dict[str, str]]:
        return [
            {"name": s.name, "snapshot_id": s.snapshot_id, "manifest_digest": s.manifest_digest}
            for s in self.sources
        ]


def resolve(specs: list[dict[str, Any]], *, allow_dirty: bool = False) -> Snapshot:
    """Resolve all sources and compute the combined snapshot identity."""
    sources = tuple(sorted(
        (resolve_source(spec, allow_dirty=allow_dirty) for spec in specs),
        key=lambda s: s.name,
    ))
    combined_core = {
        "schema_version": config.SNAPSHOT_SCHEMA_VERSION,
        "sources": [s.manifest for s in sources],
    }
    snapshot_id = short(sha256_json(combined_core), prefix="snap_")
    manifest = {**combined_core, "snapshot_id": snapshot_id}
    return Snapshot(snapshot_id=snapshot_id, sources=sources, manifest=manifest)


def materialize(snapshot: Snapshot) -> str:
    """Stream every admitted blob out of git into the snapshot directory.

    Idempotent and content-addressed: the target dir is named by snapshot_id, so
    a completed materialization is reused. Files land at
    ``<snapshot>/<source>/<repo-relative-path>``.
    """
    root = store.snapshot_path(snapshot.snapshot_id)
    done_marker = root / ".materialized"
    if done_marker.exists():
        return str(root)
    for src in snapshot.sources:
        for entry in src.manifest["files"]:
            rel = entry["path"]
            dest = root / src.name / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            blob = subprocess.run(
                ["git", "-C", src.repo, "cat-file", "blob", entry["blob"]],
                check=True,
                capture_output=True,
            ).stdout
            dest.write_bytes(blob)
    store.write_json(root / "snapshot-manifest.json", snapshot.manifest)
    done_marker.write_text("ok\n", encoding="utf-8")
    return str(root)
