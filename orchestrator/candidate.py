"""Candidate identity (§2.2).

A *candidate* is the logical index build represented by a set of source
snapshots plus the output-affecting build specification. Its id is a hash over
a canonical, schema-versioned representation of exactly the inputs that can
change what the index *means* — and nothing else.

Included (change these => new candidate):
  - each source's snapshot id + admitted-file manifest digest
  - pipeline code revision (hash of the deterministic transform's source)
  - chunker algorithm + configuration
  - embedding engine / model / dimension
  - Chroma collection + distance space
  - identity + index schema versions

Deliberately excluded (recorded in provenance, never in the id):
  - timestamps, Temporal run ids, retry counts, worker/host identity
  - the torch/onnx *backend* (cpu/cuda/mps): the published index always uses the
    deterministic torch-free ONNX embedder, so the accelerator cannot change the
    produced vectors. It is provenance, not identity.

This module is pure: same inputs -> same id, on any machine.
"""
from __future__ import annotations

from typing import Any

from .canonical import sha256_json, short
from . import config


def build_spec(
    *,
    sources: list[dict[str, Any]],
    pipeline_code_revision: str,
    chunker: dict | None = None,
    embedding: dict | None = None,
    chroma: dict | None = None,
) -> dict[str, Any]:
    """Canonical, schema-versioned representation of the identity-affecting inputs.

    ``sources`` is a list of ``{"name", "snapshot_id", "manifest_digest"}``.
    """
    normalized_sources = sorted(
        (
            {
                "name": s["name"],
                "snapshot_id": s["snapshot_id"],
                "manifest_digest": s["manifest_digest"],
            }
            for s in sources
        ),
        key=lambda s: s["name"],
    )
    return {
        "identity_schema_version": config.IDENTITY_SCHEMA_VERSION,
        "index_schema_version": config.INDEX_SCHEMA_VERSION,
        "sources": normalized_sources,
        "pipeline_code_revision": pipeline_code_revision,
        "chunker": chunker if chunker is not None else config.CHUNKER,
        "embedding": embedding if embedding is not None else config.EMBEDDING,
        "chroma": chroma if chroma is not None else config.CHROMA,
    }


def candidate_id(spec: dict[str, Any]) -> str:
    """Deterministic id for a build spec produced by :func:`build_spec`."""
    return short(sha256_json(spec), prefix="cand_")


def provenance(
    spec: dict[str, Any],
    *,
    excluded: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Full provenance: the identity spec plus the intentionally-excluded fields.

    ``excluded`` carries execution context (backend, host, timestamps, source
    commits, ...) that is recorded for auditability but must not perturb the
    candidate id.
    """
    return {
        "candidate_id": candidate_id(spec),
        "identity": spec,
        "excluded_from_identity": excluded or {},
    }
