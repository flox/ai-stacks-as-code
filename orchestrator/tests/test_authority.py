"""Publication generation + supersession invariants (§8, §19.13-14)."""
from __future__ import annotations

import unittest

from orchestrator import authority
from orchestrator.authority import AuthorityState


class AuthorityPolicyTest(unittest.TestCase):
    def test_register_assigns_increasing_generations(self):
        s = AuthorityState()
        s, g1 = authority.register(s, "cand_A")
        s, g2 = authority.register(s, "cand_B")
        self.assertEqual((g1, g2), (1, 2))
        self.assertEqual(s.desired_generation, 2)

    def test_superseded_candidate_cannot_publish_even_if_it_finishes_last(self):
        # A registered first, then B supersedes it; A finishes last.
        s = AuthorityState()
        s, gen_a = authority.register(s, "cand_A")
        s, gen_b = authority.register(s, "cand_B")

        # A reaches its commit boundary last — must be denied as superseded.
        dec_a = authority.evaluate_request(s, "cand_A", gen_a)
        self.assertFalse(dec_a.granted)
        self.assertEqual(dec_a.reason, "superseded")

        # B can publish once its gates pass.
        dec_b = authority.evaluate_request(s, "cand_B", gen_b)
        self.assertTrue(dec_b.granted)
        s = authority.commit_publication(s, "cand_B", gen_b, "art_b")
        self.assertEqual(s.published_candidate, "cand_B")
        self.assertEqual(s.published_generation, gen_b)

        # A retrying after B is live is still denied.
        self.assertFalse(authority.evaluate_request(s, "cand_A", gen_a).granted)

    def test_older_can_publish_then_newer_supersedes(self):
        s = AuthorityState()
        s, gen_a = authority.register(s, "cand_A")
        dec_a = authority.evaluate_request(s, "cand_A", gen_a)
        self.assertTrue(dec_a.granted)
        s = authority.commit_publication(s, "cand_A", gen_a, "art_a")

        s, gen_b = authority.register(s, "cand_B")
        dec_b = authority.evaluate_request(s, "cand_B", gen_b)
        self.assertTrue(dec_b.granted)
        s = authority.commit_publication(s, "cand_B", gen_b, "art_b")

        # A is now stale/superseded and cannot reclaim current.
        self.assertFalse(authority.evaluate_request(s, "cand_A", gen_a).granted)
        self.assertEqual(s.published_candidate, "cand_B")

    def test_authorize_is_idempotent_for_published_candidate(self):
        s = AuthorityState()
        s, gen = authority.register(s, "cand_A")
        self.assertTrue(authority.evaluate_request(s, "cand_A", gen).granted)
        s = authority.commit_publication(s, "cand_A", gen, "art_a")

        again = authority.evaluate_request(s, "cand_A", gen)
        self.assertTrue(again.granted)
        self.assertTrue(again.already_published)  # no-op grant, safe publish retry

    def test_stale_generation_denied(self):
        s = AuthorityState()
        s, gen = authority.register(s, "cand_A")
        s = authority.commit_publication(s, "cand_A", gen, "art_a")
        # A different candidate claiming the already-published generation loses.
        self.assertFalse(authority.evaluate_request(s, "cand_X", gen).granted)

    def test_commit_rejects_superseded_generation(self):
        s = AuthorityState()
        s, gen_a = authority.register(s, "cand_A")
        s, _gen_b = authority.register(s, "cand_B")
        with self.assertRaisesRegex(ValueError, "superseded"):
            authority.commit_publication(s, "cand_A", gen_a, "art_a")

    def test_idempotent_commit_cannot_change_artifact(self):
        s = AuthorityState()
        s, gen = authority.register(s, "cand_A")
        s = authority.commit_publication(s, "cand_A", gen, "art_a")
        self.assertIs(authority.commit_publication(s, "cand_A", gen, "art_a"), s)
        with self.assertRaisesRegex(ValueError, "cannot change artifact"):
            authority.commit_publication(s, "cand_A", gen, "art_other")


if __name__ == "__main__":
    unittest.main()
