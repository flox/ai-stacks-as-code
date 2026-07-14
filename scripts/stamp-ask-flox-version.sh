#!/usr/bin/env bash
# Stamp the ask-flox package version as <base>-g<short-commit>, written to the
# committed .flox/pkgs/ask-flox/VERSION that default.nix reads.
#
# Nix can't reliably read git state during a clean-clone publish build (packed
# refs), so the version is a committed file. Run this, then commit + push before
# publishing:
#
#   scripts/stamp-ask-flox-version.sh            # base defaults to 0.1.0
#   scripts/stamp-ask-flox-version.sh 0.2.0      # bump the base
#   git commit -am "Stamp ask-flox version" && git push
#   flox publish ask-flox
#
# The embedded commit is HEAD at stamp time (i.e. the last content commit before
# the stamp commit itself) — a stable pointer to what the release was built from.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
base="${1:-0.1.0}"
sha="$(git -C "$ROOT" rev-parse --short HEAD)"
printf '%s-g%s\n' "$base" "$sha" > "$ROOT/.flox/pkgs/ask-flox/VERSION"
echo "ask-flox VERSION -> $(cat "$ROOT/.flox/pkgs/ask-flox/VERSION")"
