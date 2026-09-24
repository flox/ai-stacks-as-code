"""Artifact store invariants (§9, §19.15-16)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from orchestrator import store


class StoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["AI_BRIEF_STORE_DIR"] = self._tmp.name
        self.index = Path(self._tmp.name) / "src-index"
        (self.index / "sub").mkdir(parents=True)
        (self.index / "a.bin").write_bytes(b"hello")
        (self.index / "sub" / "b.bin").write_bytes(b"world")
        self.manifest = Path(self._tmp.name) / "index-manifest.json"
        self.manifest.write_text('{"chunk_count": 2}\n')

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("AI_BRIEF_STORE_DIR", None)

    def test_content_digest_is_deterministic(self):
        d1 = store.content_digest(self.index, [self.manifest])
        d2 = store.content_digest(self.index, [self.manifest])
        self.assertEqual(d1, d2)
        self.assertTrue(d1.startswith("art_"))

    def test_content_digest_changes_with_content(self):
        d1 = store.content_digest(self.index, [self.manifest])
        (self.index / "a.bin").write_bytes(b"HELLO-CHANGED")
        d2 = store.content_digest(self.index, [self.manifest])
        self.assertNotEqual(d1, d2)

    def test_package_is_idempotent(self):
        digest1, path1 = store.package(self.index, [self.manifest])
        mtime1 = path1.stat().st_mtime_ns
        digest2, path2 = store.package(self.index, [self.manifest])
        self.assertEqual(digest1, digest2)
        self.assertEqual(path1, path2)
        # Idempotent: the immutable blob is reused, not rewritten.
        self.assertEqual(mtime1, path2.stat().st_mtime_ns)

    def test_package_roundtrips_via_unpack(self):
        digest, _ = store.package(self.index, [self.manifest])
        dest = Path(self._tmp.name) / "out"
        store.unpack(digest, dest)
        self.assertEqual((dest / "index" / "a.bin").read_bytes(), b"hello")
        self.assertEqual((dest / "index" / "sub" / "b.bin").read_bytes(), b"world")
        self.assertEqual((dest / "index-manifest.json").read_text().strip(), '{"chunk_count": 2}')

    def test_current_pointer_roundtrip(self):
        self.assertIsNone(store.read_current())
        entry = {"generation": 3, "candidate_id": "cand_A", "artifact_digest": "art_x"}
        store.advance_current(entry)
        self.assertEqual(store.read_current(), entry)

    def test_publication_record_idempotency_anchor(self):
        self.assertIsNone(store.is_published("cand_A"))
        store.record_publication({"candidate_id": "cand_A", "generation": 1, "artifact_digest": "art_a"})
        rec = store.is_published("cand_A")
        self.assertEqual(rec["generation"], 1)


if __name__ == "__main__":
    unittest.main()
