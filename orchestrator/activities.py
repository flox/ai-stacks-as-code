"""Temporal activities — all side-effecting work lives here (§3).

Workflow code must stay deterministic and replay-safe, so every impure step —
git access, filesystem mutation, subprocess pipeline stages, Chroma operations,
Temporal client calls, clock reads — is an Activity. Activities exchange compact
dicts/primitives with the workflow; large payloads (chunks, embeddings, the
index) stay on disk in the store and are referenced by id/digest, never carried
through Temporal history (§3 event-history budget).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from temporalio import activity

from . import candidate, config, evaluate as evaluate_mod, snapshot as snapshot_mod, store


# --- helpers ----------------------------------------------------------------

def _jsonl_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def _pipeline_code_revision() -> str:
    """Hash the deterministic transform's source so a code change => new candidate."""
    from .canonical import sha256_json, short

    parts: dict[str, str] = {}
    pdir = config.pipeline_dir()
    for name in config.PIPELINE_CODE_FILES:
        p = pdir / name
        parts[name] = sha256_json(p.read_text(encoding="utf-8")) if p.exists() else ""
    return short(sha256_json(parts), prefix="code_")


async def _run_stage(script: str, argv: list[str], work: Path, content_root: str) -> str:
    """Run a pipeline stage as a subprocess while heartbeating.

    Heartbeating (§7) lets Temporal detect a worker crash within the activity's
    heartbeat_timeout instead of waiting out the long start-to-close timeout, so a
    killed build is rescheduled and resumes promptly on the next worker.
    """
    env = dict(os.environ)
    env["WORK_DIR"] = str(work)
    env["SOURCES_DIR"] = content_root
    env["EMBED_ENGINE"] = "onnx"       # deterministic, torch-free embedder
    env["AI_BACKEND"] = "cpu"
    script_path = config.pipeline_dir() / script

    proc = await asyncio.create_subprocess_exec(
        config.python_bin(), str(script_path), *argv,
        cwd=content_root, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )

    async def _pump() -> None:
        while True:
            await asyncio.sleep(5)
            try:
                activity.heartbeat()
            except Exception:  # noqa: BLE001 - heartbeat outside a real activity ctx (tests)
                pass

    heart = asyncio.ensure_future(_pump())
    try:
        stdout, stderr = await proc.communicate()
    finally:
        heart.cancel()
    if proc.returncode != 0:
        raise RuntimeError(f"{script} failed (exit {proc.returncode}): {stderr.decode()[:2000].strip()}")
    return stdout.decode()


# --- activities -------------------------------------------------------------

@activity.defn
async def resolve_snapshot_activity(request: dict[str, Any]) -> dict[str, Any]:
    """Resolve both content repos to pinned commits and materialize the snapshot."""
    specs = config.default_source_specs()
    refs = {"docs": request.get("docs_ref", "HEAD"), "blog": request.get("blog_ref", "HEAD")}
    for spec in specs:
        spec["ref"] = refs.get(spec["name"], "HEAD")
    snap = snapshot_mod.resolve(specs, allow_dirty=bool(request.get("allow_dirty", False)))
    content_root = snapshot_mod.materialize(snap)
    return {
        "snapshot_id": snap.snapshot_id,
        "manifest": snap.manifest,
        "content_root": content_root,
        "sources": snap.source_specs_for_candidate(),
        "commits": {s.name: s.commit for s in snap.sources},
    }


@activity.defn
async def compute_candidate_activity(request: dict[str, Any]) -> dict[str, Any]:
    """Derive the candidate id + full provenance from the snapshot + build spec."""
    snap = request["snapshot"]
    spec = candidate.build_spec(
        sources=snap["sources"],
        pipeline_code_revision=_pipeline_code_revision(),
    )
    cid = candidate.candidate_id(spec)
    prov = candidate.provenance(
        spec,
        excluded={
            "source_commits": snap.get("commits", {}),
            "snapshot_id": snap["snapshot_id"],
            "backend": "cpu",
            "note": "timestamps/run-ids/backend recorded here, excluded from identity",
        },
    )
    return {"candidate_id": cid, "spec": spec, "provenance": prov}


@activity.defn
async def ingest_activity(request: dict[str, Any]) -> dict[str, Any]:
    """Parse+chunk the materialized snapshot into deterministic JSONL."""
    cid = request["candidate_id"]
    work = store.work_dir(cid)
    work.mkdir(parents=True, exist_ok=True)
    out = await _run_stage("ingest.py", [request["content_root"]], work, request["content_root"])
    return {
        "documents": _jsonl_count(work / "documents.jsonl"),
        "chunks": _jsonl_count(work / "chunks.jsonl"),
        "stdout": out.strip()[-500:],
    }


@activity.defn
async def index_activity(request: dict[str, Any]) -> dict[str, Any]:
    """Embed chunks (ONNX) and build the Chroma index for this candidate."""
    cid = request["candidate_id"]
    work = store.work_dir(cid)
    out = await _run_stage("index.py", [], work, request["content_root"])
    manifest = store.read_json(work / "index-manifest.json", default={})
    return {
        "chunk_count": manifest.get("chunk_count"),
        "engine": manifest.get("embedding_engine"),
        "dimension": manifest.get("dimension"),
        "stdout": out.strip()[-500:],
    }


@activity.defn
async def package_activity(request: dict[str, Any]) -> dict[str, Any]:
    """Package the index into an immutable, content-addressed artifact + provenance."""
    cid = request["candidate_id"]
    work = store.work_dir(cid)
    index_dir = work / "index"
    manifest_path = work / "index-manifest.json"
    if not index_dir.exists():
        raise RuntimeError(f"no index to package for {cid} at {index_dir}")
    digest, path = store.package(index_dir, [manifest_path])

    provenance = dict(request.get("provenance", {}))
    provenance["artifact_digest"] = digest
    provenance["snapshot"] = request.get("snapshot")
    store.write_json(store.provenance_path(cid), provenance)
    return {"artifact_digest": digest, "artifact_path": str(path)}


@activity.defn
async def evaluate_activity(request: dict[str, Any]) -> dict[str, Any]:
    """Deterministic gate over the built index (§11)."""
    cid = request["candidate_id"]
    work = store.work_dir(cid)
    report = evaluate_mod.evaluate(
        index_dir=work / "index",
        manifest_path=work / "index-manifest.json",
        snapshot_manifest=request["snapshot"],
    )
    return report.to_dict()


@activity.defn
async def is_published_activity(candidate_id: str) -> dict[str, Any] | None:
    return store.is_published(candidate_id)


@activity.defn
async def promote_activity(request: dict[str, Any]) -> dict[str, Any]:
    """Atomically advance `current` to a published artifact (single-writer).

    Called only by the authority workflow after a granted authorization, so this
    is the one place the `current` pointer moves. Idempotent: rewriting the same
    entry is harmless, and the published record anchors publish-retry.
    """
    entry = {
        "generation": request["generation"],
        "candidate_id": request["candidate_id"],
        "artifact_digest": request["artifact_digest"],
        "version": request["version"],
        "snapshot_id": request.get("snapshot_id"),
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    # §9 step 9 — verify a consumer can actually open the published artifact.
    verify = _verify_artifact_opens(request["artifact_digest"])
    entry["verified"] = verify
    store.record_publication(entry)
    store.advance_current(entry)
    return entry


def _verify_artifact_opens(artifact_digest: str) -> bool:
    import tempfile

    try:
        import chromadb  # type: ignore
    except Exception:  # noqa: BLE001
        return False
    with tempfile.TemporaryDirectory() as tmp:
        dest = store.unpack(artifact_digest, Path(tmp))
        manifest = store.read_json(dest / "index-manifest.json", default={})
        client = chromadb.PersistentClient(path=str(dest / "index"))
        return client.get_collection(str(manifest.get("collection", "ai_brief"))).count() > 0


# --- authority client activities --------------------------------------------
# These act as a Temporal *client* against the singleton authority workflow.
# Kept out of workflow code (which can only signal external workflows); the
# validated Update result (grant/deny) comes back synchronously here.

async def _authority_handle():
    from temporalio.client import Client
    from temporalio.common import WorkflowIDConflictPolicy

    cfg = config.temporal_config()
    client = await Client.connect(cfg.address, namespace=cfg.namespace)
    return await client.start_workflow(
        "PublicationAuthorityWorkflow",
        id=config.AUTHORITY_WORKFLOW_ID,
        task_queue=cfg.task_queue,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )


@activity.defn
async def register_generation_activity(candidate_id: str) -> int:
    handle = await _authority_handle()
    return await handle.execute_update("register", candidate_id)


@activity.defn
async def request_authorization_activity(request: dict[str, Any]) -> dict[str, Any]:
    handle = await _authority_handle()
    return await handle.execute_update("authorize", request)
