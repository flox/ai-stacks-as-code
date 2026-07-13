#!/usr/bin/env python3
"""ai-brief: index stage.

Chunks -> embeddings -> local vector index.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from common import chunk_hash, env_path, read_json, read_jsonl, sha256_json, sha256_text, stable_json, tokenize, utc_now_iso, write_json

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
COLLECTION_NAME = "ai_brief"


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def env_flag_any(*names: str) -> bool:
    return any(env_flag(name) for name in names)


def warn(message: str) -> None:
    print(message, file=sys.stderr)



def validate_chunks(chunks: list[dict[str, Any]], chunks_path: Path) -> None:
    if not chunks:
        raise RuntimeError(f"no chunks found in {chunks_path}; run ingest on supported sources first")
    seen: set[str] = set()
    for idx, chunk in enumerate(chunks):
        chunk_id = chunk.get("id")
        text = chunk.get("text")
        if not isinstance(chunk_id, str) or not chunk_id.startswith("chunk_"):
            raise RuntimeError(f"invalid chunk at {chunks_path}:{idx + 1}: missing chunk_ id")
        if chunk_id in seen:
            raise RuntimeError(f"duplicate chunk id in {chunks_path}: {chunk_id}")
        seen.add(chunk_id)
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError(f"invalid chunk {chunk_id}: missing text")


def resolve_device(requested_backend: str) -> str:
    backend = requested_backend.lower()
    if backend == "cpu":
        return "cpu"
    try:
        import torch  # type: ignore
    except Exception:  # noqa: BLE001
        warn(f"index: requested AI_BACKEND={requested_backend}, but torch is unavailable; using CPU")
        return "cpu"
    if backend == "cuda" and getattr(torch, "cuda", None) and torch.cuda.is_available():
        return "cuda"
    if backend == "mps":
        mps = getattr(getattr(torch, "backends", None), "mps", None)
        if mps and mps.is_available():
            return "mps"
    warn(f"index: requested AI_BACKEND={requested_backend}, but that accelerator is unavailable; using CPU")
    return "cpu"


class Embedder:
    engine = "base"
    dimension = 0

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError


class HashEmbedder(Embedder):
    engine = "hash"

    def __init__(self, model: str, dimension: int = 384) -> None:
        self.model = model
        self.dimension = dimension

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._encode_one(text) for text in texts]

    def _encode_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        for token in tokenize(text):
            digest = sha256_text(token)
            idx = int(digest[:8], 16) % self.dimension
            sign = 1.0 if int(digest[8:10], 16) % 2 == 0 else -1.0
            # dampen repeated boilerplate without throwing it away
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm:
            vec = [v / norm for v in vec]
        return vec


class SentenceTransformerEmbedder(Embedder):
    engine = "sentence-transformers"

    def __init__(self, model: str, device: str) -> None:
        from sentence_transformers import SentenceTransformer  # type: ignore

        self.model = model
        self.device = device
        self._model = SentenceTransformer(model, device=device)
        dimension = self._model.get_sentence_embedding_dimension()
        if not isinstance(dimension, int) or dimension <= 0:
            sample = self.encode(["dimension probe"])
            dimension = len(sample[0])
        self.dimension = dimension

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts),
            batch_size=32,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [[float(x) for x in row] for row in vectors.tolist()]


def make_embedder(model: str, requested_backend: str, allow_hash_embeddings: bool) -> tuple[Embedder, str]:
    if model.startswith("hash:") or model.startswith("hash://"):
        # Selecting an EMBED_MODEL in the hash namespace is itself an explicit
        # development-mode request. The default model remains the spec-required
        # sentence-transformers model, and missing sentence-transformers never
        # falls through to this branch.
        allow_hash_embeddings = True
        dim_raw = os.environ.get("AI_BRIEF_HASH_EMBED_DIM", "384")
        try:
            dim = int(dim_raw)
        except ValueError as exc:
            raise RuntimeError(f"invalid AI_BRIEF_HASH_EMBED_DIM={dim_raw!r}; expected an integer") from exc
        if dim <= 0:
            raise RuntimeError(f"invalid AI_BRIEF_HASH_EMBED_DIM={dim}; expected a positive integer")
        warn("index: explicit development mode enabled: using deterministic hash embeddings, not sentence-transformers")
        return HashEmbedder(model=model, dimension=dim), "cpu"

    device = resolve_device(requested_backend)
    try:
        embedder = SentenceTransformerEmbedder(model=model, device=device)
        return embedder, device
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"failed to load sentence-transformers model {model!r} on device {device!r}: {exc}. "
            "Install/activate the composed Flox environment with sentence-transformers available, allow the model cache/download on first index, "
            "or choose a valid EMBED_MODEL. The pipeline no longer falls back to hash embeddings by default. "
            "For sandbox-only smoke tests, set AI_BRIEF_ALLOW_HASH_FALLBACK=1 or AI_BRIEF_ALLOW_HASH_EMBEDDINGS=1 and EMBED_MODEL=hash:<name>."
        ) from exc

def scalar_metadata(chunk: dict[str, Any], hash_value: str) -> dict[str, str | int | float | bool]:
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    return {
        "chunk_hash": hash_value,
        "document_id": str(chunk.get("document_id", "")),
        "ordinal": int(chunk.get("ordinal", 0)),
        "source_path": str(chunk.get("source_path", "")),
        "metadata_json": stable_json(metadata),
    }


def batch_items(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def index_with_chroma(
    index_dir: Path,
    chunks: list[dict[str, Any]],
    hashes: dict[str, str],
    embedder: Embedder,
    old_manifest: dict[str, Any] | None,
    force_all: bool,
) -> tuple[str, int, int]:
    import chromadb  # type: ignore

    index_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(index_dir))
    collection = client.get_or_create_collection(name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"})

    chunk_ids = [str(chunk["id"]) for chunk in chunks]
    old_hashes = old_manifest.get("chunk_hashes", {}) if isinstance(old_manifest, dict) else {}
    if not isinstance(old_hashes, dict):
        old_hashes = {}

    existing_ids: set[str] = set()
    try:
        try:
            got = collection.get(ids=chunk_ids, include=[])
        except Exception:  # noqa: BLE001
            got = collection.get(ids=chunk_ids, include=["metadatas"])
        existing_ids = {str(item) for item in got.get("ids", [])}
    except Exception as exc:  # noqa: BLE001
        warn(f"index: warning: could not verify existing Chroma ids; changed chunks will be upserted conservatively: {exc}")
        existing_ids = set()

    changed = [
        chunk for chunk in chunks
        if force_all or old_hashes.get(str(chunk["id"])) != hashes[str(chunk["id"])] or str(chunk["id"]) not in existing_ids
    ]
    stale_ids = sorted(set(str(k) for k in old_hashes) - set(chunk_ids))
    stale = len(stale_ids)
    if stale_ids:
        try:
            for ids in batch_items(stale_ids, 512):
                collection.delete(ids=ids)
        except Exception as exc:  # noqa: BLE001
            warn(f"index: warning: failed to delete stale Chroma ids from previous manifest: {exc}")

    # Reconcile against the actual persistent collection as well, not only the
    # previous manifest. This keeps Chroma self-healing if a manifest was lost,
    # hand-edited, or copied without a matching index directory.
    try:
        all_existing = collection.get(include=[])
        all_ids = {str(item) for item in all_existing.get("ids", [])}
        extra_ids = sorted(all_ids - set(chunk_ids))
        if extra_ids:
            for ids in batch_items(extra_ids, 512):
                collection.delete(ids=ids)
            stale += len(extra_ids)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"failed to reconcile existing Chroma collection ids: {exc}") from exc

    for group in batch_items(changed, 32):
        texts = [str(chunk["text"]) for chunk in group]
        vectors = embedder.encode(texts)
        ids = [str(chunk["id"]) for chunk in group]
        metadatas = [scalar_metadata(chunk, hashes[str(chunk["id"])]) for chunk in group]
        collection.upsert(ids=ids, embeddings=vectors, documents=texts, metadatas=metadatas)

    return "chroma", len(changed), stale


def index_with_json_store(
    index_dir: Path,
    chunks: list[dict[str, Any]],
    hashes: dict[str, str],
    embedder: Embedder,
    old_manifest: dict[str, Any] | None,
    force_all: bool,
) -> tuple[str, int, int]:
    index_dir.mkdir(parents=True, exist_ok=True)
    store_path = index_dir / "index.json"
    old_store = read_json(store_path, default={})
    old_records = old_store.get("records", []) if isinstance(old_store, dict) else []
    vectors: dict[str, list[float]] = {}
    if isinstance(old_records, list):
        for rec in old_records:
            if isinstance(rec, dict) and isinstance(rec.get("id"), str) and isinstance(rec.get("embedding"), list):
                vectors[rec["id"]] = [float(x) for x in rec["embedding"]]

    old_hashes = old_manifest.get("chunk_hashes", {}) if isinstance(old_manifest, dict) else {}
    if not isinstance(old_hashes, dict):
        old_hashes = {}
    chunk_by_id = {str(chunk["id"]): chunk for chunk in chunks}
    changed_ids = [
        chunk_id for chunk_id, chunk in chunk_by_id.items()
        if force_all or old_hashes.get(chunk_id) != hashes[chunk_id] or chunk_id not in vectors
    ]
    changed_chunks = [chunk_by_id[chunk_id] for chunk_id in sorted(changed_ids)]
    stale_ids = sorted(set(vectors) - set(chunk_by_id))
    for stale in stale_ids:
        vectors.pop(stale, None)

    for group in batch_items(changed_chunks, 64):
        texts = [str(chunk["text"]) for chunk in group]
        embeddings = embedder.encode(texts)
        for chunk, embedding in zip(group, embeddings, strict=True):
            vectors[str(chunk["id"])] = embedding

    records = []
    for chunk_id in sorted(chunk_by_id):
        chunk = chunk_by_id[chunk_id]
        records.append({
            "id": chunk_id,
            "embedding": vectors[chunk_id],
            "text": str(chunk["text"]),
            "metadata": scalar_metadata(chunk, hashes[chunk_id]),
        })
    write_json(store_path, {"collection": COLLECTION_NAME, "records": records})
    return "json", len(changed_chunks), len(stale_ids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="index", description="embed chunks and build the Chroma vector index")
    parser.add_argument("paths", nargs="*", help="reserved for future explicit chunk files")
    parser.add_argument(
        "--dev-hash-embeddings",
        action="store_true",
        help="explicit sandbox/dev mode: permit EMBED_MODEL=hash:<name> instead of sentence-transformers",
    )
    parser.add_argument(
        "--dev-json-store",
        action="store_true",
        help="explicit sandbox/dev mode: write a deterministic JSON vector store if Chroma is unavailable",
    )
    args = parser.parse_args(argv)
    if args.paths:
        print("error: index does not accept positional paths yet; use WORK_DIR/chunks.jsonl", file=sys.stderr)
        return 2

    work_dir = env_path("WORK_DIR", "work")
    chunks_path = work_dir / "chunks.jsonl"
    manifest_path = work_dir / "index-manifest.json"
    index_dir = work_dir / "index"
    model = os.environ.get("EMBED_MODEL", DEFAULT_MODEL)
    requested_backend = os.environ.get("AI_BACKEND", "cpu")

    try:
        chunks = read_jsonl(chunks_path)
        validate_chunks(chunks, chunks_path)
        chunks.sort(key=lambda rec: (str(rec.get("document_id", "")), int(rec.get("ordinal", 0)), str(rec.get("id", ""))))
        hashes = {str(chunk["id"]): chunk_hash(chunk) for chunk in chunks}
        old_manifest = read_json(manifest_path, default=None)
        allow_hash_embeddings = args.dev_hash_embeddings or env_flag_any("AI_BRIEF_ALLOW_HASH_FALLBACK", "AI_BRIEF_ALLOW_HASH_EMBEDDINGS")
        allow_json_store = args.dev_json_store or env_flag_any("AI_BRIEF_ALLOW_JSON_FALLBACK", "AI_BRIEF_ALLOW_JSON_STORE")
        embedder, actual_backend = make_embedder(model, requested_backend, allow_hash_embeddings)

        old_engine = old_manifest.get("embedding_engine") if isinstance(old_manifest, dict) else None
        old_model = old_manifest.get("model") if isinstance(old_manifest, dict) else None
        old_dimension = old_manifest.get("dimension") if isinstance(old_manifest, dict) else None
        force_all = old_engine != embedder.engine or old_model != model or old_dimension != embedder.dimension

        try:
            store, changed, stale = index_with_chroma(index_dir, chunks, hashes, embedder, old_manifest, force_all)
        except Exception as exc:  # noqa: BLE001
            if not allow_json_store:
                raise RuntimeError(
                    f"failed to build required Chroma index at {index_dir}: {exc}. "
                    "Activate the composed Flox environment with chromadb available and writable WORK_DIR. "
                    "The pipeline no longer falls back to a JSON vector store by default. "
                    "For sandbox-only smoke tests, set AI_BRIEF_ALLOW_JSON_FALLBACK=1 or AI_BRIEF_ALLOW_JSON_STORE=1, or pass --dev-json-store."
                ) from exc
            warn(f"index: explicit development mode enabled: Chroma unavailable or failed ({exc}); writing deterministic JSON vector store")
            store, changed, stale = index_with_json_store(index_dir, chunks, hashes, embedder, old_manifest, force_all)

        fingerprint = sha256_json({
            "model": model,
            "engine": embedder.engine,
            "dimension": embedder.dimension,
            "backend": actual_backend,
            "store": store,
            "chunk_hashes": hashes,
            "dev_hash_embeddings": embedder.engine == "hash",
            "dev_json_store": store == "json",
        })
        generated_at = old_manifest.get("generated_at") if isinstance(old_manifest, dict) and old_manifest.get("fingerprint") == fingerprint else None
        manifest = {
            "model": model,
            "dimension": embedder.dimension,
            "backend": actual_backend,
            "requested_backend": requested_backend,
            "embedding_engine": embedder.engine,
            "store": store,
            "collection": COLLECTION_NAME,
            "chunk_count": len(chunks),
            "generated_at": generated_at or utc_now_iso(),
            "fingerprint": fingerprint,
            "chunk_hashes": dict(sorted(hashes.items())),
            "dev_mode": {
                "hash_embeddings": embedder.engine == "hash",
                "json_store": store == "json",
            },
        }
        write_json(manifest_path, manifest)
        print(
            f"index: {store} store ready at {index_dir} "
            f"({len(chunks)} chunk(s), {changed} embedded/upserted, {stale} stale removed, engine={embedder.engine})"
        )
        return 0
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"error: unexpected index failure: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
