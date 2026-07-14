#!/usr/bin/env bash
# Regenerate the committed Ask-Flox index blob from the current corpus.
#
# The chroma index is non-reproducible, so the ask-flox Nix package pins a
# prebuilt tarball rather than rebuilding it. Run this whenever corpus/ changes,
# then commit the updated blob:
#
#   flox activate -- scripts/refresh-ask-flox-index.sh
#   git add .flox/pkgs/ask-flox/index.tar.gz && git commit
#
# After that, `flox build ask-flox` (and publish) pick up the new index.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v just >/dev/null 2>&1 || {
  echo "run inside the env: flox activate -- $0" >&2; exit 1; }

just ingest corpus
just index

OUT="$ROOT/.flox/pkgs/ask-flox/index.tar.gz"
tar -czf "$OUT" -C "$ROOT/work" index index-manifest.json
echo "wrote $OUT ($(du -h "$OUT" | cut -f1)) — commit it, then: flox build ask-flox"
