"""Publication Activity invariants."""
from __future__ import annotations

import unittest
from unittest import mock

from temporalio.exceptions import ApplicationError

from orchestrator import activities


class PublicationActivityTest(unittest.IsolatedAsyncioTestCase):
    async def test_failed_publish_verification_surfaces_without_store_mutation(self):
        with (
            mock.patch.object(activities, "_verify_artifact_opens", return_value=False),
            mock.patch.object(activities.store, "advance_current") as advance_current,
            mock.patch.object(activities.store, "record_publication") as record_publication,
        ):
            with self.assertRaises(ApplicationError):
                await activities.verify_artifact_activity("art_bad")

        advance_current.assert_not_called()
        record_publication.assert_not_called()

    async def test_successful_verification_returns_a_positive_receipt(self):
        with mock.patch.object(activities, "_verify_artifact_opens", return_value=True):
            result = await activities.verify_artifact_activity("art_good")
        self.assertEqual(result, {"artifact_digest": "art_good", "verified": True})

    async def test_registration_fence_activity_surfaces_store_conflict_nonretryably(self):
        with mock.patch.object(
            activities.store,
            "fence_generation",
            side_effect=activities.store.PublicationGenerationConflict("conflict"),
        ):
            with self.assertRaises(ApplicationError) as raised:
                await activities.fence_generation_activity(
                    {"candidate_id": "cand_A", "generation": 1}
                )
        self.assertTrue(raised.exception.non_retryable)

    async def test_promote_uses_generation_guard_and_records_canonical_entry(self):
        request = {
            "candidate_id": "cand_good",
            "generation": 2,
            "artifact_digest": "art_good",
            "version": "0.1.0-good",
            "snapshot_id": "snap_good",
            "preverified": True,
        }

        def canonical(entry):
            self.assertTrue(entry["verified"])
            return dict(entry)

        with (
            mock.patch.object(
                activities.store, "advance_current", side_effect=canonical
            ) as advance_current,
            mock.patch.object(activities.store, "record_publication") as record_publication,
        ):
            result = await activities.promote_activity(request)

        advance_current.assert_called_once()
        record_publication.assert_called_once_with(result)
        self.assertTrue(result["verified"])

    async def test_legacy_promote_still_gates_failed_verification(self):
        request = {
            "candidate_id": "cand_legacy",
            "generation": 1,
            "artifact_digest": "art_bad",
            "version": "0.1.0-legacy",
        }
        with (
            mock.patch.object(activities, "_verify_artifact_opens", return_value=False),
            mock.patch.object(activities.store, "advance_current") as advance_current,
            mock.patch.object(activities.store, "record_publication") as record_publication,
        ):
            with self.assertRaises(ApplicationError) as raised:
                await activities.promote_activity(request)
        self.assertTrue(raised.exception.non_retryable)
        advance_current.assert_not_called()
        record_publication.assert_not_called()

    async def test_legacy_promote_migrates_fence_only_when_still_desired(self):
        request = {
            "candidate_id": "cand_legacy",
            "generation": 7,
            "artifact_digest": "art_good",
            "version": "0.1.0-legacy",
        }
        status = {
            "desired_generation": 7,
            "registrations": [[6, "cand_old"], [7, "cand_legacy"]],
        }
        with (
            mock.patch.object(activities, "_verify_artifact_opens", return_value=True),
            mock.patch.object(activities, "_authority_status", return_value=status) as authority_status,
            mock.patch.object(
                activities.store,
                "fence_generation",
                return_value={"generation": 7, "candidate_id": "cand_legacy"},
            ) as fence_generation,
            mock.patch.object(activities.store, "advance_current", side_effect=lambda entry: entry),
            mock.patch.object(activities.store, "record_publication"),
        ):
            await activities.promote_activity(request)

        authority_status.assert_awaited_once_with()
        fence_generation.assert_called_once_with(7, "cand_legacy")

    async def test_legacy_promote_rejects_if_newer_generation_is_now_desired(self):
        request = {
            "candidate_id": "cand_legacy",
            "generation": 7,
            "artifact_digest": "art_good",
            "version": "0.1.0-legacy",
        }
        status = {
            "desired_generation": 8,
            "registrations": [[7, "cand_legacy"], [8, "cand_new"]],
        }
        with (
            mock.patch.object(activities, "_verify_artifact_opens", return_value=True),
            mock.patch.object(activities, "_authority_status", return_value=status),
            mock.patch.object(activities.store, "fence_generation") as fence_generation,
            mock.patch.object(activities.store, "advance_current") as advance_current,
            mock.patch.object(activities.store, "record_publication") as record_publication,
        ):
            result = await activities.promote_activity(request)

        self.assertEqual(result["promoted"], False)
        self.assertEqual(result["reason"], "superseded")
        fence_generation.assert_not_called()
        advance_current.assert_not_called()
        record_publication.assert_not_called()

    async def test_promote_surfaces_stale_fence_nonretryably_without_record(self):
        request = {
            "candidate_id": "cand_old",
            "generation": 1,
            "artifact_digest": "art_old",
            "version": "0.1.0-old",
            "preverified": True,
        }
        with (
            mock.patch.object(
                activities.store,
                "advance_current",
                side_effect=activities.store.StalePublicationError("superseded"),
            ),
            mock.patch.object(activities.store, "record_publication") as record_publication,
        ):
            with self.assertRaises(ApplicationError) as raised:
                await activities.promote_activity(request)
        self.assertTrue(raised.exception.non_retryable)
        record_publication.assert_not_called()


if __name__ == "__main__":
    unittest.main()
