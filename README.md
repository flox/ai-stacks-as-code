# ai-brief

An **AI stack as code** — a composed [Flox](https://flox.dev) environment that
turns a folder of raw documents into an evidence-backed brief. It is
reproducible, cross-platform (x86-64 / aarch64 Linux and Apple Silicon), and
adapts its compute backend (CUDA / MPS / CPU) to the machine it runs on.

The demo pipeline is a small local RAG flow: **ingest → index → brief**, with
`ai-eval`, `notebook`, and `ai-doctor` around it.

## Architecture

`ai-brief` is the top-level composing environment. It merges four
single-responsibility component environments at build time:

| Component | Provides |
|---|---|
| `envs/ai-base` | Python 3.13, `uv`, and core tooling (`git`, `jq`, `ripgrep`, `just`) |
| `envs/ai-ingest` | Document parsers — `poppler-utils`, `mupdf`, `pandoc`, `html2text`, `yt-dlp` + Python parsing/chunking libs |
| `envs/ai-embeddings` | `torch`, `sentence-transformers`, and a `chromadb` vector store |
| `envs/ai-notebook` | JupyterLab + the scientific stack, on the same 3.13 interpreter |

Every dependency comes from the Flox catalog and is pinned in the composed
lockfile, so the stack resolves identically on every machine.

## Repository layout

```
.
├── .flox/            # the ai-brief composing environment (activate here)
├── justfile          # routing layer: command -> just recipe -> pipeline script
├── pipeline/         # ingest.py, index.py, brief.py, eval.py, doctor.py
├── envs/             # the four component environments
│   ├── ai-base/  ai-ingest/  ai-embeddings/  ai-notebook/
└── sources/ work/ reports/ prompts/ evals/ notebooks/   # created on activation
```

## Quickstart

```bash
flox activate        # from the repo root; first run realises a large closure
ai-doctor            # confirm the environment is ready on this machine
# drop files into sources/, then:
ingest               # sources/          -> work/documents.jsonl, work/chunks.jsonl
index                # work/chunks.jsonl -> work/index/
brief                # chunks + index    -> reports/brief.md (proof-of-concept only; see note)
mcp                  # serve retrieval as an "Ask Flox" MCP tool for agents
notebook             # open JupyterLab on the same env/paths/backend
```

For the "Ask Flox" demo, build the docs index and use the **MCP tool**, not `brief`:

```bash
flox activate -- scripts/build-ask-flox-index.sh   # corpus/ -> chunks -> index -> verify
# `search_flox_docs` is then available to Claude Code via .mcp.json
```

## Command surface

| Command | Purpose |
|---|---|
| `ingest` | Parse and normalize raw sources into deterministic JSONL chunks |
| `index` | Embed chunks (per `AI_BACKEND`) and build the local vector index |
| `mcp` | Serve retrieval as the `search_flox_docs` **MCP tool** — the "Ask Flox" surface for agents |
| `brief` | Extractive retrieve/cluster/summarize digest — a proof-of-concept, **not** the Q&A surface (see note) |
| `ai-eval` | Lightweight pipeline regression check against fixtures |
| `notebook` | JupyterLab exploration surface |
| `ai-doctor` | Report whether the environment is ready (backend, torch, paths) |

> **Use `mcp` for questions, not `brief`.** `search_flox_docs` returns focused,
> cited passages for a query and lets the agent synthesize the answer — that's the
> intended "Ask Flox" flow. `brief` exists only to *prove the retrieval + index
> work end-to-end*: it summarizes broadly across the whole corpus, so on a pointed
> question its keyword-clustered themes read as off-topic salad. That's expected;
> it's a demonstration artifact, not the product surface.

Commands are thin shell wrappers (defined in the Flox manifest) that call `just`
recipes, which call `pipeline/<stage>.py`. `just` is the routing layer, so an
implementation can move (Python → shell → Rust) without changing the command
surface. The wrappers are interactive-shell functions; for scripting/CI call the
recipe directly, e.g. `flox activate -- just ai-doctor`.

## Backends and configuration

`AI_BACKEND` is auto-detected (`cuda` on Linux+NVIDIA, `mps` on Apple Silicon,
else `cpu`) and overridable, as are all project paths:

```bash
AI_BACKEND=cpu flox activate
SOURCES_DIR=/data/corpus flox activate
```

On Linux the CUDA-enabled torch build is used and falls back to CPU at runtime
when no GPU is present; on Apple Silicon the stock build provides the Metal/MPS
backend.

## Status

The scaffolding is complete and green: the environment composes, resolves across
all three systems, and `ai-doctor` reports `READY`. The pipeline stages
(`ingest`/`index`/`brief`/`ai-eval`) are **stubs** — each fails clearly with an
actionable message until `pipeline/<stage>.py` is implemented.

## Requirements

- [Flox](https://flox.dev) installed.
- For CUDA on Linux, access to the `flox-cuda` catalog (`flox auth login`). CPU
  and MPS need no special access.
