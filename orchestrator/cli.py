"""Small control-surface CLI for the ask-flox pipeline.

    python -m orchestrator.cli submit [--docs-ref REF] [--blog-ref REF] [--allow-dirty]
    python -m orchestrator.cli status <workflow-id>
    python -m orchestrator.cli authority          # publication-authority state
    python -m orchestrator.cli current            # active published index + provenance

Favor this one coherent surface over many one-off scripts (§20).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from temporalio.client import Client

from . import config, store


async def _client() -> Client:
    cfg = config.temporal_config()
    return await Client.connect(cfg.address, namespace=cfg.namespace)


def _build_id(docs_ref: str, blog_ref: str) -> str:
    # Client-side id (not workflow code): deterministic-ish + unique enough.
    import time

    return f"ask-flox-build-{int(time.time() * 1000)}"


async def cmd_submit(args: argparse.Namespace) -> int:
    cfg = config.temporal_config()
    client = await _client()
    wf_id = args.id or _build_id(args.docs_ref, args.blog_ref)
    handle = await client.start_workflow(
        "BuildWorkflow",
        {
            "docs_ref": args.docs_ref,
            "blog_ref": args.blog_ref,
            "allow_dirty": args.allow_dirty,
            "base_version": args.base_version,
            "require_review": args.require_review,
        },
        id=wf_id,
        task_queue=cfg.task_queue,
    )
    print(json.dumps({"workflow_id": handle.id, "run_id": handle.result_run_id}, indent=2))
    if args.wait:
        result = await handle.result()
        print(json.dumps(result, indent=2))
    return 0


async def cmd_status(args: argparse.Namespace) -> int:
    client = await _client()
    handle = client.get_workflow_handle(args.workflow_id)
    print(json.dumps(await handle.query("status"), indent=2))
    return 0


async def cmd_authority(args: argparse.Namespace) -> int:
    client = await _client()
    handle = client.get_workflow_handle(config.AUTHORITY_WORKFLOW_ID)
    try:
        print(json.dumps(await handle.query("status"), indent=2))
    except Exception as exc:  # noqa: BLE001
        print(f"authority not running yet: {exc}", file=sys.stderr)
        return 1
    return 0


async def cmd_reviews(args: argparse.Namespace) -> int:
    client = await _client()
    found = 0
    async for wf in client.list_workflows(
        "WorkflowType = 'BuildWorkflow' AND ExecutionStatus = 'Running'"
    ):
        handle = client.get_workflow_handle(wf.id)
        try:
            status = await handle.query("status")
        except Exception:  # noqa: BLE001 - workflow may have moved on / not yet queryable
            continue
        if status.get("stage") != "awaiting_review":
            continue
        found += 1
        rev = status.get("review") or {}
        print(json.dumps({
            "workflow_id": wf.id,
            "review_request_id": rev.get("review_request_id"),
            "candidate_id": rev.get("candidate_id"),
            "generation": rev.get("generation"),
            "reason": rev.get("reason"),
            "allowed_decisions": rev.get("allowed_decisions"),
        }, indent=2))
    if not found:
        print("no builds awaiting review")
    return 0


async def cmd_decide(args: argparse.Namespace) -> int:
    client = await _client()
    handle = client.get_workflow_handle(args.workflow_id)
    rev = await handle.query("review")
    if not rev:
        print("no open review for that workflow", file=sys.stderr)
        return 1
    payload = {
        "review_request_id": rev["review_request_id"],
        "candidate_id": rev["candidate_id"],
        "decision": args.decision,
        "reviewer": args.reviewer,
        "note": args.note,
    }
    try:
        result = await handle.execute_update("submit_review", payload)
    except Exception as exc:  # noqa: BLE001 - validator rejects stale/invalid decisions
        print(f"decision rejected: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


async def cmd_current(args: argparse.Namespace) -> int:
    current = store.read_current()
    if not current:
        print("no published index yet")
        return 0
    out = dict(current)
    prov = store.read_json(store.provenance_path(current["candidate_id"]), default=None)
    out["provenance"] = prov
    print(json.dumps(out, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orchestrator", description="ask-flox pipeline control")
    sub = parser.add_subparsers(dest="command", required=True)

    p_submit = sub.add_parser("submit", help="start a build from source refs")
    p_submit.add_argument("--docs-ref", default="HEAD")
    p_submit.add_argument("--blog-ref", default="HEAD")
    p_submit.add_argument("--base-version", default="0.1.0")
    p_submit.add_argument("--allow-dirty", action="store_true")
    p_submit.add_argument("--require-review", action="store_true", help="gate publish on human review")
    p_submit.add_argument("--id", default=None, help="explicit workflow id")
    p_submit.add_argument("--wait", action="store_true", help="block for the result")
    p_submit.set_defaults(fn=cmd_submit)

    p_status = sub.add_parser("status", help="query a build workflow's status")
    p_status.add_argument("workflow_id")
    p_status.set_defaults(fn=cmd_status)

    p_auth = sub.add_parser("authority", help="query the publication authority state")
    p_auth.set_defaults(fn=cmd_authority)

    p_reviews = sub.add_parser("reviews", help="list builds awaiting human review")
    p_reviews.set_defaults(fn=cmd_reviews)

    p_decide = sub.add_parser("decide", help="submit a review decision for a build")
    p_decide.add_argument("workflow_id")
    p_decide.add_argument("decision", choices=["approve", "reject"])
    p_decide.add_argument("--reviewer", default="operator")
    p_decide.add_argument("--note", default="")
    p_decide.set_defaults(fn=cmd_decide)

    p_current = sub.add_parser("current", help="show the active published index")
    p_current.set_defaults(fn=cmd_current)

    args = parser.parse_args(argv)
    return asyncio.run(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
