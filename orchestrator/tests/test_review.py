"""Human-review policy invariants (§6, §19.11-12)."""
from __future__ import annotations

import unittest

from orchestrator import review


def _request(candidate_id="cand_A", generation=1):
    return review.build_review_request(
        candidate_id=candidate_id,
        generation=generation,
        snapshot_id="snap_x",
        source_commits={"docs": "abc"},
        report={"ok": True, "checks": []},
        artifact_digest="art_x",
    )


def _payload(req, decision="approve", **over):
    p = {
        "review_request_id": req["review_request_id"],
        "candidate_id": req["candidate_id"],
        "decision": decision,
    }
    p.update(over)
    return p


class ReviewPolicyTest(unittest.TestCase):
    def test_request_id_is_deterministic(self):
        self.assertEqual(
            review.review_request_id("cand_A", 3),
            review.review_request_id("cand_A", 3),
        )

    def test_approve_and_reject_validate(self):
        req = _request()
        review.validate_decision(req, None, _payload(req, "approve"))
        review.validate_decision(req, None, _payload(req, "reject"))

    def test_no_open_review_rejected(self):
        with self.assertRaises(review.ReviewError):
            review.validate_decision(None, None, _payload(_request()))

    def test_bad_decision_value_rejected(self):
        req = _request()
        with self.assertRaises(review.ReviewError):
            review.validate_decision(req, None, _payload(req, "maybe"))

    def test_wrong_candidate_rejected(self):
        req = _request()
        with self.assertRaises(review.ReviewError):
            review.validate_decision(req, None, _payload(req, candidate_id="cand_OTHER"))

    def test_stale_request_id_rejected(self):
        req = _request()
        with self.assertRaises(review.ReviewError):
            review.validate_decision(req, None, _payload(req, review_request_id="rev-stale"))

    def test_duplicate_decision_is_idempotent(self):
        req = _request()
        decided = review.record_decision(_payload(req, "approve"), "2026-01-01T00:00:00Z")
        # Re-submitting the same decision validates (caller returns the recorded one).
        review.validate_decision(req, decided, _payload(req, "approve"))
        self.assertTrue(review.is_idempotent_repeat(_payload(req, "approve"), decided))

    def test_conflicting_decision_after_recorded_rejected(self):
        req = _request()
        decided = review.record_decision(_payload(req, "approve"), "2026-01-01T00:00:00Z")
        with self.assertRaises(review.ReviewError):
            review.validate_decision(req, decided, _payload(req, "reject"))

    def test_record_decision_shape(self):
        req = _request()
        rec = review.record_decision(_payload(req, "approve", reviewer="alice", note="lgtm"),
                                     "2026-01-01T00:00:00Z")
        self.assertEqual(rec["decision"], "approve")
        self.assertEqual(rec["reviewer"], "alice")
        self.assertEqual(rec["candidate_id"], "cand_A")
        self.assertEqual(rec["decided_at"], "2026-01-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
