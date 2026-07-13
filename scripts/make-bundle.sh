#!/usr/bin/env bash
# Regenerate the code bundle handed to the implementing model alongside
# pipeline/SPEC.md. Flattens the implementation-relevant files (+ a snapshot of
# the resolved packages) into one self-contained text file — no repo access
# needed. Run from anywhere: scripts/make-bundle.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/pipeline/CODE-BUNDLE.md"

FILES=(
  README.md
  justfile
  .flox/env/manifest.toml
  envs/ai-base/.flox/env/manifest.toml
  envs/ai-ingest/.flox/env/manifest.toml
  envs/ai-embeddings/.flox/env/manifest.toml
  pipeline/doctor.py
  pipeline/ingest.py
  pipeline/index.py
  pipeline/brief.py
  pipeline/eval.py
)

{
  echo "# ai-brief — code bundle"
  echo
  echo "Self-contained context for implementing the pipeline. Read alongside"
  echo "pipeline/SPEC.md (the contract). Files are separated by 'FILE:' markers;"
  echo "content is verbatim. doctor.py is a reference implementation; the other"
  echo "four pipeline/*.py are stubs to replace."
  echo
  echo "================================================================"
  echo "RESOLVED PACKAGES (composed ai-brief env — exact available libs/versions)"
  echo "================================================================"
  flox list -d "$ROOT" 2>/dev/null || echo "(run 'flox list -d .' in the repo to refresh)"
  echo

  for f in "${FILES[@]}"; do
    echo "================================================================"
    echo "FILE: $f"
    echo "================================================================"
    if [ -f "$ROOT/$f" ]; then
      cat "$ROOT/$f"
    else
      echo "(missing: $f)"
    fi
    echo
  done
} > "$OUT"

echo "wrote $OUT ($(wc -l < "$OUT") lines)"
