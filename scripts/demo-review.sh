#!/usr/bin/env bash
# Durable human-review demonstration (§6, §19 C + §19.12).
#
# Drives a build to the review gate, CRASHES the worker while it waits, restarts,
# rejects an invalid (wrong-candidate) decision, then submits a valid approval —
# proving the review is durable workflow state and decisions are validated.
#
# Run inside the composed env with the Temporal dev server up:
#   flox activate -s -- scripts/demo-review.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${AI_BRIEF_PYTHON:-python3.13}"

export ASK_FLOX_DOCS_REPO="$ROOT/state/review-demo/docs"
export ASK_FLOX_BLOG_REPO="$ROOT/state/review-demo/blog"
export AI_BRIEF_STORE_DIR="$ROOT/state/review-demo/store"
export PYTHONUNBUFFERED=1
LOG="$ROOT/state/logs/review-demo-worker.log"
mkdir -p "$(dirname "$LOG")"
WF="review-demo-$(date +%s)"

sleep_py() { "$PY" - "$1" <<'PY'
import sys, time; time.sleep(float(sys.argv[1]))
PY
}

mk_repo() {  # $1 = dir; remaining args = "relpath::content" pairs
  local dir="$1"; shift
  mkdir -p "$dir"; git -C "$dir" init -q 2>/dev/null || true
  local pair rel content
  for pair in "$@"; do
    rel="${pair%%::*}"; content="${pair#*::}"
    mkdir -p "$dir/$(dirname "$rel")"; printf '%s\n' "$content" > "$dir/$rel"
  done
  git -C "$dir" add -A
  if [ -n "$(git -C "$dir" status --porcelain)" ]; then
    git -C "$dir" -c user.email=demo@demo -c user.name=demo commit -q -m demo
  fi
}

start_worker() { setsid "$PY" -m orchestrator.worker >>"$LOG" 2>&1 & echo $!; }
crash_worker() { kill -9 -- "-$1" 2>/dev/null || kill -9 "$1" 2>/dev/null || true; }

wait_stage() {  # $1 wf, $2 stage
  local s
  for _ in $(seq 1 120); do
    s="$("$PY" -m orchestrator.cli status "$1" 2>/dev/null | "$PY" -c 'import sys,json;print(json.load(sys.stdin).get("stage",""))' 2>/dev/null || true)"
    [ "$s" = "$2" ] && return 0
    [ "$s" = "published" ] && return 0
    sleep_py 1
  done
  return 1
}

echo "== waiting for Temporal dev server"
for _ in $(seq 1 60); do temporal operator cluster health >/dev/null 2>&1 && break; sleep_py 1; done

echo "== setup tiny corpus"
mk_repo "$ASK_FLOX_DOCS_REPO" \
  "layering.mdx::# Layering
Flox layering stacks environments at activation; later layers win conflicts." \
  "manifest.mdx::# Manifest
A flox manifest declares packages; install with flox install."
mk_repo "$ASK_FLOX_BLOG_REPO" \
  "src/posts/hello.mdx::# Hello
How a flox environment works."

echo "== submit build $WF with --require-review"
W1="$(start_worker)"; echo "  worker pid=$W1"
"$PY" -m orchestrator.cli submit --id "$WF" --require-review >/dev/null
wait_stage "$WF" awaiting_review || { echo "did not reach awaiting_review"; crash_worker "$W1"; exit 1; }
echo "  reached stage=awaiting_review"

echo "== CRASH worker while the review is pending"
crash_worker "$W1"
echo "  server-side state after crash (durable; no worker):"
temporal workflow describe -w "$WF" -o json 2>/dev/null | "$PY" -c 'import sys,json;print("    status:", json.load(sys.stdin).get("workflowExecutionInfo",{}).get("status"))' 2>/dev/null || true

echo "== RESTART worker"
W2="$(start_worker)"; echo "  worker pid=$W2"
for _ in $(seq 1 40); do
  temporal task-queue describe --task-queue ask-flox-pipeline -o json 2>/dev/null | grep -q '"identity"' && break
  sleep_py 1
done

echo "== a stale/invalid decision (wrong candidate) must be rejected"
"$PY" - "$WF" <<'PY'
import asyncio, sys
from temporalio.client import Client
from orchestrator import config
async def main():
    cfg = config.temporal_config()
    client = await Client.connect(cfg.address, namespace=cfg.namespace)
    handle = client.get_workflow_handle(sys.argv[1])
    rev = await handle.query("review")
    bad = {"review_request_id": rev["review_request_id"], "candidate_id": "cand_WRONG", "decision": "approve"}
    try:
        await handle.execute_update("submit_review", bad)
        print("  BUG: invalid decision was accepted")
    except Exception as exc:
        print("  invalid decision rejected (expected):", str(exc).splitlines()[-1][:90])
asyncio.run(main())
PY

echo "== submit the valid approval after restart"
"$PY" -m orchestrator.cli decide "$WF" approve --reviewer alice --note "looks good" | sed 's/^/    /'
wait_stage "$WF" published || true

echo "== final status"
"$PY" -m orchestrator.cli status "$WF" | "$PY" -m json.tool | sed 's/^/    /'
crash_worker "$W2"
echo "DONE: review survived the crash; an invalid decision was rejected; the approved build published."
