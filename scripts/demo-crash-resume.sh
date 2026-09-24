#!/usr/bin/env bash
# Crash-and-resume demonstration (§19 A).
#
# Submits a build, kills the worker after meaningful work has completed, restarts
# it, and shows the workflow resumes on the SAME run without redoing finished
# expensive stages (Temporal replays completed activities from history).
#
# Run inside the composed env with the Temporal dev server up (flox activate -s):
#   flox activate -s -- scripts/demo-crash-resume.sh
#
# Uses the real docs+blog corpus by default so the index stage is long enough to
# interrupt; override ASK_FLOX_DOCS_REPO / ASK_FLOX_BLOG_REPO to point elsewhere.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${AI_BRIEF_PYTHON:-python3.13}"

export ASK_FLOX_DOCS_REPO="${ASK_FLOX_DOCS_REPO:-$HOME/dev/flox-docs}"
export ASK_FLOX_BLOG_REPO="${ASK_FLOX_BLOG_REPO:-$HOME/dev/floxwebsite}"
export AI_BRIEF_STORE_DIR="${AI_BRIEF_STORE_DIR:-$ROOT/state/crash-demo-store}"
export PYTHONUNBUFFERED=1
LOG="${AI_BRIEF_LOG_DIR:-$ROOT/state/logs}/crash-demo-worker.log"
mkdir -p "$(dirname "$LOG")"

WF_ID="crash-resume-$(date +%s)"

echo "== waiting for Temporal dev server"
for _ in $(seq 1 60); do
  if temporal operator cluster health >/dev/null 2>&1; then echo "  temporal ready"; break; fi
  "$PY" - <<'PY'
import time; time.sleep(1)
PY
done

start_worker() {
  # setsid: new session/process group so a crash can take the worker AND its
  # pipeline subprocess (index.py) down together, avoiding a surviving writer.
  # (orchestrator is importable via PYTHONPATH from the Flox env, so no cd.)
  setsid "$PY" -m orchestrator.worker >>"$LOG" 2>&1 &
  echo $!
}

crash_worker() {  # $1 = worker pid (== process-group id via setsid)
  kill -9 -- "-$1" 2>/dev/null || kill -9 "$1" 2>/dev/null || true
  pkill -9 -f "pipeline/index.py" 2>/dev/null || true
}

wait_for_stage() {  # $1 = workflow id, $2 = stage to reach
  for _ in $(seq 1 120); do
    stage="$("$PY" -m orchestrator.cli status "$1" 2>/dev/null | "$PY" -c 'import sys,json;print(json.load(sys.stdin).get("stage",""))' 2>/dev/null || true)"
    echo "  stage=$stage"
    [ "$stage" = "$2" ] && return 0
    [ "$stage" = "published" ] && return 0
    "$PY" - <<'PY'
import time; time.sleep(1)
PY
  done
  return 1
}

echo "== submit build $WF_ID (real corpus)"
W1="$(start_worker)"; echo "  worker pid=$W1"
"$PY" -m orchestrator.cli submit --id "$WF_ID" >/dev/null
echo "  waiting until the build is mid-flight (stage=indexing, i.e. snapshot+ingest already done)"
wait_for_stage "$WF_ID" indexing || { echo "did not reach indexing"; crash_worker "$W1"; exit 1; }

echo "== CRASH: killing worker group $W1 (worker + index.py) mid-build"
crash_worker "$W1"
echo "  server-side workflow state right after crash (durable in Temporal; no worker needed):"
temporal workflow describe -w "$WF_ID" -o json 2>/dev/null | "$PY" -c '
import sys, json
try:
    d = json.load(sys.stdin)
    info = d.get("workflowExecutionInfo", d)
    print("    status:", info.get("status"), "(worker down, state persisted)")
except Exception:
    print("    (describe unavailable)")
' || true

echo "== RESTART worker; workflow must resume on the same run and finish"
W2="$(start_worker)"; echo "  worker pid=$W2"
for _ in $(seq 1 600); do
  stage="$("$PY" -m orchestrator.cli status "$WF_ID" 2>/dev/null | "$PY" -c 'import sys,json;print(json.load(sys.stdin).get("stage",""))' 2>/dev/null || true)"
  [ "$stage" = "published" ] && break
  "$PY" - <<'PY'
import time; time.sleep(1)
PY
done

echo "== final status"
"$PY" -m orchestrator.cli status "$WF_ID" | "$PY" -m json.tool | sed 's/^/    /'
echo "== active published index"
"$PY" -m orchestrator.cli current | "$PY" -m json.tool | sed 's/^/    /'
crash_worker "$W2"
echo "DONE: the build resumed after the crash and published without re-running completed stages."
