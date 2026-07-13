#!/usr/bin/env python3
"""Shared utilities for the ai-brief pipeline.

The helpers in this module are deliberately small, deterministic, and free of
network behavior. Stage scripts may import optional third-party packages, but
common.py stays stdlib-only so errors remain actionable even in a partial env.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

JSON = dict[str, Any]

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_'-]*")
_SENTENCE_RE = re.compile(r"[^.!?]+(?:[.!?]+|$)", re.MULTILINE)


def env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_text(stable_json(value))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON in {path}: {exc}") from exc


def read_jsonl(path: Path) -> list[JSON]:
    records: list[JSON] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"invalid JSONL in {path}:{line_no}: {exc}") from exc
                if not isinstance(obj, dict):
                    raise RuntimeError(f"invalid JSONL in {path}:{line_no}: record is not an object")
                records.append(obj)
    except FileNotFoundError as exc:
        raise RuntimeError(f"missing required input: {path}") from exc
    return records


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass



def write_text(path: Path, text: str) -> None:
    _atomic_write(path, text)


def write_json(path: Path, value: Any) -> None:
    _atomic_write(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def write_jsonl(path: Path, records: Iterable[JSON]) -> None:
    lines = [json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for rec in records]
    _atomic_write(path, "\n".join(lines) + ("\n" if lines else ""))


def preserve_generated_at(output_path: Path, fingerprint: str) -> str:
    existing = read_json(output_path, default=None)
    if isinstance(existing, dict):
        metadata = existing.get("metadata")
        if isinstance(metadata, dict) and metadata.get("fingerprint") == fingerprint:
            generated_at = existing.get("generated_at")
            if isinstance(generated_at, str) and generated_at:
                return generated_at
    return utc_now_iso()


def relative_display_path(path: Path, base: Path | None = None) -> str:
    resolved = path.resolve()
    candidates = []
    if base is not None:
        candidates.append(base.resolve())
    candidates.append(Path.cwd().resolve())
    for root in candidates:
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            continue
    return str(resolved)


def tokenize(text: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(text)]


def token_count(text: str) -> int:
    return len(tokenize(text))


def chunk_hash(chunk: dict[str, Any]) -> str:
    """Return the canonical content hash used to bind chunks to the index.

    Keep this narrow: the index should be invalidated when the identity, text,
    offsets, ordering, or chunk-level metadata changes. Non-contract fields added
    later should not accidentally perturb the vector cache.
    """
    return sha256_json({
        "id": chunk.get("id"),
        "document_id": chunk.get("document_id"),
        "ordinal": chunk.get("ordinal"),
        "text": chunk.get("text"),
        "char_start": chunk.get("char_start"),
        "char_end": chunk.get("char_end"),
        "metadata": chunk.get("metadata", {}),
    })


def sentences_with_offsets(text: str) -> Iterator[tuple[int, int, str]]:
    for match in _SENTENCE_RE.finditer(text):
        sent = re.sub(r"\s+", " ", match.group(0)).strip()
        if sent:
            yield match.start(), match.end(), sent


def unique_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
