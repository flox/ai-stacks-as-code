"""Durable, reproducible ask-flox index pipeline (Temporal + Flox).

This package adds a correctness spine on top of the existing `pipeline/` stages:

  immutable source snapshots -> candidate identity -> publication generation ->
  Temporal workflow -> isolated candidate artifact -> evaluation ->
  publication authorization -> immutable publish

The modules split cleanly into a *pure* core (snapshot, candidate, authority,
store, evaluate) that carries the correctness invariants and is unit-testable
without a Temporal server, and a *Temporal* layer (activities, workflows,
worker, cli) that provides durable orchestration around that core.
"""

__all__ = [
    "canonical",
    "config",
    "candidate",
    "authority",
    "store",
    "snapshot",
    "evaluate",
]
