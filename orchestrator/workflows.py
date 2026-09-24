"""Temporal workflows — durable orchestration + the single publication authority.

Two workflows:

* ``PublicationAuthorityWorkflow`` — a singleton (stable Workflow ID) that is the
  ONE authority for publication ordering (§2.3, §8). Its async Update handlers
  use a workflow-local lock for generation mutation and the final publication
  commit, while slow verification may interleave safely. It is also the only
  logical writer of the ``current`` pointer (via ``promote_activity``).

* ``BuildWorkflow`` — the per-candidate lifecycle: snapshot -> identity ->
  register intent -> ingest -> index -> package -> evaluate -> request
  authorization -> (published | superseded | rejected). All side effects are
  activities; this code stays deterministic and replay-safe.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from . import authority, config, review
from .activities import (
    compute_candidate_activity,
    evaluate_activity,
    existing_artifact_activity,
    fence_generation_activity,
    hydrate_activity,
    index_activity,
    ingest_activity,
    is_published_activity,
    package_activity,
    promote_activity,
    register_generation_activity,
    request_authorization_activity,
    resolve_snapshot_activity,
    verify_artifact_activity,
)

# Bound authority history: Continue-As-New after this many handled updates.
_CONTINUE_AFTER_UPDATES = 500

# Workflow command-sequence version. Histories created before this patch replay
# the legacy handlers; the first live post-upgrade invocation records the marker
# and takes the fenced/verified path. Keep until all pre-patch histories are gone.
_PUBLICATION_AUTHORITY_PATCH = "publication-authority-fence-v1"

_DEFAULT_RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=1))


# --- authority state (de)serialization for Continue-As-New ------------------

def _state_to_dict(state: authority.AuthorityState) -> dict[str, Any]:
    return {
        "desired_generation": state.desired_generation,
        "published_generation": state.published_generation,
        "published_candidate": state.published_candidate,
        "published_artifact": state.published_artifact,
        "registrations": [list(r) for r in state.registrations],
    }


def _state_from_dict(data: dict[str, Any]) -> authority.AuthorityState:
    return authority.AuthorityState(
        desired_generation=data.get("desired_generation", 0),
        published_generation=data.get("published_generation", 0),
        published_candidate=data.get("published_candidate"),
        published_artifact=data.get("published_artifact"),
        registrations=tuple(tuple(r) for r in data.get("registrations", [])),
    )


def _decision_to_dict(d: authority.Decision) -> dict[str, Any]:
    return {
        "granted": d.granted,
        "reason": d.reason,
        "generation": d.generation,
        "published_generation": d.published_generation,
        "published_candidate": d.published_candidate,
    }


@workflow.defn
class PublicationAuthorityWorkflow:
    def __init__(self) -> None:
        self._state = authority.AuthorityState()
        self._handled = 0
        # Async Update handlers run concurrently and may interleave at awaits.
        # Serialize state mutation and the final current-pointer swap; slow
        # artifact verification deliberately happens outside this lock.
        self._publication_lock = asyncio.Lock()

    @workflow.run
    async def run(self, carry: dict[str, Any] | None = None) -> dict[str, Any]:
        if carry:
            self._state = _state_from_dict(carry)
        # Live forever handling updates; recycle history periodically.
        await workflow.wait_condition(
            lambda: self._handled >= _CONTINUE_AFTER_UPDATES and workflow.all_handlers_finished()
        )
        workflow.continue_as_new(_state_to_dict(self._state))

    @workflow.update
    async def register(self, candidate_id: str) -> int:
        """Register and durably fence a new publication intent."""
        if not workflow.patched(_PUBLICATION_AUTHORITY_PATCH):
            # Replay compatibility for histories produced before the fencing
            # change. This branch emits the exact legacy command sequence.
            self._state, generation = authority.register(self._state, candidate_id)
            self._handled += 1
            return generation

        # State advances before the Activity is scheduled, so even a failed
        # fence attempt consumes its generation. The lock stays held until the
        # durable fence is confirmed, which makes registration and publication
        # linearizable at the external store boundary.
        async with self._publication_lock:
            self._state, generation = authority.register(self._state, candidate_id)
            self._handled += 1
            await workflow.execute_activity(
                fence_generation_activity,
                {"candidate_id": candidate_id, "generation": generation},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_DEFAULT_RETRY,
            )
            return generation

    @register.validator
    def _validate_register(self, candidate_id: str) -> None:
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError("candidate_id must be a non-empty string")

    @workflow.update
    async def authorize(self, request: dict[str, Any]) -> dict[str, Any]:
        """Grant or deny publication for a candidate/generation (§8).

        Slow verification runs outside the publication lock. The request is
        re-evaluated after verification, immediately before the pointer swap.
        """
        candidate_id = request["candidate_id"]
        generation = request["generation"]

        if not workflow.patched(_PUBLICATION_AUTHORITY_PATCH):
            # Exact legacy Workflow command sequence for replay of pre-patch
            # histories. `promote_activity` retains a legacy verification and
            # authority re-check for an old Activity resumed after an upgrade.
            decision = authority.evaluate_request(self._state, candidate_id, generation)
            if decision.granted and not decision.already_published:
                promotion = await workflow.execute_activity(
                    promote_activity,
                    {
                        "candidate_id": candidate_id,
                        "generation": generation,
                        "artifact_digest": request["artifact_digest"],
                        "version": request["version"],
                        "snapshot_id": request.get("snapshot_id"),
                    },
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=_DEFAULT_RETRY,
                )
                if isinstance(promotion, dict) and promotion.get("promoted") is False:
                    decision = authority.Decision(
                        False,
                        str(promotion.get("reason", "superseded")),
                        generation,
                        self._state.published_generation,
                        self._state.published_candidate,
                    )
                else:
                    self._state = authority.commit_publication_legacy(
                        self._state, candidate_id, generation, request["artifact_digest"]
                    )
            self._handled += 1
            return _decision_to_dict(decision)

        async with self._publication_lock:
            decision = authority.evaluate_request(self._state, candidate_id, generation)
            if not decision.granted or decision.already_published:
                self._handled += 1
                return _decision_to_dict(decision)
            # Establish/refresh the fence here too so a generation registered by
            # a pre-patch worker can migrate safely when it later authorizes.
            await workflow.execute_activity(
                fence_generation_activity,
                {"candidate_id": candidate_id, "generation": generation},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_DEFAULT_RETRY,
            )

        await workflow.execute_activity(
            verify_artifact_activity,
            request["artifact_digest"],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_DEFAULT_RETRY,
        )

        async with self._publication_lock:
            # Registration and other authorizations may have run while the
            # immutable artifact was being verified. Re-check freshness at the
            # actual commit boundary before any current-pointer mutation.
            decision = authority.evaluate_request(self._state, candidate_id, generation)
            if decision.granted and not decision.already_published:
                await workflow.execute_activity(
                    promote_activity,
                    {
                        "candidate_id": candidate_id,
                        "generation": generation,
                        "artifact_digest": request["artifact_digest"],
                        "version": request["version"],
                        "snapshot_id": request.get("snapshot_id"),
                        "preverified": True,
                    },
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=_DEFAULT_RETRY,
                )
                self._state = authority.commit_publication(
                    self._state, candidate_id, generation, request["artifact_digest"]
                )
            self._handled += 1
            return _decision_to_dict(decision)

    @authorize.validator
    def _validate_authorize(self, request: dict[str, Any]) -> None:
        for key in ("candidate_id", "generation", "artifact_digest", "version"):
            if key not in request:
                raise ValueError(f"authorize request missing {key!r}")

    @workflow.query
    def status(self) -> dict[str, Any]:
        return _state_to_dict(self._state)


@workflow.defn
class BuildWorkflow:
    def __init__(self) -> None:
        self._status: dict[str, Any] = {
            "stage": "created",
            "candidate_id": None,
            "generation": None,
            "snapshot_id": None,
            "source_commits": None,
            "artifact_digest": None,
            "reused": None,
            "embed_cache": None,
            "review": None,
            "review_decision": None,
            "published": None,
            "reason": None,
        }
        self._review: dict[str, Any] | None = None
        self._review_decision: dict[str, Any] | None = None

    def _stage(self, stage: str) -> None:
        self._status["stage"] = stage
        workflow.logger.info("build stage -> %s", stage)

    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        self._stage("registered")

        # 1. Immutable source snapshot (§2.1).
        self._stage("snapshotting")
        snap = await workflow.execute_activity(
            resolve_snapshot_activity, request,
            start_to_close_timeout=timedelta(minutes=10), retry_policy=_DEFAULT_RETRY,
        )
        self._status["snapshot_id"] = snap["snapshot_id"]
        self._status["source_commits"] = snap.get("commits")

        # 2. Candidate identity (§2.2).
        cand = await workflow.execute_activity(
            compute_candidate_activity, {"snapshot": snap},
            start_to_close_timeout=timedelta(minutes=2), retry_policy=_DEFAULT_RETRY,
        )
        candidate_id = cand["candidate_id"]
        self._status["candidate_id"] = candidate_id

        # 3. Register publication intent (ordering authority, §2.3).
        generation = await workflow.execute_activity(
            register_generation_activity, candidate_id,
            start_to_close_timeout=timedelta(seconds=30), retry_policy=_DEFAULT_RETRY,
        )
        self._status["generation"] = generation

        # 4. Candidate-level reuse (§12): identical inputs => identical candidate
        # id => reuse the immutable artifact wholesale, skipping the expensive
        # ingest/index/package. Otherwise build it.
        existing = await workflow.execute_activity(
            existing_artifact_activity, candidate_id,
            start_to_close_timeout=timedelta(seconds=30), retry_policy=_DEFAULT_RETRY,
        )
        args = {"candidate_id": candidate_id, "content_root": snap["content_root"]}
        if existing:
            self._stage("reusing")
            self._status["reused"] = True
            artifact_digest = existing["artifact_digest"]
            await workflow.execute_activity(
                hydrate_activity,
                {"candidate_id": candidate_id, "artifact_digest": artifact_digest},
                start_to_close_timeout=timedelta(minutes=5), retry_policy=_DEFAULT_RETRY,
            )
        else:
            self._status["reused"] = False
            # ingest -> index. Both heartbeat, so a worker crash is detected within
            # heartbeat_timeout and the stage resumes on the next worker. The index
            # stage reuses already-embedded chunks via the content-addressed cache.
            self._stage("processing")
            await workflow.execute_activity(
                ingest_activity, args,
                start_to_close_timeout=timedelta(minutes=15),
                heartbeat_timeout=timedelta(seconds=30), retry_policy=_DEFAULT_RETRY,
            )
            self._stage("indexing")
            index_result = await workflow.execute_activity(
                index_activity, args,
                start_to_close_timeout=timedelta(minutes=45),
                heartbeat_timeout=timedelta(seconds=30), retry_policy=_DEFAULT_RETRY,
            )
            self._status["embed_cache"] = index_result.get("embed_cache")
            # Package an immutable, content-addressed candidate artifact (§9).
            self._stage("packaging")
            pkg = await workflow.execute_activity(
                package_activity,
                {"candidate_id": candidate_id, "provenance": cand["provenance"], "snapshot": snap["manifest"]},
                start_to_close_timeout=timedelta(minutes=10), retry_policy=_DEFAULT_RETRY,
            )
            artifact_digest = pkg["artifact_digest"]
        self._status["artifact_digest"] = artifact_digest

        # 7. Deterministic evaluation gate (§11).
        self._stage("evaluating")
        report = await workflow.execute_activity(
            evaluate_activity, {"candidate_id": candidate_id, "snapshot": snap["manifest"]},
            start_to_close_timeout=timedelta(minutes=10), retry_policy=_DEFAULT_RETRY,
        )
        if not report.get("ok"):
            self._stage("rejected")
            self._status.update(published=False, reason="evaluation-failed")
            return {**self._status, "report": report}

        # 7b. Durable human review (§6), when policy requires it. The workflow
        # waits durably (surviving worker restarts) for a validated Update; an
        # approval does NOT override supersession — the authority is still
        # consulted below, so an approved-but-superseded candidate can't publish.
        if request.get("require_review"):
            self._review = review.build_review_request(
                candidate_id=candidate_id,
                generation=generation,
                snapshot_id=snap["snapshot_id"],
                source_commits=snap.get("commits"),
                report=report,
                artifact_digest=artifact_digest,
            )
            self._status["review"] = self._review
            self._stage("awaiting_review")
            await workflow.wait_condition(lambda: self._review_decision is not None)
            self._status["review_decision"] = self._review_decision
            if self._review_decision["decision"] == "reject":
                self._stage("rejected")
                self._status.update(published=False, reason="rejected-by-review")
                return {**self._status, "report": report}

        # 8. Request publication authorization at the final commit boundary (§8).
        self._stage("publishing")
        version = f"{request.get('base_version', '0.1.0')}-{candidate_id}"
        decision = await workflow.execute_activity(
            request_authorization_activity,
            {
                "candidate_id": candidate_id,
                "generation": generation,
                "artifact_digest": artifact_digest,
                "version": version,
                "snapshot_id": snap["snapshot_id"],
            },
            start_to_close_timeout=timedelta(minutes=6), retry_policy=_DEFAULT_RETRY,
        )

        if decision.get("granted"):
            self._stage("published")
            self._status.update(published=True, reason=decision["reason"], version=version)
        else:
            self._stage("superseded")
            self._status.update(published=False, reason=decision["reason"])
        return {**self._status, "decision": decision, "report": report}

    @workflow.update
    async def submit_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Record a human review decision (§6).

        Idempotent: a verbatim re-submit of the recorded decision returns it
        unchanged. The validator has already rejected wrong-candidate, stale, or
        conflicting decisions before this runs.
        """
        if self._review_decision is not None:
            return self._review_decision  # idempotent repeat
        self._review_decision = review.record_decision(payload, workflow.now().isoformat())
        return self._review_decision

    @submit_review.validator
    def _validate_review(self, payload: dict[str, Any]) -> None:
        review.validate_decision(self._review, self._review_decision, payload)

    @workflow.query
    def review(self) -> dict[str, Any] | None:
        """The open review request (with request id + evidence), or None."""
        return self._review

    @workflow.query
    def status(self) -> dict[str, Any]:
        return self._status
