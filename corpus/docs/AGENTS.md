# Documentation project instructions

> For Mintlify product knowledge (components, configuration, writing standards),
> install the Mintlify skill: `npx skills add https://mintlify.com/docs`

## About this project

- This is a documentation site built on [Mintlify](https://mintlify.com)
- Pages are MDX files with YAML frontmatter
- Configuration lives in `docs.json`
- Run `mint dev` to preview locally
- Run `mint broken-links` to check links

## Terminology

- **Environment** is the core Flox concept. Call it an "environment" — not a "project," "container," "workspace," or "virtualenv."
- **Flox** (capitalized) is the product and company; **`flox`** (lowercase, code-formatted) is the CLI command.
- **FloxHub** is one word with a capital H.
- A package's source is **the catalog** (and the **base catalog**); the package list lives in the **manifest** (`manifest.toml`), pinned by the **lockfile** (`manifest.lock`).
- You **activate** an environment; the running result is an **activation**. Related terms: **package group**, **generation**, **build**, **publish**.

## Style preferences

- Use active voice and second person ("you")
- Keep sentences concise — one idea per sentence
- Use sentence case for headings
- Bold for UI elements: Click **Settings**
- Code formatting for file names, commands, paths, and code references

## Code-block conventions

The copy button on code fences should yield content that pastes-and-runs cleanly into a terminal or editor. Rules:

- **Language tag matches body content.** Use `bash` only when the body is runnable shell. Use `console` for transcripts (prompt-prefixed commands, optionally with output). Use `text` for grammar/syntax notation, error output, or non-runnable illustrative content.
- **No prompts in `bash` fences.** If a fence is `bash`, every non-blank line should be a runnable command: no `$`/`❯` prefixes, no interleaved tool output.
- **`&&`-join multi-command sequences** when the intent is paste-and-run. Single-line `a && b && c` when the joined length stays under ~80 characters; multi-line trailing `&&` otherwise.
- **Do not `&&`-join across a non-terminal `flox activate`.** `flox activate` (without `-c` or `--`) launches an interactive sub-shell; a `&&` chain that crosses it stalls until the user manually exits, then runs the remainder outside the activated environment, often with expected variables unset. If downstream work needs to run inside the env, either split the fence (use `console` with `$` prompts for the interactive sequence) or use one of the inline-command forms: `flox activate -- CMD ARGS` (exec form, single program) or `flox activate -c 'CMD'` (shell-command form).
- **Borderline blocks stay `console`.** When a fence resists mechanical paste-and-run conversion (inline narrative comments, `<placeholder>` notation, mixed command + output), prefer leaving it as `console` over restructuring the surrounding prose. The right fix is upstream with the author.
- **Do not `&&`-chain cleanup or uninstall sequences.** Cleanup is typically a sequence of best-effort no-ops, not a dependency chain. `&&` short-circuits on the first idempotent failure (e.g., service already stopped, file already removed), leaving later steps such as `daemon-reload` unrun. Use newline-separated commands for teardown blocks; reserve `&&` for setup/install where each step genuinely depends on the previous one.

## Man pages are generated — do not edit directly

Everything under `man/` is generated from the man page sources in the
[flox/flox](https://github.com/flox/flox) repo (`cli/flox/doc/*.md`).

- **Do not edit `man/*.mdx` directly** — changes will be overwritten by the
  next sync.
- To fix or update a man page, edit the source in flox/flox
  (`cli/flox/doc/`), then regenerate:

  ```bash
  ./scripts/sync-man-pages.sh <path-to-flox-repo> ./man
  ```

  The script requires `pandoc` (e.g. `flox install pandoc`).
- New pages also need a navigation entry in `docs.json` under the
  "CLI reference" tab.

## Content boundaries

This is a **public repository**. Keep that in mind when adding or editing content:

- **No secrets.** Never commit real API keys, tokens, credentials, or private keys. Use obvious placeholders (`<your-token>`, `you@example.com`) in examples.
- **No internal-only details.** Don't reference internal infrastructure, hostnames, dashboards, runbooks, or admin/staff-only tooling.
- **No customer or NDA-covered information.** Don't name specific customers or include anything under NDA. The `customer/` section documents generally-available enterprise and self-hosting features — not specific accounts.
- **Sanitize screenshots and transcripts.** Redact real org handles, emails, and one-time codes (e.g. device-confirmation codes) before adding an image or terminal capture.
