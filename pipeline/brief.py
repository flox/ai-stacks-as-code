#!/usr/bin/env python3
"""ai-brief: brief stage.

Retrieve from the persisted vector index, group retrieved evidence, summarize
extractively, and cite real chunks. The index is a required input: a matching
manifest is not enough if the vector store is missing, stale, or unreadable.
"""
from __future__ import annotations

import argparse
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from common import (
    chunk_hash,
    env_path,
    preserve_generated_at,
    read_json,
    read_jsonl,
    sentences_with_offsets,
    sha256_json,
    tokenize,
    unique_preserve_order,
    write_json,
    write_text,
)
from index import COLLECTION_NAME, HashEmbedder, OnnxMiniLMEmbedder, SentenceTransformerEmbedder, batch_items, resolve_device

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "do", "does", "for", "from",
    "has", "have", "having", "if", "in", "into", "is", "it", "its", "of", "on", "or", "our", "that",
    "the", "their", "there", "these", "this", "to", "was", "were", "when", "with", "without", "will",
    "within", "you", "your", "we", "they", "them", "than", "then", "also", "not", "no", "yes", "may",
    "more", "most", "such", "one", "two", "three", "using", "use", "used", "via", "per", "all",
    "should", "could", "would", "shall",
}
DEFAULT_QUERY = "What are the main themes in the corpus?"


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def env_flag_any(*names: str) -> bool:
    return any(env_flag(name) for name in names)


def meaningful_terms(text: str) -> list[str]:
    return [tok for tok in tokenize(text) if len(tok) > 2 and tok not in STOPWORDS and not tok.isdigit()]


def load_required_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        return read_jsonl(path)
    except RuntimeError as exc:
        raise RuntimeError(f"missing or invalid {label}: {exc}") from exc


def current_chunk_hashes(chunks: list[dict[str, Any]]) -> dict[str, str]:
    return {str(chunk["id"]): chunk_hash(chunk) for chunk in chunks}


def explain_hash_delta(expected: dict[str, str], current: dict[str, str]) -> str:
    missing = sorted(set(expected) - set(current))
    extra = sorted(set(current) - set(expected))
    changed = sorted(cid for cid in set(expected) & set(current) if expected[cid] != current[cid])
    parts: list[str] = []
    if changed:
        parts.append(f"changed={len(changed)} example={changed[:3]}")
    if missing:
        parts.append(f"removed={len(missing)} example={missing[:3]}")
    if extra:
        parts.append(f"added={len(extra)} example={extra[:3]}")
    return "; ".join(parts) or "hash mismatch"


def validate_inputs(
    chunks: list[dict[str, Any]],
    manifest: dict[str, Any] | None,
    chunks_path: Path,
    manifest_path: Path,
    *,
    allow_hash_embeddings: bool,
    allow_json_store: bool,
) -> dict[str, str]:
    if not chunks:
        raise RuntimeError(f"no chunks found in {chunks_path}; run ingest first")
    if not isinstance(manifest, dict):
        raise RuntimeError(f"missing required index manifest {manifest_path}; run index first")
    if manifest.get("chunk_count") != len(chunks):
        raise RuntimeError(
            f"index manifest chunk_count={manifest.get('chunk_count')} does not match {len(chunks)} chunks; run index again"
        )

    ids: set[str] = set()
    for chunk in chunks:
        chunk_id = chunk.get("id")
        if not isinstance(chunk_id, str) or not chunk_id.startswith("chunk_"):
            raise RuntimeError(f"invalid chunk record in {chunks_path}: missing chunk_ id")
        if chunk_id in ids:
            raise RuntimeError(f"duplicate chunk id in {chunks_path}: {chunk_id}")
        ids.add(chunk_id)
        if not isinstance(chunk.get("text"), str) or not chunk["text"].strip():
            raise RuntimeError(f"invalid chunk {chunk_id}: missing text")

    manifest_hashes = manifest.get("chunk_hashes")
    if not isinstance(manifest_hashes, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in manifest_hashes.items()):
        raise RuntimeError(f"index manifest {manifest_path} is missing valid chunk_hashes; run index again")
    hashes = current_chunk_hashes(chunks)
    if dict(sorted(manifest_hashes.items())) != dict(sorted(hashes.items())):
        raise RuntimeError(
            "chunks changed since index was generated; run index again "
            f"({explain_hash_delta({str(k): str(v) for k, v in manifest_hashes.items()}, hashes)})"
        )

    store = manifest.get("store")
    if store == "json" and not allow_json_store:
        raise RuntimeError(
            "index manifest points to a JSON vector store, which is allowed only in explicit sandbox/dev mode; "
            "run index with the required Chroma store or pass --dev-json-store with AI_BRIEF_ALLOW_JSON_STORE=1"
        )
    if store not in {"chroma", "json"}:
        raise RuntimeError(f"index manifest has unsupported store={store!r}; run index again")

    engine = manifest.get("embedding_engine")
    if engine == "hash" and not allow_hash_embeddings:
        raise RuntimeError(
            "index manifest uses hash embeddings, which are allowed only in explicit sandbox/dev mode; "
            "run index with sentence-transformers or pass --dev-hash-embeddings, set AI_BRIEF_ALLOW_HASH_FALLBACK=1, or use EMBED_MODEL=hash:<name> for explicit sandbox/dev mode"
        )
    if engine not in {"onnx", "sentence-transformers", "hash"}:
        raise RuntimeError(f"index manifest has unsupported embedding_engine={engine!r}; run index again")
    if engine == "hash" and store == "chroma":
        # Legal only in explicit dev mode, but still queryable because vectors are persisted in Chroma.
        pass
    return hashes


def corpus_statistics(chunks: list[dict[str, Any]]) -> tuple[dict[str, Counter[str]], Counter[str], Counter[str], dict[str, float]]:
    per_chunk: dict[str, Counter[str]] = {}
    df: Counter[str] = Counter()
    tf: Counter[str] = Counter()
    for chunk in chunks:
        chunk_id = str(chunk["id"])
        counts = Counter(meaningful_terms(str(chunk["text"])))
        per_chunk[chunk_id] = counts
        tf.update(counts)
        df.update(counts.keys())
    n = max(1, len(chunks))
    idf = {term: math.log((1 + n) / (1 + freq)) + 1.0 for term, freq in df.items()}
    return per_chunk, df, tf, idf


def score_chunk(chunk: dict[str, Any], counts: Counter[str], idf: dict[str, float], query_terms: list[str], seed: str | None = None) -> float:
    score = 0.0
    if seed:
        score += counts.get(seed, 0) * idf.get(seed, 1.0) * 4.0
    for term in query_terms:
        score += counts.get(term, 0) * idf.get(term, 1.0) * 3.0
    if not query_terms and not seed:
        score += sum(count * idf.get(term, 1.0) for term, count in counts.items())
    score += min(float(chunk.get("token_count", 0) or 0), 300.0) / 3000.0
    return score


def seed_terms(query: str, tf: Counter[str], df: Counter[str], idf: dict[str, float], max_terms: int) -> list[str]:
    seeds: list[str] = []
    for term in meaningful_terms(query):
        if term in df and term not in seeds:
            seeds.append(term)
    ranked = sorted(tf, key=lambda term: (-tf[term] * idf.get(term, 1.0), df[term], term))
    for term in ranked:
        if term not in seeds:
            seeds.append(term)
        if len(seeds) >= max_terms * 4:
            break
    return seeds


def retrieval_queries(query: str, chunks: list[dict[str, Any]], max_themes: int) -> list[str]:
    per_chunk, df, tf, idf = corpus_statistics(chunks)
    del per_chunk
    query_clean = re.sub(r"\s+", " ", query).strip() or DEFAULT_QUERY
    query_terms = meaningful_terms(query_clean)
    seeds = seed_terms(query_clean, tf, df, idf, max(1, max_themes))
    out: list[str] = []
    if query_terms and query_clean != DEFAULT_QUERY:
        out.append(query_clean)
        for seed in seeds:
            if seed not in query_terms:
                out.append(f"{query_clean} {seed}")
            if len(out) >= max_themes:
                break
    else:
        for seed in seeds[:max_themes]:
            out.append(f"Evidence about {seed}")
    if not out:
        out.append(query_clean)
    return unique_preserve_order(out)[:max(1, max_themes)]


def make_query_embedder(manifest: dict[str, Any], *, allow_hash_embeddings: bool):
    model = manifest.get("model")
    if not isinstance(model, str) or not model:
        raise RuntimeError("index manifest is missing embedding model; run index again")
    engine = manifest.get("embedding_engine")
    if engine == "hash":
        if not allow_hash_embeddings:
            raise RuntimeError("hash query embeddings require explicit sandbox/dev mode; run index with sentence-transformers, pass --dev-hash-embeddings, set AI_BRIEF_ALLOW_HASH_FALLBACK=1, or use EMBED_MODEL=hash:<name>")
        dim = manifest.get("dimension", 384)
        try:
            dim_int = int(dim)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid hash embedding dimension in index manifest: {dim!r}") from exc
        return HashEmbedder(model=model, dimension=dim_int)
    if engine == "onnx":
        try:
            return OnnxMiniLMEmbedder()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"failed to load the ONNX MiniLM query embedder: {exc}. "
                "Activate the composed Flox environment (chromadb + onnxruntime)."
            ) from exc
    if engine != "sentence-transformers":
        raise RuntimeError(f"unsupported embedding engine in index manifest: {engine!r}; run index again")
    device = resolve_device(str(manifest.get("backend") or os.environ.get("AI_BACKEND", "cpu")))
    try:
        return SentenceTransformerEmbedder(model=model, device=device)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"failed to load sentence-transformers query model {model!r}: {exc}. "
            "Activate the composed Flox environment or run index/brief in the same dependency context."
        ) from exc


def validate_chroma_collection(collection: Any, chunks: list[dict[str, Any]], hashes: dict[str, str], collection_name: str) -> None:
    chunk_ids = [str(chunk["id"]) for chunk in chunks]
    try:
        count = int(collection.count())
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"could not read Chroma collection {collection_name!r}: {exc}") from exc
    if count != len(chunk_ids):
        raise RuntimeError(
            f"Chroma collection {collection_name!r} contains {count} vector(s), but chunks.jsonl contains {len(chunk_ids)} chunk(s); run index again"
        )

    found: dict[str, dict[str, Any] | None] = {}
    for ids in batch_items(chunk_ids, 512):
        try:
            got = collection.get(ids=ids, include=["metadatas"])
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"could not read Chroma records for indexed chunks: {exc}") from exc
        got_ids = got.get("ids", []) if isinstance(got, dict) else []
        got_metas = got.get("metadatas", []) if isinstance(got, dict) else []
        for cid, meta in zip(got_ids, got_metas, strict=False):
            found[str(cid)] = meta if isinstance(meta, dict) else None
    missing = sorted(set(chunk_ids) - set(found))
    if missing:
        raise RuntimeError(f"Chroma index is missing {len(missing)} chunk vector(s), for example {missing[:3]}; run index again")
    mismatched = sorted(cid for cid, meta in found.items() if not meta or str(meta.get("chunk_hash", "")) != hashes[cid])
    if mismatched:
        raise RuntimeError(f"Chroma index has stale metadata for {len(mismatched)} chunk vector(s), for example {mismatched[:3]}; run index again")


def retrieve_from_chroma(
    index_dir: Path,
    chunks: list[dict[str, Any]],
    hashes: dict[str, str],
    manifest: dict[str, Any],
    queries: list[str],
    n_results: int,
    *,
    allow_hash_embeddings: bool,
) -> list[str]:
    try:
        import chromadb  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"failed to import ChromaDB while reading required index at {index_dir}: {exc}. "
            "Activate the composed Flox environment with chromadb available."
        ) from exc
    collection_name = str(manifest.get("collection") or COLLECTION_NAME)
    try:
        client = chromadb.PersistentClient(path=str(index_dir))
        collection = client.get_collection(name=collection_name)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"failed to open Chroma collection {collection_name!r} at {index_dir}: {exc}; run index again") from exc

    validate_chroma_collection(collection, chunks, hashes, collection_name)
    embedder = make_query_embedder(manifest, allow_hash_embeddings=allow_hash_embeddings)
    query_embeddings = embedder.encode(queries)
    n = max(1, min(n_results, len(chunks)))
    try:
        result = collection.query(query_embeddings=query_embeddings, n_results=n, include=["metadatas", "distances"])
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"failed to query Chroma collection {collection_name!r}: {exc}; run index again") from exc

    known = {str(chunk["id"]) for chunk in chunks}
    rows = result.get("ids", []) if isinstance(result, dict) else []
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"Chroma query returned no result rows from {collection_name!r}; run index again")
    ordered: list[str] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        for cid in row:
            cid_s = str(cid)
            if cid_s not in known:
                raise RuntimeError(f"Chroma query returned unknown chunk id {cid_s!r}; run index again")
            ordered.append(cid_s)
    return unique_preserve_order(ordered)


def dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=False))


def retrieve_from_json_store(
    index_dir: Path,
    chunks: list[dict[str, Any]],
    hashes: dict[str, str],
    manifest: dict[str, Any],
    queries: list[str],
    n_results: int,
    *,
    allow_hash_embeddings: bool,
    allow_json_store: bool,
) -> list[str]:
    if not allow_json_store:
        raise RuntimeError("JSON vector-store retrieval requires explicit sandbox/dev mode")
    store_path = index_dir / "index.json"
    store = read_json(store_path, default=None)
    if not isinstance(store, dict) or not isinstance(store.get("records"), list):
        raise RuntimeError(f"missing or invalid JSON vector store {store_path}; run index again")
    records: dict[str, dict[str, Any]] = {}
    for rec in store["records"]:
        if isinstance(rec, dict) and isinstance(rec.get("id"), str):
            records[rec["id"]] = rec
    chunk_ids = {str(chunk["id"]) for chunk in chunks}
    if set(records) != chunk_ids:
        missing = sorted(chunk_ids - set(records))
        extra = sorted(set(records) - chunk_ids)
        raise RuntimeError(f"JSON index ids do not match chunks; missing={missing[:3]} extra={extra[:3]}; run index again")
    mismatched: list[str] = []
    vectors: dict[str, list[float]] = {}
    dimension = int(manifest.get("dimension", 0) or 0)
    for cid, rec in records.items():
        meta = rec.get("metadata") if isinstance(rec.get("metadata"), dict) else {}
        if str(meta.get("chunk_hash", "")) != hashes[cid]:
            mismatched.append(cid)
        emb = rec.get("embedding")
        if not isinstance(emb, list) or not emb:
            raise RuntimeError(f"JSON index record {cid} is missing its embedding; run index again")
        vector = [float(x) for x in emb]
        if dimension and len(vector) != dimension:
            raise RuntimeError(f"JSON index record {cid} has dimension {len(vector)}, expected {dimension}; run index again")
        vectors[cid] = vector
    if mismatched:
        raise RuntimeError(f"JSON index has stale metadata for {len(mismatched)} chunk vector(s), for example {sorted(mismatched)[:3]}; run index again")

    embedder = make_query_embedder(manifest, allow_hash_embeddings=allow_hash_embeddings)
    query_embeddings = embedder.encode(queries)
    scores: dict[str, float] = {}
    for cid, vector in vectors.items():
        scores[cid] = max(dot(query_vec, vector) for query_vec in query_embeddings)
    ranked = sorted(scores, key=lambda cid: (-scores[cid], cid))
    return ranked[:max(1, min(n_results, len(ranked)))]


def retrieve_chunk_ids(
    work_dir: Path,
    chunks: list[dict[str, Any]],
    hashes: dict[str, str],
    manifest: dict[str, Any],
    query: str,
    max_themes: int,
    chunks_per_theme: int,
    *,
    allow_hash_embeddings: bool,
    allow_json_store: bool,
) -> tuple[list[str], list[str]]:
    queries = retrieval_queries(query, chunks, max_themes)
    n_results = max(max_themes * chunks_per_theme * 2, chunks_per_theme, 1)
    index_dir = work_dir / "index"
    store = manifest.get("store")
    if store == "chroma":
        ids = retrieve_from_chroma(index_dir, chunks, hashes, manifest, queries, n_results, allow_hash_embeddings=allow_hash_embeddings)
    elif store == "json":
        ids = retrieve_from_json_store(
            index_dir,
            chunks,
            hashes,
            manifest,
            queries,
            n_results,
            allow_hash_embeddings=allow_hash_embeddings,
            allow_json_store=allow_json_store,
        )
    else:
        raise RuntimeError(f"unsupported index store={store!r}; run index again")
    if not ids:
        raise RuntimeError("index retrieval returned no chunks; run index again")
    return ids, queries


def title_for_group(seed: str, chunks: list[dict[str, Any]], per_chunk: dict[str, Counter[str]], idf: dict[str, float]) -> str:
    combined: Counter[str] = Counter()
    for chunk in chunks:
        combined.update(per_chunk[str(chunk["id"])])
    ranked = sorted(combined, key=lambda term: (-combined[term] * idf.get(term, 1.0), term))
    terms = unique_preserve_order([seed] + ranked)[:3]
    title = " ".join(term.replace("_", " ").title() for term in terms if term)
    return title or "Evidence Theme"


def candidate_sentences(chunks: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for chunk in chunks:
        chunk_id = str(chunk["id"])
        text = str(chunk["text"])
        before = len(out)
        for _start, _end, sentence in sentences_with_offsets(text):
            if len(sentence.split()) >= 5:
                out.append((chunk_id, sentence, text))
        if len(out) == before:
            compact = re.sub(r"\s+", " ", text).strip()
            if compact:
                out.append((chunk_id, compact[:500], text))
    return out


def score_sentence(sentence: str, seed: str, theme_terms: list[str], query_terms: list[str]) -> float:
    counts = Counter(meaningful_terms(sentence))
    score = 0.0
    score += counts.get(seed, 0) * 5.0
    for term in theme_terms[:6]:
        score += counts.get(term, 0) * 1.4
    for term in query_terms:
        score += counts.get(term, 0) * 2.5
    length = len(sentence.split())
    if 12 <= length <= 38:
        score += 2.0
    elif length > 70:
        score -= 1.0
    return score


def summarize_group(seed: str, chunks: list[dict[str, Any]], per_chunk: dict[str, Counter[str]], idf: dict[str, float], query_terms: list[str]) -> tuple[str, list[str]]:
    combined: Counter[str] = Counter()
    for chunk in chunks:
        combined.update(per_chunk[str(chunk["id"])])
    theme_terms = sorted(combined, key=lambda term: (-combined[term] * idf.get(term, 1.0), term))
    candidates = candidate_sentences(chunks)
    ranked = sorted(
        candidates,
        key=lambda item: (-score_sentence(item[1], seed, theme_terms, query_terms), item[0], item[1]),
    )
    sentences: list[str] = []
    citations: list[str] = []
    seen_norm: set[str] = set()
    for chunk_id, sentence, _text in ranked:
        norm = re.sub(r"\W+", " ", sentence.lower()).strip()
        if not norm or norm in seen_norm:
            continue
        seen_norm.add(norm)
        sentences.append(sentence)
        citations.append(chunk_id)
        if len(sentences) >= 3:
            break
    if not sentences and chunks:
        chunk = chunks[0]
        sentences = [str(chunk["text"])[:500].strip()]
        citations = [str(chunk["id"])]
    return " ".join(sentences), unique_preserve_order(citations)


def build_themes(
    chunks: list[dict[str, Any]],
    retrieved_ids: list[str],
    query: str,
    max_themes: int,
    chunks_per_theme: int,
) -> list[dict[str, Any]]:
    chunk_by_id = {str(chunk["id"]): chunk for chunk in chunks}
    evidence = [chunk_by_id[cid] for cid in retrieved_ids if cid in chunk_by_id]
    if not evidence:
        raise RuntimeError("retrieval returned no chunk ids present in chunks.jsonl; run index again")
    per_chunk, df, tf, idf = corpus_statistics(evidence)
    query_terms = meaningful_terms(query)
    seeds = seed_terms(query, tf, df, idf, max_themes)
    themes: list[dict[str, Any]] = []
    used_citations: set[str] = set()

    for seed in seeds:
        candidates = [chunk for chunk in evidence if per_chunk[str(chunk["id"])].get(seed, 0) > 0]
        if not candidates:
            continue
        retrieved_rank = {cid: idx for idx, cid in enumerate(retrieved_ids)}
        ranked = sorted(
            candidates,
            key=lambda chunk: (
                -score_chunk(chunk, per_chunk[str(chunk["id"])], idf, query_terms, seed),
                str(chunk.get("document_id", "")),
                int(chunk.get("ordinal", 0)),
                str(chunk.get("id", "")),
                retrieved_rank.get(str(chunk.get("id", "")), len(retrieved_rank)),
            ),
        )
        primary = str(ranked[0]["id"])
        if primary in used_citations:
            continue
        selected = ranked[:chunks_per_theme]
        summary, citations = summarize_group(seed, selected, per_chunk, idf, query_terms)
        if not citations:
            continue
        themes.append({
            "title": title_for_group(seed, selected, per_chunk, idf),
            "summary": summary,
            "citations": citations,
        })
        used_citations.update(citations)
        if len(themes) >= max_themes:
            break

    if not themes:
        selected = evidence[:chunks_per_theme]
        seed = seed_terms(query, tf, df, idf, 1)[0] if tf else "corpus"
        summary, citations = summarize_group(seed, selected, per_chunk, idf, query_terms)
        themes.append({"title": title_for_group(seed, selected, per_chunk, idf), "summary": summary, "citations": citations})

    seen_titles: Counter[str] = Counter()
    for theme in themes:
        title = str(theme["title"])
        seen_titles[title] += 1
        if seen_titles[title] > 1:
            theme["title"] = f"{title} {seen_titles[title]}"
    return themes


def build_uncertainties(query: str, chunks: list[dict[str, Any]], retrieved_ids: list[str], manifest: dict[str, Any]) -> list[str]:
    uncertainties: list[str] = []
    if not query.strip() or query.strip() == DEFAULT_QUERY:
        uncertainties.append("No explicit query was supplied; the brief retrieves and ranks recurring corpus themes rather than answering a targeted question.")
    if len(chunks) < 3:
        uncertainties.append("The corpus is small, so recurring themes may reflect limited source coverage.")
    if len(retrieved_ids) < len(chunks):
        uncertainties.append(f"The brief summarizes {len(retrieved_ids)} retrieved chunk(s) out of {len(chunks)} indexed chunk(s).")
    if manifest.get("embedding_engine") == "hash":
        uncertainties.append("The index uses deterministic hash embeddings in explicit development mode, so semantic retrieval quality is lower than a sentence-transformers index.")
    if not uncertainties:
        uncertainties.append("The brief is extractive and limited to the supplied sources; it does not infer facts outside the corpus.")
    return uncertainties


def render_markdown(brief: dict[str, Any], doc_titles: dict[str, str]) -> str:
    lines: list[str] = []
    lines.append("# Evidence-backed brief")
    lines.append("")
    lines.append(f"**Query:** {brief['query']}")
    lines.append(f"**Chunks:** {brief['chunk_count']}")
    lines.append(f"**Generated:** {brief['generated_at']}")
    lines.append("")
    lines.append("## Themes")
    for idx, theme in enumerate(brief["themes"], start=1):
        lines.append("")
        lines.append(f"### {idx}. {theme['title']}")
        lines.append("")
        lines.append(str(theme["summary"]))
        lines.append("")
        lines.append("Citations: " + ", ".join(f"`{cid}`" for cid in theme["citations"]))
    lines.append("")
    lines.append("## Uncertainties and gaps")
    for item in brief["uncertainties"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Sources")
    for doc_id in brief["sources"]:
        title = doc_titles.get(doc_id, "")
        lines.append(f"- `{doc_id}`" + (f" - {title}" if title else ""))
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="brief", description="generate the evidence-backed brief")
    parser.add_argument("paths", nargs="*", help="reserved; brief reads WORK_DIR artifacts")
    parser.add_argument("--query", default=None, help="question or focus for retrieval")
    parser.add_argument("--max-themes", type=int, default=5)
    parser.add_argument("--chunks-per-theme", type=int, default=4)
    parser.add_argument(
        "--dev-hash-embeddings",
        action="store_true",
        help="explicit sandbox/dev mode: permit querying a hash-embedding index",
    )
    parser.add_argument(
        "--dev-json-store",
        action="store_true",
        help="explicit sandbox/dev mode: permit retrieval from the JSON vector store",
    )
    args = parser.parse_args(argv)
    if args.paths:
        print("error: brief does not accept positional paths yet; use --query and WORK_DIR artifacts", file=sys.stderr)
        return 2
    if args.max_themes <= 0 or args.chunks_per_theme <= 0:
        print("error: --max-themes and --chunks-per-theme must be positive", file=sys.stderr)
        return 2

    work_dir = env_path("WORK_DIR", "work")
    reports_dir = env_path("REPORTS_DIR", "reports")
    chunks_path = work_dir / "chunks.jsonl"
    documents_path = work_dir / "documents.jsonl"
    manifest_path = work_dir / "index-manifest.json"
    brief_json_path = reports_dir / "brief.json"
    brief_md_path = reports_dir / "brief.md"
    query = args.query if args.query is not None else DEFAULT_QUERY
    embed_model_env = os.environ.get("EMBED_MODEL", "")
    allow_hash_embeddings = (
        args.dev_hash_embeddings
        or env_flag_any("AI_BRIEF_ALLOW_HASH_FALLBACK", "AI_BRIEF_ALLOW_HASH_EMBEDDINGS")
        or embed_model_env.startswith("hash:")
        or embed_model_env.startswith("hash://")
    )
    allow_json_store = args.dev_json_store or env_flag_any("AI_BRIEF_ALLOW_JSON_FALLBACK", "AI_BRIEF_ALLOW_JSON_STORE")

    try:
        chunks = load_required_jsonl(chunks_path, "chunks")
        chunks.sort(key=lambda rec: (str(rec.get("document_id", "")), int(rec.get("ordinal", 0)), str(rec.get("id", ""))))
        manifest = read_json(manifest_path, default=None)
        hashes = validate_inputs(
            chunks,
            manifest,
            chunks_path,
            manifest_path,
            allow_hash_embeddings=allow_hash_embeddings,
            allow_json_store=allow_json_store,
        )
        assert isinstance(manifest, dict)
        documents = read_jsonl(documents_path) if documents_path.exists() else []
        doc_titles = {str(doc.get("id")): str(doc.get("title", "")) for doc in documents if isinstance(doc.get("id"), str)}

        retrieved_ids, queries = retrieve_chunk_ids(
            work_dir,
            chunks,
            hashes,
            manifest,
            query,
            args.max_themes,
            args.chunks_per_theme,
            allow_hash_embeddings=allow_hash_embeddings,
            allow_json_store=allow_json_store,
        )
        themes = build_themes(chunks, retrieved_ids, query, args.max_themes, args.chunks_per_theme)
        valid_chunk_ids = {str(chunk["id"]) for chunk in chunks}
        for theme in themes:
            citations = theme.get("citations")
            if not isinstance(citations, list) or not citations:
                raise RuntimeError(f"theme {theme.get('title', '<untitled>')} has no citations")
            bad = [cid for cid in citations if cid not in valid_chunk_ids]
            if bad:
                raise RuntimeError(f"theme {theme.get('title', '<untitled>')} cites unknown chunk ids: {bad}")
            not_retrieved = [cid for cid in citations if cid not in set(retrieved_ids)]
            if not_retrieved:
                raise RuntimeError(f"theme {theme.get('title', '<untitled>')} cites chunks not returned by index retrieval: {not_retrieved}")

        cited_chunk_ids = {cid for theme in themes for cid in theme["citations"]}
        chunk_to_doc = {str(chunk["id"]): str(chunk.get("document_id", "")) for chunk in chunks}
        sources = sorted({chunk_to_doc[cid] for cid in cited_chunk_ids if chunk_to_doc.get(cid)})
        if not sources:
            sources = sorted({str(chunk.get("document_id", "")) for chunk in chunks if chunk.get("document_id")})

        fingerprint = sha256_json({
            "query": query,
            "chunk_hashes": hashes,
            "index": manifest.get("fingerprint"),
            "retrieval_queries": queries,
            "retrieved_ids": retrieved_ids,
            "themes": themes,
        })
        generated_at = preserve_generated_at(brief_json_path, fingerprint)
        brief = {
            "query": query,
            "generated_at": generated_at,
            "backend": manifest.get("backend", "cpu"),
            "chunk_count": len(chunks),
            "themes": themes,
            "uncertainties": build_uncertainties(query, chunks, retrieved_ids, manifest),
            "sources": sources,
            "metadata": {
                "fingerprint": fingerprint,
                "index_collection": manifest.get("collection"),
                "embedding_model": manifest.get("model"),
                "embedding_engine": manifest.get("embedding_engine"),
                "index_store": manifest.get("store"),
                "retrieval_queries": queries,
                "retrieved_chunk_ids": retrieved_ids,
            },
        }
        reports_dir.mkdir(parents=True, exist_ok=True)
        write_json(brief_json_path, brief)
        write_text(brief_md_path, render_markdown(brief, doc_titles))
        print(
            f"brief: wrote {brief_md_path} and {brief_json_path} "
            f"({len(themes)} theme(s), {len(retrieved_ids)} retrieved chunk(s), store={manifest.get('store')})"
        )
        return 0
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"error: unexpected brief failure: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
