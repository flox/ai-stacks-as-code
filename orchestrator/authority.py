"""Publication generation + supersession policy (§2.3, §8).

This is the *pure* heart of the correctness guarantee: given the authority's
state and a request, decide whether a candidate is still allowed to publish.
It is deliberately free of Temporal and IO so the invariant can be tested
exhaustively and then simply *wrapped* by the singleton authority workflow.

Model:
  - ``desired_generation`` — the newest publication intent registered. Every new
    build request bumps this; that is what "supersedes" older requests.
  - ``published_generation`` — the highest generation that actually became
    current, with the candidate/artifact that won.

Rules:
  - Registration assigns a strictly increasing generation.
  - Authorization is granted only for the *desired* generation and only if it is
    newer than what is already published. A superseded generation can never win,
    even if its build finishes last.
  - Authorization is idempotent: re-requesting for the candidate/generation that
    is already published returns a grant with no state change (safe publish retry).
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class AuthorityState:
    desired_generation: int = 0
    published_generation: int = 0
    published_candidate: str | None = None
    published_artifact: str | None = None
    # (generation, candidate_id) audit trail of registrations, newest last.
    registrations: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True)
class Decision:
    granted: bool
    reason: str
    generation: int
    published_generation: int
    published_candidate: str | None = None

    @property
    def already_published(self) -> bool:
        return self.granted and self.reason == "already-published"


def register(state: AuthorityState, candidate_id: str) -> tuple[AuthorityState, int]:
    """Register a new publication intent, returning its generation.

    The new generation becomes the desired one, superseding all older requests.
    """
    generation = state.desired_generation + 1
    new_state = replace(
        state,
        desired_generation=generation,
        registrations=state.registrations + ((generation, candidate_id),),
    )
    return new_state, generation


def evaluate_request(
    state: AuthorityState, candidate_id: str, generation: int
) -> Decision:
    """Decide whether to grant publication — WITHOUT mutating state.

    The caller (authority workflow) must, on a fresh ``granted`` decision,
    perform the atomic promotion and only then call :func:`commit_publication`.
    A ``reason == "already-published"`` decision is a no-op idempotent grant.
    """
    # Idempotent republish of what is already current.
    if (
        state.published_generation == generation
        and state.published_candidate == candidate_id
    ):
        return Decision(True, "already-published", generation,
                        state.published_generation, state.published_candidate)

    if not isinstance(generation, int) or generation <= 0:
        return Decision(False, "invalid-generation", generation,
                        state.published_generation, state.published_candidate)

    # A request older than the newest intent is superseded and can never win.
    if generation < state.desired_generation:
        return Decision(False, "superseded", generation,
                        state.published_generation, state.published_candidate)

    # Never move backwards or re-publish an already-covered generation.
    if generation <= state.published_generation:
        return Decision(False, "stale-generation", generation,
                        state.published_generation, state.published_candidate)

    if generation != state.desired_generation:
        return Decision(False, "unknown-generation", generation,
                        state.published_generation, state.published_candidate)

    return Decision(True, "granted", generation, generation, candidate_id)


def commit_publication_legacy(
    state: AuthorityState,
    candidate_id: str,
    generation: int,
    artifact_digest: str,
) -> AuthorityState:
    """Replay-only form of the pre-fencing publication commit.

    Existing Temporal histories may contain the old authorize command sequence,
    whose post-Activity state mutation was unconditional. New executions must use
    :func:`commit_publication`; this helper exists only so those histories replay
    with their original pure-state semantics.
    """
    return replace(
        state,
        published_generation=generation,
        published_candidate=candidate_id,
        published_artifact=artifact_digest,
    )


def commit_publication(
    state: AuthorityState,
    candidate_id: str,
    generation: int,
    artifact_digest: str,
) -> AuthorityState:
    """Record that ``candidate_id`` became current at ``generation``.

    The commit re-checks the ordering policy rather than trusting its caller.
    This keeps stale or superseded generations from being written into authority
    state even if a future caller forgets the evaluate-before-commit contract.
    """
    decision = evaluate_request(state, candidate_id, generation)
    if decision.already_published:
        if state.published_artifact != artifact_digest:
            raise ValueError("published generation cannot change artifact")
        return state
    if not decision.granted:
        raise ValueError(f"cannot commit publication: {decision.reason}")
    return replace(
        state,
        published_generation=generation,
        published_candidate=candidate_id,
        published_artifact=artifact_digest,
    )
