# Session priming — building the "Ask Flox" retrieval layer

**You (Claude) are picking up an in-progress project.** Read this fully before acting.
It captures the current, verified state of the `ai-brief` RAG stack and the next
phase we're building. The human is `stephen.swoyer@gmail.com`, working style
noted at the end.

---

## Mission (this phase)

Turn the existing document-RAG stack into a grounded **"Ask Flox anything"**
capability: index the **Flox documentation + blog posts** and expose
**retrieval as a tool** so an AI agent (Claude Code) can pull authoritative,
cited Flox context on demand instead of hallucinating Flox specifics.

- **The human chunks the Flox docs + populates ChromaDB** (they're doing this
  the day after this doc was written — 2026-07-13). Assume the corpus/index may
  already exist when you start; confirm before rebuilding.
- **The deliverable that matters is the *retrieval-as-a-tool* surface**, not a
  batch report. Expose "retrieve relevant, cited chunks for this question" as an
  **MCP server/tool** (and/or a plain HTTP endpoint).
- Generation is decoupled and optional (see below). Don't over-build.

---

## What already exists and is VERIFIED GREEN

### Repo / environment
- Repo root: `/home/daedalus/dev/ai-stacks-as-code` — a git repo.
- The **top-level composing env is named `ai-brief`** (its `.flox/` is at the repo
  root). Activate it with `flox activate` from the repo root.
- It **composes four component envs** under `envs/` via `[include]`:
  - `ai-base` — Python **3.13.13** (`python3`, pinned) + `uv`, `git`, `jq`, `ripgrep`, `just`.
  - `ai-ingest` — parser CLIs (`poppler-utils`, `mupdf`, `pandoc`, `html2text`, `yt-dlp`, `dos2unix`) **plus** Python parsing/chunking libs in pkg-group `ingest-py`: `pymupdf`, `pdfplumber` (pinned `0.11.9`), `markdown`, `markdown-it-py`, `beautifulsoup4`, `lxml`, `readability-lxml`, `srt`, `unstructured`, `ftfy`, `langchain-text-splitters`, `tiktoken`, `semchunk`.
  - `ai-embeddings` — `torch` (flox-cuda build on Linux w/ runtime CPU fallback, group `cuda`; stock `python313Packages.torch` on `aarch64-darwin` for MPS), `sentence-transformers`, `chromadb`, `numpy` (grouped).
  - `ai-notebook` — JupyterLab stack, **now on Python 3.13** (was 3.12), runs as a Flox service.
- Systems: `x86_64-linux`, `aarch64-linux`, `aarch64-darwin`.

### The pipeline (implemented, tested end-to-end)
Implemented in `pipeline/*.py` (a reasoning model wrote it against `pipeline/SPEC.md`;
integrated and validated). Local, offline, deterministic, extractive RAG:
- `ingest.py` — parse+normalize sources → `work/documents.jsonl`, `work/chunks.jsonl`
  (content-hashed chunk IDs `chunk_<sha>`; chunks via `semchunk` by default).
- `index.py` — embed chunks (`sentence-transformers/all-MiniLM-L6-v2`) → **ChromaDB
  `PersistentClient(path=work/index/)`, collection `ai_brief`, cosine**; idempotent
  via `chunk_hashes` in `work/index-manifest.json` (re-embeds only changed chunks).
- `brief.py` — retrieve → theme → **extractive** summary → `reports/brief.{md,json}`;
  every claim cites a real chunk id.
- `eval.py` (command `ai-eval`) — runs the pipeline on `evals/fixtures/`, checks shape,
  **citation resolution**, golden match vs `evals/expected/brief.json`, stale-index &
  bad-citation rejection, byte-identical rerun idempotency. **Strict path (semchunk +
  sentence-transformers + chroma) passes 18/18** with `embedding_engine=sentence-transformers`,
  `index_store=chroma`.
- `doctor.py` (command `ai-doctor`) — machine readiness: OS/arch, `AI_BACKEND`, python/uv,
  strict-dep imports (semchunk/sentence-transformers/chromadb/torch), dirs. Currently **READY**.
- `common.py` — shared helpers/schemas.

### Command surface (interactive-shell functions → `just` → `pipeline/*.py`)
`ingest`, `index`, `brief`, `ai-eval`, `notebook`, `ai-doctor`. Defined as thin
wrappers in the `ai-brief` manifest `[profile]`; they route through `justfile` to
the scripts. **Wrappers are interactive-only** (Flox sources `[profile]` into the
interactive shell); for scripting/CI call `just <recipe>` directly, e.g.
`flox activate -- just ai-doctor`. Env vars set by the manifest `[hook]`:
`SOURCES_DIR`, `WORK_DIR`, `REPORTS_DIR`, `PROMPTS_DIR`, `EVALS_DIR`, `NOTEBOOKS_DIR`,
`PIPELINE_DIR`, `AI_BRIEF_PYTHON` (=python3.13), and `AI_BACKEND` (auto: cuda/mps/cpu, overridable).

### ChromaDB usage
Embedded library, **not a server**. `index` writes and `brief` reads via
`PersistentClient` on `work/index/`. Running `index` *is* running Chroma.
(A `chroma` CLI is on PATH if you ever want server mode, but the pipeline uses
PersistentClient.)

---

## Hard-won lessons — DO NOT re-learn these

1. **Catalog-page coherence via pkg-groups.** A Flox group resolves on ONE catalog
   page per system. Cramming the whole heavy Python stack into `toplevel` fails to
   resolve across 3 systems (esp. `aarch64-darwin`). **Fix: give distinct/heavy or
   separate-catalog packages their own `pkg-group`.** `flox-cuda` is a *separate
   catalog* → must be its own group. This is why `ai-embeddings` and `ai-ingest`
   use groups. The `~/dev/llamacpp` env puts *every* package in its own group.
2. **glibc floor.** flox-cuda torch is built against a newer glibc than an older
   base page ships → `import torch` fails with `GLIBC_2.42 not found`. **Fix:** the
   env's glibc must be ≥ what the native wheels need; bumping the base interpreter
   to a newer page (`python3` pinned `3.13.13`) pulled a matching glibc and torch
   imports. Resolve ≠ import — always verify imports, not just locking.
3. **`flox include upgrade -d .`** on the composer after editing ANY component;
   editing a component alone doesn't propagate into the composed env.
4. **`pdfplumber` 0.11.7** pulls a `pandas-stubs` build whose tests fail → pin `0.11.9`.
5. **`webvtt-py` is not in the catalog** — dropped; `.srt` covered by `srt`.
6. `flox edit -f <file>` where `<file>` == current manifest = no-op ("No changes");
   edit from a scratchpad copy, or expect activation to re-lock.

---

## The next phase — decisions already made

- **Retrieval is decoupled from generation.** Embedding model (MiniLM) stays LOCAL
  and powers retrieval; the reasoning/generation model is swappable.
- **No custom/fine-tuned model needed.** RAG = stock model + existing embedder;
  domain knowledge lives in retrieved chunks, refreshed by re-indexing. If retrieval
  ever plateaus, improve the *retrieval* side first (better off-the-shelf embedder,
  or a cross-encoder reranker) — not the generator.
- **Expose retrieval as an MCP tool** ("search/ask Flox docs" → returns cited chunks).
  This is the highest-leverage way to help agents; it turns RAG-*generation* into
  retrieval-augmented *agents*.
- **Two generation paths, both valid:**
  - **Claude Code on upstream Anthropic models (preferred/simplest):** the agent
    runs on hosted Claude and calls the local MCP retrieval tool for grounding. No
    local LLM required. Caveat: retrieved doc chunks travel to Anthropic as context
    (fine for public Flox docs).
  - **Fully local:** the `~/dev/llamacpp` env runs a llama.cpp `llama-server`
    (**OpenAI-compatible**, fronted by a proxy at `127.0.0.1:8081`, api key
    `llamacpp-local`). Point an `ask` bridge at `http://127.0.0.1:8081/v1/...`.
    Use a **general instruct GGUF**, not the coder models the env defaults to.
- **Integrate by LAYERING, not composition.** The inference/agent env is heavy,
  service-based, and optional — activate it on top of `ai-brief` when needed.

### The `~/dev/llamacpp` env (assessed, fits well)
Provides: llama.cpp server (OpenAI-compatible via proxy `:8081`), `flox-labs/vram-optimizer`,
`flox-labs/llamacpp-launchers`/`-proxy`, coding-agent harnesses (`claude-code`, `codex`,
`gemini-cli`, `crush`, `opencode`, `deepseek-tui`), **`flox/flox-mcp-server`**, `huggingface-hub`.
Runs `llama-server` + proxy as Flox services (`flox activate -s`). Systems match ours.
It's *coding-agent oriented* (banner pushes `qwen3-coder`); for docs Q&A pick a general model.

---

## Concrete next steps (when the human is ready)

1. Confirm the corpus/index state (`work/index/`, `work/chunks.jsonl`, `index-manifest.json`).
   **NOTE:** as of this writing the index holds **3 chunks from the eval *fixtures*** (a
   demo run), **not** the Flox docs — don't mistake a small count for "docs loaded."
   Also `work/` is gitignored, so on any fresh clone/machine the index is empty. Rebuild
   from the real corpus: a fetch/sync step to pull Flox docs/blogs into `sources/` is still
   unbuilt (likely `git clone` the docs repo + fetch blog HTML), then `ingest`+`index`.
2. Build an **`ask`/retrieve bridge**: embed the question → ChromaDB top-k → return
   cited chunks (+ optional generated answer). Reuse `common.py`/`brief.py` retrieval code.
3. Wrap that retrieval as an **MCP tool** (see `flox/flox-mcp-server` in the llamacpp env
   for the pattern) so Claude Code can call it.
4. Keep the index **fresh** (re-index on doc changes). Retrieval quality is the bottleneck.
5. Verify empirically (imports, a real query returning correct cited passages), and
   extend `ai-eval`/`ai-doctor` to cover the new surface.

---

## Working style / preferences of the human

- **Extreme brevity in prose/marketing copy** — when asked for descriptions, deliver
  1–3 sentences; cut component-by-component detail unless asked. (This handoff doc is
  the exception — completeness is wanted here.)
- **Honest surfacing over reassurance** — never hide failures; the `ai-doctor`
  "report the glibc FAIL honestly" ethic is the standard. Verify empirically; say what
  actually happened.
- **Build-and-verify** — resolve, activate, import, run; don't trust locking alone.
- Disciplined package-group hygiene; layering vs composition chosen deliberately.

## Fast state-check commands
```bash
cd /home/daedalus/dev/ai-stacks-as-code
flox activate -- just ai-doctor                 # READY?
flox activate -- just ai-eval                    # 18/18, strict path?
flox activate -- python3.13 -c "import chromadb,os; c=chromadb.PersistentClient(path=os.environ['WORK_DIR']+'/index'); print(c.get_collection('ai_brief').count())"
# ^ currently prints 3 (eval FIXTURE chunks, not the Flox docs corpus)
```
