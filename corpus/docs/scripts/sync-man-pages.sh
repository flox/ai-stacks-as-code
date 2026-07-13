#!/usr/bin/env bash
# sync-man-pages.sh — sync man pages from flox/flox (source of truth) to flox/docs
#
# Converts the pandoc man-page sources in <flox-repo>/cli/flox/doc/*.md into
# Mintlify .mdx pages in <docs-man-dir>, expanding shared ./include snippets.
#
# Usage:
#   ./sync-man-pages.sh <flox-repo-path> <docs-man-dir>
# Example:
#   ./sync-man-pages.sh ~/work/flox ~/work/docs/man
#
# Requires: pandoc >= 2.12 (e.g. `flox install pandoc`)

set -euo pipefail

sync_man_pages() {
  local flox_src_dir="${1?usage: sync-man-pages.sh <flox-repo-path> <docs-man-dir>}"
  local docs_man_pages_dir="${2?usage: sync-man-pages.sh <flox-repo-path> <docs-man-dir>}"

  local src_doc_dir="$flox_src_dir/cli/flox/doc"
  local lua_filter="$flox_src_dir/pkgs/flox-manpages/pandoc-filters/include-files.lua"

  [ -d "$src_doc_dir" ] || { echo "error: man page source dir not found: $src_doc_dir" >&2; return 1; }
  [ -f "$lua_filter" ] || { echo "error: pandoc include filter not found: $lua_filter" >&2; return 1; }
  command -v pandoc > /dev/null || { echo "error: pandoc not found on PATH" >&2; return 1; }

  # Clean the output dir so pages deleted upstream don't linger
  mkdir -p "$docs_man_pages_dir"
  rm -f "$docs_man_pages_dir"/*.mdx

  local page name title out
  for page in "$src_doc_dir"/*.md; do
    name="$(basename "$page" .md)"
    out="$docs_man_pages_dir/$name.mdx"

    # Page title: 'flox-services-start' -> 'flox services start';
    # file-format pages (manifest.toml, nix-builds.toml) keep their literal name
    case "$name" in
      *.toml) title="$name" ;;
      *)      title="${name//-/ }" ;;
    esac

    {
      printf -- '---\ntitle: "%s"\n---\n\n' "$title"
      # - include-files.lua expands the shared ./include/*.md snippets
      # - --shift-heading-level-by=1 demotes '# NAME' -> '## NAME'
      #   (the page title comes from frontmatter in Mintlify)
      # - --strip-comments drops source HTML comments (maintainer notes that
      #   live in cli/flox/doc/*.md); MDX cannot parse them
      ( cd "$src_doc_dir" &&
          pandoc --from markdown --to gfm \
            --lua-filter "$lua_filter" \
            --shift-heading-level-by=1 \
            --strip-comments \
            "./$name.md" ) |
        # Rewrite cross-references: (./flox-push.md) -> (/man/flox-push)
        sed -E -e 's|\(\./([A-Za-z0-9._-]+)\.md\)|(/man/\1)|g' \
          -e 's|\[`([A-Za-z0-9._-]+)\([0-9]\)`\]|[`\1`]|g' \
          -e 's|^``` ([A-Za-z0-9_+-]+)$|```\1|' |
        # Drop pandoc's '<!-- -->' block separators (MDX cannot parse HTML
        # comments) along with the blank line that follows each one
        awk '{
          if ($0 == "<!-- -->") { skip_blank = 1; next }
          if (skip_blank && $0 == "") { skip_blank = 0; next }
          skip_blank = 0; print
        }'
    } > "$out"

    echo "  $name.md -> $out"
  done

  # Validate the generated pages. `mint broken-links` is the real check (it
  # parses every MDX file and resolves links), but it needs Node LTS plus
  # network and runs against the whole docs site via docs.json. So run it only
  # when both `mint` and a sibling docs.json are present (e.g. a real sync into
  # the docs repo), and skip it for scratch output dirs. CI remains the hard
  # gate either way.
  local docs_root
  docs_root="$(cd "$docs_man_pages_dir/.." && pwd)"
  if command -v mint > /dev/null && [ -f "$docs_root/docs.json" ]; then
    echo
    echo "validating with 'mint broken-links'..."
    ( cd "$docs_root" && mint broken-links ) \
      || echo "warning: 'mint broken-links' reported problems (see above)." >&2
  else
    echo
    echo "note: skipping 'mint broken-links' (mint or docs.json not found here)." >&2
    echo "Run 'mint broken-links' from the docs repo root to validate rendering." >&2
  fi

  echo
  echo "synced $(find "$docs_man_pages_dir" -name '*.mdx' | wc -l | tr -d ' ') pages to $docs_man_pages_dir"
}

sync_man_pages "$@"
