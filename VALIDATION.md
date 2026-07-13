# Validation

This bundle was patched on July 8, 2026 so the default implementation satisfies `SPEC.md` rather than passing through a substitute smoke path. v6 tightens development-mode documentation and extends `ai-doctor` with checks for the strict-path package imports.

## What is strict by default

- `ingest.py` uses `semchunk` by default and fails nonzero if `semchunk` is unavailable or fails. The inline deterministic chunker exists only under `AI_BRIEF_ALLOW_CHUNK_FALLBACK=1` or `ingest --dev-fallback-chunker`.
- `index.py` uses `sentence-transformers` and a persistent ChromaDB collection by default. Missing `sentence-transformers`, failed model load, missing `chromadb`, or failed Chroma writes return nonzero with actionable stderr.
- Hash embeddings are explicit development mode only: set `EMBED_MODEL=hash:<name>` to choose the deterministic hash embedder. The `AI_BRIEF_ALLOW_HASH_FALLBACK=1` / `AI_BRIEF_ALLOW_HASH_EMBEDDINGS=1` environment variables or `index --dev-hash-embeddings` authorize that explicit development-mode choice; they do not choose hash embeddings by themselves.
- The JSON vector store is explicit development mode only: set `AI_BRIEF_ALLOW_JSON_FALLBACK=1` / `AI_BRIEF_ALLOW_JSON_STORE=1` or pass `index --dev-json-store`.
- `brief.py` validates exact `chunk_hashes` parity with `index-manifest.json`, opens the persisted vector store, validates indexed records, embeds retrieval queries, and cites only chunks returned from the index.
- `ingest.py` treats explicitly requested missing input paths as user errors.
- `ai-eval` defaults to the strict `semchunk` + `sentence-transformers` + Chroma path, records `embedding_engine` and `index_store` in `work/eval-results.json`, compares against `evals/expected/brief.json`, verifies citation resolution, verifies stale-index rejection, verifies bad-citation rejection, and verifies byte-identical rerun idempotency.

## Commands run in this sandbox

The sandbox runtime does not include `semchunk`, `sentence-transformers`, or `chromadb`, so the real strict path cannot complete here with the actual packaged dependencies. The default path correctly fails instead of silently falling back:

```bash
python3 -m py_compile pipeline/*.py
python3 pipeline/doctor.py
WORK_DIR=/tmp/ai-brief-v6-strict EVALS_DIR=evals python3 pipeline/eval.py
# nonzero in this sandbox because strict-path packages are absent
```

The explicit development smoke path passes and exercises fallback chunking, hash embeddings, JSON-store retrieval, golden comparison, bad-citation rejection, stale-index rejection, and rerun idempotency:

```bash
WORK_DIR=/tmp/ai-brief-v6-dev EVALS_DIR=evals python3 pipeline/eval.py --dev-hash
# ok
```

The missing-explicit-path rule was validated:

```bash
WORK_DIR=/tmp/ai-brief-missing python3 pipeline/ingest.py /tmp/does-not-exist
# exit 2 with: error: explicit input path does not exist
```

A strict-path harness was also run with temporary interface-compatible local shims for `semchunk`, `sentence_transformers`, and `chromadb` to verify the evaluator logic, Chroma code path, metadata recording, golden comparison, stale-index rejection, bad-citation rejection, and rerun idempotency without changing the bundle. That harness produced `ok: true` with:

```json
{
  "mode": "strict",
  "embedding_engine": "sentence-transformers",
  "index_store": "chroma",
  "brief_embedding_engine": "sentence-transformers",
  "brief_index_store": "chroma"
}
```

## Required release check

Before release, run plain `ai-eval` inside the composed Flox environment. It must complete with `ok: true` and `metadata.embedding_engine == "sentence-transformers"` plus `metadata.index_store == "chroma"`. Do not treat `ai-eval --dev-hash` as a handoff signal.
