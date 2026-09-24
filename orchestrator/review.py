"""Human-review policy (§6) — the pure decision logic.

Human review is durable *workflow state*, not a worker blocked on stdin or an
open browser request. The build workflow, after the deterministic gate passes,
opens a review request and durably waits; a reviewer submits a decision via a
validated Temporal Update. This module holds the pure, testable rules the
workflow's update validator and handler wrap:

  - a decision must target the open review (matching request id + candidate);
  - the decision value must be allowed;
  - a duplicate of the already-recorded decision is idempotent (no-op);
  - a *conflicting* decision after one is recorded is rejected;
  - (enforced by the workflow, not here) approval never overrides supersession —
    an approved-but-superseded candidate still cannot become current, because the
    publication authority is consulted after review.
"""
from __future__ import annotations

from typing import Any

REVIEW_SCHEMA_VERSION = 1
ALLOWED_DECISIONS = ("approve", "reject")


class ReviewError(ValueError):
    """Raised for an invalid or stale review decision (rejected before it becomes state)."""


def review_request_id(candidate_id: str, generation: int) -> str:
    """Deterministic id for the review of a candidate at a generation."""
    return f"rev-{candidate_id}-g{generation}"


def build_review_request(
    *,
    candidate_id: str,
    generation: int,
    snapshot_id: str,
    source_commits: dict[str, str] | None,
    report: dict[str, Any],
    artifact_digest: str,
    reason: str = "policy: manual approval required before publish",
) -> dict[str, Any]:
    """The review request surfaced to a reviewer — enough to decide (§6)."""
    return {
        "review_request_id": review_request_id(candidate_id, generation),
        "schema_version": REVIEW_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "generation": generation,
        "snapshot_id": snapshot_id,
        "source_commits": source_commits or {},
        "reason": reason,
        "allowed_decisions": list(ALLOWED_DECISIONS),
        "evidence": {"artifact_digest": artifact_digest, "eval": report},
    }


def is_idempotent_repeat(payload: dict[str, Any], existing: dict[str, Any] | None) -> bool:
    """True if ``payload`` re-submits the already-recorded decision verbatim."""
    return (
        existing is not None
        and payload.get("review_request_id") == existing["review_request_id"]
        and payload.get("decision") == existing["decision"]
    )


def validate_decision(
    request: dict[str, Any] | None,
    existing_decision: dict[str, Any] | None,
    payload: dict[str, Any],
) -> None:
    """Raise :class:`ReviewError` if ``payload`` must not be accepted as state.

    A verbatim repeat of the recorded decision is allowed (handled idempotently
    by the caller); any *conflicting* decision after one is recorded is rejected.
    """
    if request is None:
        raise ReviewError("no review is currently open for this build")
    if payload.get("decision") not in ALLOWED_DECISIONS:
        raise ReviewError(f"decision must be one of {ALLOWED_DECISIONS}")
    if payload.get("review_request_id") != request["review_request_id"]:
        raise ReviewError("review_request_id does not match the open review (stale decision)")
    if payload.get("candidate_id") != request["candidate_id"]:
        raise ReviewError("decision targets the wrong candidate")
    if existing_decision is not None and not is_idempotent_repeat(payload, existing_decision):
        raise ReviewError("review already decided")


def record_decision(payload: dict[str, Any], decided_at: str) -> dict[str, Any]:
    """Canonical stored form of an accepted decision."""
    return {
        "review_request_id": payload["review_request_id"],
        "candidate_id": payload["candidate_id"],
        "decision": payload["decision"],
        "reviewer": payload.get("reviewer") or "unknown",
        "note": payload.get("note") or "",
        "decided_at": decided_at,
    }
