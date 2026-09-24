"""Temporal workflows — durable orchestration + the single publication authority.

Two workflows:

* ``PublicationAuthorityWorkflow`` — a singleton (stable Workflow ID) that is the
  ONE authority for publication ordering (§2.3, §8). It serializes generation
  registration and authorization; because a single workflow processes its
  updates one at a time, generation-aware compare-and-set needs no locks. It is
  also the only writer of the ``current`` pointer (via ``promote_activity``).

* ``BuildWorkflow`` — the per-candidate lifecycle: snapshot -> identity ->
  register intent -> ingest -> index -> package -> evaluate -> request
  authorization -> (published | superseded | rejected). All side effects are
  activities; this code stays deterministic and replay-safe.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from . import authority, config
from .activities import (
    compute_candidate_activity,
    evaluate_activity,
    existing_artifact_activity,
    hydrate_activity,
    index_activity,
    ingest_activity,
    is_published_activity,
    package_activity,
    promote_activity,
    register_generation_activity,
    request_authorization_activity,
    resolve_snapshot_activity,
)

# Bound authority history: Continue-As-New after this many handled updates.
_CONTINUE_AFTER_UPDATES = 500

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
        """Register a new publication intent; returns its generation."""
        self._state, generation = authority.register(self._state, candidate_id)
        self._handled += 1
        return generation

    @register.validator
    def _validate_register(self, candidate_id: str) -> None:
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError("candidate_id must be a non-empty string")

    @workflow.update
    async def authorize(self, request: dict[str, Any]) -> dict[str, Any]:
        """Grant or deny publication for a candidate/generation (§8).

        On a fresh grant this performs the atomic `current` swap itself, so the
        authority is the sole mutator of the active pointer. Superseded or stale
        requests are denied and never touch `current`.
        """
        candidate_id = request["candidate_id"]
        generation = request["generation"]
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
                },
                start_to_close_timeout=timedelta(minutes=5),
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
            "published": None,
            "reason": None,
        }

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

    @workflow.query
    def status(self) -> dict[str, Any]:
        return self._status
