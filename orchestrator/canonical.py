"""Canonical serialization + hashing.

Every identity in this system (source snapshot id, candidate id, artifact
digest) is a hash over a *canonical* JSON serialization: UTF-8, sorted keys,
no insignificant whitespace. Keeping this in one place means every hash in the
pipeline is computed the same deterministic way.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, UTF-8 preserved."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_text(stable_json(value))


def short(digest: str, prefix: str, width: int = 16) -> str:
    return f"{prefix}{digest[:width]}"
