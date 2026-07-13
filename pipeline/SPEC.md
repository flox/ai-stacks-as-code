# Pipeline specification — `ai-brief`

Implementation contract for the five scripts in `pipeline/`. The Flox
environment, command surface, and routing already exist; these scripts are the
only thing to build. Honor the artifact contracts below and the commands,
composition, and `ai-doctor` keep working unchanged.

## Goals & constraints

- **Offline & keyless by default.** No network calls or API keys required to run
  `ingest → index → brief → ai-eval`. The only acceptable download is the
  embedding model on first `index` (small, cached).
- **Lightweight.** No large/hosted model. Default embedding model
  `sentence-transformers/all-MiniLM-L6-v2` (~90 MB, CPU-fine); override via
  `EMBED_MODEL`.
- **Deterministic & idempotent.** Stable content-hash IDs, sorted output, fixed
  seeds. Re-running on unchanged inputs must not redo work or change outputs
  (so the `ai-eval` golden compare is stable).
- **Cite real chunks.** Every citation in the brief must be a chunk `id` that
  exists in `work/chunks.jsonl`. No invented references.
- **Fail clearly.** Non-zero exit + an actionable stderr message on any error.

## How stages are invoked

Each command is a shell wrapper → `just <recipe>` → `$AI_BRIEF_PYTHON
pipeline/<stage>.py "$@"`. Scripts read configuration from the environment
(below) and may accept optional CLI args (e.g. `brief --query "..."`). Do not
hardcode paths.

### Environment provided at runtime

| Variable | Meaning | Default |
|---|---|---|
| `SOURCES_DIR` | raw input files | `./sources` |
| `WORK_DIR` | intermediate artifacts | `./work` |
| `REPORTS_DIR` | final outputs | `./reports` |
| `PROMPTS_DIR` | prompt/config assets for `brief` | `./prompts` |
| `EVALS_DIR` | `fixtures/` and optional `expected/` | `./evals` |
| `AI_BACKEND` | `cuda` \| `mps` \| `cpu` (map to the torch device) | auto |
| `EMBED_MODEL` | embedding model id | `all-MiniLM-L6-v2` |

### Libraries already installed (use these; do not pip-install)

- Parsing: `pymupdf` (`fitz`), `pdfplumber`, `markdown`, `markdown-it-py`,
  `beautifulsoup4`, `lxml`, `readability-lxml`, `srt`, `unstructured`, `ftfy`.
  CLIs: `pandoc`, `poppler-utils`, `mupdf`, `html2text`, `yt-dlp`, `dos2unix`.
- Chunking: `semchunk`, `langchain-text-splitters`, `tiktoken`.
- Embeddings / store: `sentence-transformers`, `torch`, `chromadb`, `numpy`.
- Note: WebVTT is **not** available (`webvtt-py` isn't packaged); handle `.vtt`
  via a minimal inline parser or skip-and-report.

## Stages & artifact contracts

### `ingest.py` — sources → normalized chunks

- **In:** every supported file under `SOURCES_DIR` (recursively): PDF, Markdown,
  `.txt`, transcripts (`.srt`/`.vtt`), notes, `.jsonl`/`.csv`. Optional explicit
  paths as argv.
- **Out:** `WORK_DIR/documents.jsonl`, `WORK_DIR/chunks.jsonl`.
- **Do:** parse → normalize text (`ftfy`, unicode/whitespace/line-endings) →
  attach source metadata → split into chunks (`semchunk`) → write deterministic
  JSONL (stable IDs, sorted by `document_id` then `ordinal`). Skip unsupported
  files and report them to stderr; never crash on one bad file.

`documents.jsonl` record:
```json
{ "id": "doc_<sha256[:16]>", "source_path": "sources/a.pdf",
  "source_type": "pdf", "title": "…", "text": "<normalized full text>",
  "bytes": 12345, "metadata": { } }
```
`chunks.jsonl` record:
```json
{ "id": "chunk_<sha256[:16]>", "document_id": "doc_…", "ordinal": 0,
  "text": "<chunk text>", "char_start": 0, "char_end": 812,
  "token_count": 190, "source_path": "sources/a.pdf", "metadata": { } }
```
IDs are content hashes so unchanged inputs yield identical IDs across runs.

### `index.py` — chunks → vector index

- **In:** `WORK_DIR/chunks.jsonl`.
- **Out:** `WORK_DIR/index/` (Chroma persistent store), `WORK_DIR/index-manifest.json`.
- **Do:** embed each chunk with `sentence-transformers` on the `AI_BACKEND`
  device; upsert into a persistent ChromaDB collection keyed by chunk `id`, with
  the chunk text + metadata. **Idempotent:** store each chunk's content hash;
  skip chunks whose hash is unchanged since the last run (do not re-embed).

`index-manifest.json`:
```json
{ "model": "all-MiniLM-L6-v2", "dimension": 384, "backend": "cpu",
  "collection": "ai_brief", "chunk_count": 128, "generated_at": "<iso8601>",
  "chunk_hashes": { "chunk_…": "<sha256>" } }
```

### `brief.py` — retrieve + summarize → the brief

- **In:** `WORK_DIR/chunks.jsonl`, `WORK_DIR/index/`, `PROMPTS_DIR/`. Optional
  `--query`.
- **Out:** `REPORTS_DIR/brief.md` (human-readable), `REPORTS_DIR/brief.json`.
- **Do:** retrieve relevant chunks (query and/or cluster the corpus), group into
  themes, summarize each theme **extractively by default** (select
  representative real sentences — no LLM/API), cite the chunk `id`s each theme
  draws from, and surface uncertainties/gaps. Deterministic ordering.

`brief.json` schema (the contract `ai-eval` checks):
```json
{ "query": "…", "generated_at": "<iso8601>", "backend": "cpu",
  "chunk_count": 128,
  "themes": [
    { "title": "…", "summary": "…", "citations": ["chunk_…", "chunk_…"] }
  ],
  "uncertainties": ["…"],
  "sources": ["doc_…"] }
```
Required top-level keys: `query, themes, uncertainties, sources, chunk_count`.
`themes` must be non-empty; every `citations` entry must exist in
`chunks.jsonl`.

### `eval.py` — regression guard (invoked as `ai-eval`)

- **In:** `EVALS_DIR/fixtures/` (a tiny sample corpus, shipped), optional
  `EVALS_DIR/expected/brief.json` (golden).
- **Out:** `WORK_DIR/eval-results.json`.
- **Do:** run `ingest → index → brief` with `SOURCES_DIR=EVALS_DIR/fixtures`,
  then check: brief.json parses; required sections present; `themes` non-empty;
  **all citations resolve to real chunks**; optionally themes match the golden.
  Write per-check results; exit non-zero on any failure. Keep it seconds-fast.

`eval-results.json`:
```json
{ "ok": true, "generated_at": "<iso8601>",
  "checks": [ { "check": "citations resolve", "ok": true } ] }
```
Also ship a minimal `evals/fixtures/` (a few short docs) so `ai-eval` is green
out of the box.

### `doctor.py` — already implemented

Environment readiness report (OS/arch, backend, python/uv, torch import + CUDA/
MPS, project dirs, verdict). Do not stub it; extend only if adding checks.

## Exit conventions

- `0` success; non-zero on failure with an actionable stderr message.
- A missing `pipeline/<stage>.py` is reported by the `just` recipe as
  `not implemented: create pipeline/<stage>.py` — keep the filenames stable.
