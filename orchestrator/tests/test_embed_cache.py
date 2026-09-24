"""Embedding cache reuse invariants (Phase 2 incremental reuse, §12)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# embed_cache lives in the pipeline layer (where embedding happens).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))
from embed_cache import CachedEmbedder, EmbeddingCache  # noqa: E402


class _CountingEmbedder:
    """Deterministic fake: records exactly which texts it was asked to compute."""

    engine = "fake"
    model = "fake-model"
    dimension = 3

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(self, texts):
        self.calls.append(list(texts))
        return [[float(len(t)), 1.0, 2.0] for t in texts]

    @property
    def computed(self) -> list[str]:
        return [t for call in self.calls for t in call]


class EmbedCacheTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _cache(self, inner):
        return EmbeddingCache(self.base, inner.engine, inner.model, inner.dimension)

    def test_only_misses_are_computed(self):
        inner = _CountingEmbedder()
        emb = CachedEmbedder(inner, self._cache(inner))

        v1 = emb.encode(["alpha", "beta"])
        self.assertEqual(inner.computed, ["alpha", "beta"])   # both missed first time
        self.assertEqual(emb.stats, {"hits": 0, "misses": 2})

        inner.calls.clear()
        v2 = emb.encode(["alpha", "beta", "gamma"])
        self.assertEqual(inner.computed, ["gamma"])            # only the new text computed
        self.assertEqual(emb.stats, {"hits": 2, "misses": 3})
        # Cached vectors are identical to freshly computed ones.
        self.assertEqual(v2[0], v1[0])
        self.assertEqual(v2[1], v1[1])

    def test_cache_persists_across_instances(self):
        inner1 = _CountingEmbedder()
        CachedEmbedder(inner1, self._cache(inner1)).encode(["alpha"])
        self.assertEqual(inner1.computed, ["alpha"])

        # A brand-new embedder over the same cache dir serves the hit — this is
        # what makes a crash-interrupted index resume without re-embedding.
        inner2 = _CountingEmbedder()
        emb2 = CachedEmbedder(inner2, self._cache(inner2))
        emb2.encode(["alpha"])
        self.assertEqual(inner2.computed, [])                  # nothing recomputed
        self.assertEqual(emb2.stats, {"hits": 1, "misses": 0})

    def test_identity_change_is_a_different_namespace(self):
        inner = _CountingEmbedder()
        CachedEmbedder(inner, self._cache(inner)).encode(["alpha"])

        # A different model id must not return the prior vector.
        other = _CountingEmbedder()
        other.model = "different-model"
        other2 = _CountingEmbedder()
        other2.model = "different-model"
        emb = CachedEmbedder(other2, EmbeddingCache(self.base, other2.engine, other2.model, other2.dimension))
        emb.encode(["alpha"])
        self.assertEqual(other2.computed, ["alpha"])           # recomputed under new identity


if __name__ == "__main__":
    unittest.main()
