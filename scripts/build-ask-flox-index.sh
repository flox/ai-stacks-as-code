#!/usr/bin/env bash
# Ask-Flox demo: build the retrieval index from the Flox docs/blog corpus.
#
# One command takes the raw corpus/ (Flox docs + blog posts, mostly .mdx) all
# the way to a queryable ChromaDB index:
#
#   corpus/  --ingest-->  work/chunks.jsonl  --index-->  Chroma (ai_brief)  --verify-->
#
# Run inside the ai-brief Flox environment:
#
#   flox activate -- scripts/build-ask-flox-index.sh          # default corpus dir
#   flox activate -- env CORPUS_DIR=path/to/docs scripts/build-ask-flox-index.sh
#   flox activate -- scripts/build-ask-flox-index.sh --skip-doctor
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

command -v just >/dev/null 2>&1 || {
  echo "✗ 'just' not on PATH — run this inside the env: flox activate -- $0" >&2
  exit 1
}

# --- config ------------------------------------------------------------------
CORPUS_DIR="${CORPUS_DIR:-corpus}"
COLLECTION="${COLLECTION:-ai_brief}"
SKIP_DOCTOR=0
for arg in "$@"; do
  case "$arg" in
    --skip-doctor) SKIP_DOCTOR=1 ;;
    -h|--help) grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# --- styled output: plain ANSI, disabled on non-TTY or NO_COLOR --------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_BLUE=$'\033[38;5;39m'; C_GREEN=$'\033[38;5;42m'; C_RED=$'\033[38;5;196m'; C_OFF=$'\033[0m'
else
  C_BLUE=; C_GREEN=; C_RED=; C_OFF=
fi
say()  { printf '%s▸ %s%s\n' "$C_BLUE"  "$*" "$C_OFF"; }
ok()   { printf '%s✓ %s%s\n' "$C_GREEN" "$*" "$C_OFF"; }
die()  { printf '%s✗ %s%s\n' "$C_RED"   "$*" "$C_OFF" >&2; exit 1; }

# File types ingest.py knows how to parse (keep in sync with its
# SUPPORTED_EXTENSIONS). The preflight count uses the same set so it matches the
# "wrote N document(s)" ingest reports, regardless of what's dropped in corpus/.
INGEST_EXTS=(mdx md markdown pdf txt text note notes srt vtt jsonl csv html htm)

# --- steps -------------------------------------------------------------------
check_corpus() {
  [ -d "$ROOT/$CORPUS_DIR" ] || die "corpus dir not found: $CORPUS_DIR (set CORPUS_DIR=...)"
  local find_args=() ext
  for ext in "${INGEST_EXTS[@]}"; do find_args+=(-o -iname "*.$ext"); done
  local n
  n="$(find "$ROOT/$CORPUS_DIR" -type f \( "${find_args[@]:1}" \) 2>/dev/null | wc -l)"
  [ "$n" -gt 0 ] || die "no ingestible files under $CORPUS_DIR (looked for: ${INGEST_EXTS[*]})"
  ok "corpus: $n ingestible file(s) under $CORPUS_DIR/"
}

preflight_doctor() {
  if [ "$SKIP_DOCTOR" -eq 1 ]; then say "skipping readiness preflight (--skip-doctor)"; return; fi
  say "readiness preflight (ai-doctor)"
  just ai-doctor || die "environment is NOT ready — fix the FAILs above before indexing"
  ok "environment ready"
}

run_ingest() {
  say "ingest: $CORPUS_DIR/ → work/{documents,chunks}.jsonl"
  just ingest "$CORPUS_DIR"
  ok "ingest complete"
}

run_index() {
  say "index: embedding chunks → Chroma collection '$COLLECTION'"
  just index
  ok "index complete"
}

verify_index() {
  say "verify: querying the live index"
  "${AI_BRIEF_PYTHON:-python3.13}" - "$COLLECTION" <<'PY'
import os, sys
import chromadb
collection = sys.argv[1]
client = chromadb.PersistentClient(path=os.path.join(os.environ["WORK_DIR"], "index"))
count = client.get_collection(collection).count()
print(f"  collection '{collection}' holds {count} embedded chunk(s)")
if count < 1:
    sys.exit("  index is empty — nothing was embedded")
PY
  ok "index is live and queryable"
}

main() {
  say "Ask-Flox index build"
  check_corpus
  preflight_doctor
  run_ingest
  run_index
  verify_index
  ok "done — retrieval index is ready. Try:  flox activate -- just brief --query \"how does layering work?\""
}

main
