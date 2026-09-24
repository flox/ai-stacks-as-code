"""Workflow-level concurrency invariants for publication authority."""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from orchestrator import workflows


class PublicationAuthorityWorkflowConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_new_generation_supersedes_authorization_during_verification(self):
        """A newer registration may interleave with slow verification, but not lose."""
        authority_wf = workflows.PublicationAuthorityWorkflow()

        old_verify_started = asyncio.Event()
        release_old_verify = asyncio.Event()
        calls: list[tuple[str, str]] = []

        async def fake_execute_activity(activity_fn, arg, **_kwargs):
            if activity_fn is workflows.fence_generation_activity:
                return dict(arg)

            if activity_fn is workflows.verify_artifact_activity:
                calls.append(("verify", arg))
                if arg == "art_old":
                    old_verify_started.set()
                    await release_old_verify.wait()
                return {"artifact_digest": arg, "verified": True}

            if activity_fn is workflows.promote_activity:
                calls.append(("promote", arg["candidate_id"]))
                return arg

            self.fail(f"unexpected Activity {activity_fn!r}")

        with (
            mock.patch.object(workflows.workflow, "patched", return_value=True),
            mock.patch.object(workflows.workflow, "execute_activity", new=fake_execute_activity),
        ):
            gen_old = await authority_wf.register("cand_OLD")
            old_task = asyncio.create_task(
                authority_wf.authorize(
                    {
                        "candidate_id": "cand_OLD",
                        "generation": gen_old,
                        "artifact_digest": "art_old",
                        "version": "0.1.0-old",
                    }
                )
            )
            await old_verify_started.wait()

            # Registration persists its generation fence while holding the same
            # workflow lock used at publication commit. Once this returns, even
            # an orphaned old promotion Activity is fenced independently in the
            # store; the live old handler is also denied on its second check.
            gen_new = await authority_wf.register("cand_NEW")
            self.assertEqual(gen_new, gen_old + 1)

            new_decision = await authority_wf.authorize(
                {
                    "candidate_id": "cand_NEW",
                    "generation": gen_new,
                    "artifact_digest": "art_new",
                    "version": "0.1.0-new",
                }
            )
            self.assertTrue(new_decision["granted"])

            release_old_verify.set()
            old_decision = await old_task

        self.assertFalse(old_decision["granted"])
        self.assertEqual(old_decision["reason"], "superseded")
        self.assertEqual(
            calls,
            [
                ("verify", "art_old"),
                ("verify", "art_new"),
                ("promote", "cand_NEW"),
            ],
        )
        self.assertEqual(authority_wf._state.published_generation, gen_new)
        self.assertEqual(authority_wf._state.published_candidate, "cand_NEW")

    async def test_legacy_patch_branch_keeps_original_command_sequence(self):
        authority_wf = workflows.PublicationAuthorityWorkflow()
        calls: list[tuple[object, object, dict]] = []

        async def fake_execute_activity(activity_fn, arg, **kwargs):
            calls.append((activity_fn, arg, kwargs))
            return {"published": True}

        with (
            mock.patch.object(workflows.workflow, "patched", return_value=False),
            mock.patch.object(workflows.workflow, "execute_activity", new=fake_execute_activity),
        ):
            generation = await authority_wf.register("cand_legacy")
            self.assertEqual(calls, [])  # legacy register emitted no Activity
            decision = await authority_wf.authorize(
                {
                    "candidate_id": "cand_legacy",
                    "generation": generation,
                    "artifact_digest": "art_legacy",
                    "version": "0.1.0-legacy",
                }
            )

        self.assertTrue(decision["granted"])
        self.assertEqual(len(calls), 1)
        activity_fn, request, kwargs = calls[0]
        self.assertIs(activity_fn, workflows.promote_activity)
        self.assertNotIn("preverified", request)
        self.assertEqual(kwargs["start_to_close_timeout"].total_seconds(), 300)
        self.assertEqual(authority_wf._state.published_generation, generation)

    async def test_legacy_resumed_promotion_can_resolve_as_superseded_without_commit(self):
        authority_wf = workflows.PublicationAuthorityWorkflow()

        async def fake_execute_activity(activity_fn, arg, **_kwargs):
            self.assertIs(activity_fn, workflows.promote_activity)
            return {
                "promoted": False,
                "reason": "superseded",
                "generation": arg["generation"],
                "candidate_id": arg["candidate_id"],
            }

        with (
            mock.patch.object(workflows.workflow, "patched", return_value=False),
            mock.patch.object(workflows.workflow, "execute_activity", new=fake_execute_activity),
        ):
            generation = await authority_wf.register("cand_legacy")
            decision = await authority_wf.authorize(
                {
                    "candidate_id": "cand_legacy",
                    "generation": generation,
                    "artifact_digest": "art_legacy",
                    "version": "0.1.0-legacy",
                }
            )

        self.assertFalse(decision["granted"])
        self.assertEqual(decision["reason"], "superseded")
        self.assertEqual(authority_wf._state.published_generation, 0)
        self.assertIsNone(authority_wf._state.published_candidate)


if __name__ == "__main__":
    unittest.main()
