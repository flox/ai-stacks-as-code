#!/usr/bin/env python3.13
"""Incremental-reuse demo (Phase 2, §12).

Three builds against a tiny two-repo corpus, with a server + worker already up
and their env pointing at the same store + demo repos:

  1. first build            -> full work; every chunk embedded (cache misses)
  2. identical re-build      -> candidate-level reuse (ingest/index/package skipped)
  3. one doc added           -> new candidate; index re-embeds ONLY the new chunk,
                                serving the unchanged chunks from the embedding cache
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from temporalio.client import Client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from orchestrator import config, store  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _make_repo(path: Path, files: dict[str, str]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if not (path / ".git").exists():
        _git(path, "init", "-q")
    for rel, text in files.items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    _git(path, "add", "-A")
    if subprocess.run(["git", "-C", str(path), "status", "--porcelain"],
                      capture_output=True, text=True).stdout.strip():
        _git(path, "-c", "user.email=demo@demo", "-c", "user.name=demo", "commit", "-q", "-m", "demo")


async def _build(client: Client, tag: str) -> dict:
    cfg = config.temporal_config()
    handle = await client.start_workflow(
        "BuildWorkflow",
        {"docs_ref": "HEAD", "blog_ref": "HEAD", "allow_dirty": False, "base_version": "0.1.0"},
        id=f"demo-reuse-{tag}", task_queue=cfg.task_queue,
    )
    return await handle.result()


def _show(label: str, r: dict) -> None:
    print(f"  {label}: candidate={r['candidate_id']} reused={r['reused']} "
          f"embed_cache={r.get('embed_cache')} published={r['published']}")


async def main() -> int:
    docs = Path(config._env("ASK_FLOX_DOCS_REPO", ""))
    blog = Path(config._env("ASK_FLOX_BLOG_REPO", ""))
    _make_repo(docs, {
        "layering.mdx": "# Layering\nFlox layering stacks environments at activation; "
                        "later layers win conflicts.\n",
        "manifest.mdx": "# Manifest\nA flox manifest declares packages; install with flox install.\n",
    })
    _make_repo(blog, {"src/posts/hello.mdx": "# Hello\nHow a flox environment works.\n"})

    cfg = config.temporal_config()
    client = await Client.connect(cfg.address, namespace=cfg.namespace)

    print("== build 1: first time (expect reused=False, all cache misses)")
    r1 = await _build(client, "1")
    _show("build1", r1)
    assert r1["reused"] is False and r1["published"] is True
    assert (r1.get("embed_cache") or {}).get("hits") == 0

    print("== build 2: identical inputs (expect candidate-level reuse, no index run)")
    r2 = await _build(client, "2")
    _show("build2", r2)
    assert r2["candidate_id"] == r1["candidate_id"]
    assert r2["reused"] is True and r2["published"] is True

    print("== build 3: add one doc (new candidate; embedding cache serves the unchanged chunks)")
    _make_repo(docs, {"services.mdx": "# Services\nFlox services run processes; start with flox activate -s.\n"})
    r3 = await _build(client, "3")
    _show("build3", r3)
    assert r3["candidate_id"] != r1["candidate_id"]   # inputs changed => new identity
    assert r3["reused"] is False                       # must rebuild
    ec = r3.get("embed_cache") or {}
    assert ec.get("hits", 0) > 0 and ec.get("misses", 0) > 0, ec  # reused old, embedded only new

    print("\nDEMO OK: identical inputs reuse the whole candidate; a changed corpus "
          "re-embeds only the new chunks and serves the rest from cache.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
