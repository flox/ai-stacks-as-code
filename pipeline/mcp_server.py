#!/usr/bin/env python3
"""Ask-Flox MCP server — expose the local retrieval index as an agent tool.

A stdio Model Context Protocol server with one tool, ``search_flox_docs``, that
embeds a question with the same MiniLM model the pipeline indexed with, queries
the persisted ChromaDB ``ai_brief`` collection, and returns the top-k *cited*
passages. Generation stays with the calling agent; this surface is retrieval
only (see NEXT-SESSION.md — retrieval decoupled from generation).

Reuses the verified retrieval path: ``make_query_embedder`` (brief.py) builds the
embedder from the index manifest; Chroma stores the chunk text + source_path, so
the server needs only WORK_DIR/index/ and WORK_DIR/index-manifest.json.

Run inside the composed ai-brief env:  just mcp   (or: python pipeline/mcp_server.py)

CRITICAL: MCP stdio speaks JSON-RPC over STDOUT. Nothing else may touch stdout,
or the protocol corrupts silently. Model/Chroma init is wrapped to redirect any
stray stdout writes to stderr, and telemetry is disabled.
"""
from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import Any

# Silence ChromaDB's telemetry before it is ever imported (keeps stdout clean).
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

# pipeline/ is on sys.path when run as a script; mirror the other stage scripts.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import env_path, read_json  # noqa: E402
from index import COLLECTION_NAME  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP(
    "ask-flox",
    instructions=(
        "Search the indexed Flox documentation and blog posts. Use this to ground "
        "any Flox-specific claim in authoritative, cited source passages instead of "
        "relying on memory. Returns the most relevant chunks, each with its source "
        "file and chunk id for citation."
    ),
)

# Built once, on first use, and cached. A tuple of (embedder, collection) on
# success; a RuntimeError-carrying message drives a friendly tool response.
_RETRIEVER: tuple[Any, Any] | None = None
_RETRIEVER_ERROR: str | None = None


def _build_retriever() -> tuple[Any, Any]:
    """Load the query embedder and open the Chroma collection (heavy; once)."""
    work = env_path("WORK_DIR", "work")
    index_dir = work / "index"
    manifest = read_json(work / "index-manifest.json", default={}) or {}
    if not isinstance(manifest, dict) or not manifest.get("model"):
        raise RuntimeError(
            f"no usable index manifest at {work / 'index-manifest.json'} — build the "
            "index first: flox activate -- scripts/build-ask-flox-index.sh"
        )

    # Import inside the guard so a missing dep yields an actionable message, and
    # so any import-time stdout stays off the protocol stream.
    import chromadb  # type: ignore
    from brief import make_query_embedder

    collection_name = str(manifest.get("collection") or COLLECTION_NAME)
    client = chromadb.PersistentClient(path=str(index_dir))
    try:
        collection = client.get_collection(name=collection_name)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Chroma collection {collection_name!r} not found at {index_dir} — build the "
            f"index first: flox activate -- scripts/build-ask-flox-index.sh ({exc})"
        ) from exc
    if int(collection.count()) < 1:
        raise RuntimeError(
            f"Chroma collection {collection_name!r} is empty — run the index build."
        )
    # A hash-embedded index is a dev artifact; allow it so local sandboxes work.
    embedder = make_query_embedder(manifest, allow_hash_embeddings=True)
    return embedder, collection


def _get_retriever() -> tuple[Any, Any] | None:
    """Return the cached (embedder, collection), or None if init failed."""
    global _RETRIEVER, _RETRIEVER_ERROR
    if _RETRIEVER is not None:
        return _RETRIEVER
    if _RETRIEVER_ERROR is not None:
        return None
    try:
        # Redirect any stray stdout (model download bars, etc.) to stderr so the
        # JSON-RPC channel stays pristine.
        with contextlib.redirect_stdout(sys.stderr):
            _RETRIEVER = _build_retriever()
        return _RETRIEVER
    except Exception as exc:  # noqa: BLE001
        _RETRIEVER_ERROR = str(exc)
        return None


@mcp.tool()
def search_flox_docs(query: str, k: int = 5) -> str:
    """Search the Flox docs/blog index for passages relevant to a question.

    Args:
        query: A natural-language question or topic about Flox.
        k: How many passages to return (1-20, default 5).

    Returns:
        The top matches, each with source file, similarity score, and chunk id
        for citation. If the index is not built, an actionable message is returned.
    """
    query = (query or "").strip()
    if not query:
        return "error: empty query — pass a natural-language question about Flox."
    try:
        k = int(k)
    except (TypeError, ValueError):
        k = 5
    k = max(1, min(k, 20))

    retriever = _get_retriever()
    if retriever is None:
        return f"error: retrieval unavailable — {_RETRIEVER_ERROR}"
    embedder, collection = retriever

    with contextlib.redirect_stdout(sys.stderr):
        vectors = embedder.encode([query])
        result = collection.query(
            query_embeddings=vectors,
            n_results=k,
            include=["documents", "metadatas", "distances"],
        )

    ids = (result.get("ids") or [[]])[0]
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]
    if not ids:
        return f'No passages found for "{query}".'

    lines = [f'Top {len(ids)} passage(s) for "{query}":', ""]
    for rank, (cid, doc, meta, dist) in enumerate(zip(ids, docs, metas, dists), start=1):
        meta = meta if isinstance(meta, dict) else {}
        source = str(meta.get("source_path") or "unknown-source")
        # Cosine distance -> similarity in [0, 1]; clamp for display sanity.
        score = max(0.0, min(1.0, 1.0 - float(dist)))
        text = " ".join(str(doc).split())  # normalize whitespace for readability
        lines.append(f"[{rank}] {source}  (score {score:.3f})  id={cid}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip()


if __name__ == "__main__":
    mcp.run()  # stdio transport by default
