"""Artifact store invariants (§9, §19.15-16)."""
from __future__ import annotations

import concurrent.futures
import os
import tempfile
import threading
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
        store.fence_generation(3, "cand_A")
        store.advance_current(entry)
        self.assertEqual(store.read_current(), entry)

    def test_current_pointer_rejects_generation_rollback(self):
        newer = {"generation": 2, "candidate_id": "cand_NEW", "artifact_digest": "art_new"}
        older = {"generation": 1, "candidate_id": "cand_OLD", "artifact_digest": "art_old"}
        store.fence_generation(2, "cand_NEW")
        store.advance_current(newer)
        with self.assertRaises(store.StalePublicationError):
            store.advance_current(older)
        self.assertEqual(store.read_current(), newer)

    def test_new_registration_fence_blocks_late_older_promotion_before_newer_publish(self):
        old = {"generation": 1, "candidate_id": "cand_OLD", "artifact_digest": "art_old"}
        self.assertEqual(
            store.fence_generation(1, "cand_OLD"),
            {"generation": 1, "candidate_id": "cand_OLD"},
        )
        self.assertEqual(
            store.fence_generation(2, "cand_NEW"),
            {"generation": 2, "candidate_id": "cand_NEW"},
        )

        # Model an orphaned generation-1 Activity finally reaching its side
        # effect after generation 2 has registered but before generation 2 has
        # published. This was the hole in the previous bundle.
        with self.assertRaisesRegex(store.StalePublicationError, "superseded"):
            store.advance_current(old)
        self.assertIsNone(store.read_current())

    def test_publication_fence_is_monotonic_and_idempotent(self):
        first = store.fence_generation(2, "cand_NEW")
        self.assertEqual(store.fence_generation(2, "cand_NEW"), first)
        self.assertEqual(store.fence_generation(1, "cand_OLD"), first)
        self.assertEqual(store.read_publication_fence(), first)

        with self.assertRaises(store.PublicationGenerationConflict):
            store.fence_generation(2, "cand_OTHER")

    def test_current_pointer_same_generation_is_idempotent_only_for_same_publication(self):
        first = {
            "generation": 3,
            "candidate_id": "cand_A",
            "artifact_digest": "art_a",
            "published_at": "first",
        }
        retry = {**first, "published_at": "retry"}
        store.fence_generation(3, "cand_A")
        canonical = store.advance_current(first)
        self.assertEqual(store.advance_current(retry), canonical)
        self.assertEqual(store.read_current()["published_at"], "first")

        with self.assertRaises(store.StalePublicationError):
            store.advance_current(
                {"generation": 3, "candidate_id": "cand_B", "artifact_digest": "art_b"}
            )

    def test_concurrent_current_advances_cannot_finish_on_older_generation(self):
        older = {"generation": 1, "candidate_id": "cand_OLD", "artifact_digest": "art_old"}
        newer = {"generation": 2, "candidate_id": "cand_NEW", "artifact_digest": "art_new"}

        # Exercise the real filesystem compare/write path concurrently after the
        # newer registration is durably fenced. A late generation-1 Activity can
        # never become current, regardless of thread scheduling.
        store.fence_generation(2, "cand_NEW")
        for _ in range(20):
            try:
                store.current_path().unlink()
            except FileNotFoundError:
                pass
            barrier = threading.Barrier(3)

            def advance(entry):
                barrier.wait()
                try:
                    store.advance_current(entry)
                except store.StalePublicationError:
                    pass

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(advance, older), pool.submit(advance, newer)]
                barrier.wait()
                for future in futures:
                    future.result()

            self.assertEqual(store.read_current()["generation"], 2)
            self.assertEqual(store.read_current()["candidate_id"], "cand_NEW")

    def test_publication_record_idempotency_anchor(self):
        self.assertIsNone(store.is_published("cand_A"))
        store.record_publication({"candidate_id": "cand_A", "generation": 1, "artifact_digest": "art_a"})
        rec = store.is_published("cand_A")
        self.assertEqual(rec["generation"], 1)


if __name__ == "__main__":
    unittest.main()
