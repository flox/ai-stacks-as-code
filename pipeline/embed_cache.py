"""Content-addressed embedding cache (incremental reuse).

Embedding is the expensive step. This cache lets `index.py` skip re-computing a
vector whenever it has already embedded the *exact same text* with the *same
embedding identity* (engine + model + dimension). It is keyed by
`sha256(text)` under a per-identity namespace, so:

  - a crash-interrupted index resumes without re-embedding what it already did;
  - a later build whose sources barely changed re-embeds only the changed chunks.

Correctness: the key includes the full embedding identity, so a model/engine/
dimension change lands in a different namespace and never returns a stale vector.
Stdlib-only and deterministic; safe to import in the offline pipeline env.
"""
from __future__ import annotations

import array
import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol, Sequence


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


class EmbeddingCache:
    """Filesystem cache of float32 vectors, keyed by text hash under an identity namespace."""

    def __init__(self, base: Path | str, engine: str, model: str, dimension: int) -> None:
        self.dimension = dimension
        self.namespace = _sanitize(f"{engine}__{model}__{dimension}")
        self.root = Path(base) / self.namespace
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, text: str) -> Path:
        digest = _sha256(text)
        return self.root / digest[:2] / f"{digest}.f32"

    def get(self, text: str) -> list[float] | None:
        path = self._path(text)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        vec = array.array("f")
        vec.frombytes(raw)
        if len(vec) != self.dimension:
            return None  # namespace guards identity; guard length defensively too
        return list(vec)

    def put(self, text: str, vector: Sequence[float]) -> None:
        path = self._path(text)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(array.array("f", vector).tobytes())
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)  # atomic; concurrent writers of the same key are harmless
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass


class _Embedder(Protocol):
    engine: str
    dimension: int

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


class CachedEmbedder:
    """Wrap an embedder so encode() serves cache hits and only computes misses."""

    def __init__(self, inner: _Embedder, cache: EmbeddingCache) -> None:
        self._inner = inner
        self._cache = cache
        self.engine = inner.engine
        self.dimension = inner.dimension
        self.model = getattr(inner, "model", "unknown")
        self.hits = 0
        self.misses = 0

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        texts = list(texts)
        out: list[list[float] | None] = [None] * len(texts)
        miss_positions: list[int] = []
        miss_texts: list[str] = []
        for i, text in enumerate(texts):
            cached = self._cache.get(text)
            if cached is not None:
                out[i] = cached
                self.hits += 1
            else:
                miss_positions.append(i)
                miss_texts.append(text)
        if miss_texts:
            computed = self._inner.encode(miss_texts)
            for pos, vec in zip(miss_positions, computed, strict=True):
                out[pos] = [float(x) for x in vec]
                self._cache.put(texts[pos], out[pos])
            self.misses += len(miss_texts)
        return [v if v is not None else [] for v in out]

    @property
    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses}
