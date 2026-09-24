"""Candidate identity invariants (§2.2, §19.1-3)."""
from __future__ import annotations

import unittest

from orchestrator import candidate


def _sources():
    return [
        {"name": "docs", "snapshot_id": "src_aaaa", "manifest_digest": "d1"},
        {"name": "blog", "snapshot_id": "src_bbbb", "manifest_digest": "d2"},
    ]


def _spec(**over):
    kwargs = dict(sources=_sources(), pipeline_code_revision="code_v1")
    kwargs.update(over)
    return candidate.build_spec(**kwargs)


class CandidateIdentityTest(unittest.TestCase):
    def test_stable_for_identical_inputs(self):
        self.assertEqual(candidate.candidate_id(_spec()), candidate.candidate_id(_spec()))

    def test_stable_regardless_of_source_order(self):
        a = candidate.build_spec(sources=_sources(), pipeline_code_revision="code_v1")
        b = candidate.build_spec(sources=list(reversed(_sources())), pipeline_code_revision="code_v1")
        self.assertEqual(candidate.candidate_id(a), candidate.candidate_id(b))

    def test_changing_source_digest_changes_id(self):
        other = _sources()
        other[0]["manifest_digest"] = "CHANGED"
        self.assertNotEqual(
            candidate.candidate_id(_spec()),
            candidate.candidate_id(_spec(sources=other)),
        )

    def test_changing_pipeline_code_changes_id(self):
        self.assertNotEqual(
            candidate.candidate_id(_spec()),
            candidate.candidate_id(_spec(pipeline_code_revision="code_v2")),
        )

    def test_changing_chunker_changes_id(self):
        self.assertNotEqual(
            candidate.candidate_id(_spec()),
            candidate.candidate_id(_spec(chunker={"chunker": "semchunk", "target_tokens": 999, "max_tokens": 1000})),
        )

    def test_changing_embedding_model_changes_id(self):
        self.assertNotEqual(
            candidate.candidate_id(_spec()),
            candidate.candidate_id(_spec(embedding={"engine": "onnx", "model": "other", "dimension": 384})),
        )

    def test_incidental_metadata_does_not_change_id(self):
        # Provenance may carry timestamps/run-ids/backend, but the id is a
        # function of the identity spec alone.
        spec = _spec()
        p1 = candidate.provenance(spec, excluded={"generated_at": "t1", "run_id": "r1", "backend": "cpu"})
        p2 = candidate.provenance(spec, excluded={"generated_at": "t2", "run_id": "r2", "backend": "cuda"})
        self.assertEqual(p1["candidate_id"], p2["candidate_id"])


if __name__ == "__main__":
    unittest.main()
