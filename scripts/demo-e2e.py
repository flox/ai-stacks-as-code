#!/usr/bin/env python3.13
"""End-to-end demo of the durable ask-flox pipeline (§19 A + B, happy path).

Run inside the composed Flox env, with the Temporal dev server + a worker
running and their env pointing at the SAME store + demo repos:

    export ASK_FLOX_DOCS_REPO=/tmp/ask-flox-demo/docs
    export ASK_FLOX_BLOG_REPO=/tmp/ask-flox-demo/blog
    export AI_BRIEF_STORE_DIR=/tmp/ask-flox-demo/store
    python -m orchestrator.worker &                 # (already started by the runner)
    python scripts/demo-e2e.py

It (1) creates two tiny content repos, (2) submits a BuildWorkflow and waits for
it to publish, then (3) drives a fresh publication authority through the
supersession race and asserts the older candidate can never become current.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from temporalio.client import Client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from orchestrator import config, store  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _make_repo(path: Path, files: dict[str, str]) -> str:
    path.mkdir(parents=True, exist_ok=True)
    if not (path / ".git").exists():
        _git(path, "init", "-q")
    for rel, text in files.items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    _git(path, "add", "-A")
    # Only commit if there's something to commit (idempotent re-runs).
    status = subprocess.run(["git", "-C", str(path), "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    if status.strip():
        _git(path, "-c", "user.email=demo@demo", "-c", "user.name=demo", "commit", "-q", "-m", "demo")
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def setup_repos() -> None:
    docs = Path(config._env("ASK_FLOX_DOCS_REPO", ""))
    blog = Path(config._env("ASK_FLOX_BLOG_REPO", ""))
    _make_repo(docs, {
        "layering.mdx": "# Layering\nFlox environment layering stacks environments at "
                        "activation time; later layers take precedence for conflicts.\n",
        "manifest.mdx": "# Manifest\nA flox environment manifest declares packages to "
                        "install and hooks. Install a package with flox install.\n",
    })
    _make_repo(blog, {
        "src/posts/hello.mdx": "# Hello Flox\nThis post explains how to install a package "
                               "with flox and how a flox environment works.\n",
    })


async def happy_path(client: Client, run_tag: str) -> dict:
    cfg = config.temporal_config()
    handle = await client.start_workflow(
        "BuildWorkflow",
        {"docs_ref": "HEAD", "blog_ref": "HEAD", "allow_dirty": False, "base_version": "0.1.0"},
        id=f"demo-build-{run_tag}",
        task_queue=cfg.task_queue,
    )
    print(f"  submitted build workflow id={handle.id}")
    result = await handle.result()
    print("  build result:", json.dumps({k: result.get(k) for k in
          ("stage", "candidate_id", "generation", "published", "reason", "artifact_digest")}, indent=2))
    assert result["published"] is True, f"expected published, got {result.get('reason')}"
    current = store.read_current()
    assert current and current["candidate_id"] == result["candidate_id"], "current must point at the build"
    print("  current ->", current["candidate_id"], "gen", current["generation"], "verified", current.get("verified"))
    return result


async def supersession(client: Client, artifact_digest: str, run_tag: str) -> None:
    """Drive a fresh authority through the exact §19.13-14 race."""
    cfg = config.temporal_config()
    auth_id = f"{config.AUTHORITY_WORKFLOW_ID}-demo-race-{run_tag}"
    handle = await client.start_workflow(
        "PublicationAuthorityWorkflow", id=auth_id, task_queue=cfg.task_queue,
    )
    gen_a = await handle.execute_update("register", "cand_OLD")
    gen_b = await handle.execute_update("register", "cand_NEW")
    print(f"  registered cand_OLD=gen{gen_a}, cand_NEW=gen{gen_b}")

    dec_old = await handle.execute_update("authorize", {
        "candidate_id": "cand_OLD", "generation": gen_a,
        "artifact_digest": artifact_digest, "version": "0.1.0-old", "snapshot_id": "demo"})
    print("  authorize(cand_OLD) ->", dec_old["granted"], dec_old["reason"])
    assert not dec_old["granted"] and dec_old["reason"] == "superseded"

    dec_new = await handle.execute_update("authorize", {
        "candidate_id": "cand_NEW", "generation": gen_b,
        "artifact_digest": artifact_digest, "version": "0.1.0-new", "snapshot_id": "demo"})
    print("  authorize(cand_NEW) ->", dec_new["granted"], dec_new["reason"])
    assert dec_new["granted"]

    state = await handle.query("status")
    assert state["published_candidate"] == "cand_NEW"
    print("  authority published_candidate =", state["published_candidate"], "(older can never win)")


async def main() -> int:
    import time

    setup_repos()
    run_tag = str(int(time.time()))
    cfg = config.temporal_config()
    client = await Client.connect(cfg.address, namespace=cfg.namespace)

    print("== Part 1: full pipeline (snapshot -> identity -> ingest -> index -> package -> eval -> publish)")
    result = await happy_path(client, run_tag)

    print("== Part 2: supersession race (older candidate finishes last, must not win)")
    await supersession(client, result["artifact_digest"], run_tag)

    print("\nDEMO OK: pipeline published a verified index; a superseded candidate could not become current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
