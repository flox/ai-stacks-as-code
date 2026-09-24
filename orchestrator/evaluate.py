"""Deterministic evaluation gate (§11).

A ChromaDB build completing is not enough to publish it. This gate runs only
deterministic checks (no model judge — that is deferred to a later phase) over a
freshly built index and its snapshot manifest:

  - the index opens and is non-empty
  - the index manifest has the required, valid fields
  - every indexed chunk carries source provenance that resolves to an admitted
    file in the snapshot
  - no empty/pathological chunks
  - the manifest chunk_count matches the live collection count
  - representative queries retrieve non-empty passages

Model-based evaluation, when added, must live separately and must not be able to
conceal a deterministic failure here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import store

DEFAULT_QUERIES = (
    "how does environment layering work in flox",
    "what is a flox environment manifest",
    "how do i install a package with flox",
)

REQUIRED_MANIFEST_FIELDS = ("model", "dimension", "embedding_engine", "collection", "chunk_count")


@dataclass
class EvalReport:
    ok: bool = True
    checks: list[dict[str, Any]] = field(default_factory=list)

    def add(self, check: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"check": check, "ok": ok, "detail": detail})
        if not ok:
            self.ok = False

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": self.checks}


def _admitted_paths(snapshot_manifest: dict[str, Any]) -> set[str]:
    admitted: set[str] = set()
    for src in snapshot_manifest.get("sources", []):
        name = src["name"]
        for entry in src.get("files", []):
            admitted.add(f"{name}/{entry['path']}")
    return admitted


def evaluate(
    index_dir: Path,
    manifest_path: Path,
    snapshot_manifest: dict[str, Any],
    *,
    queries: tuple[str, ...] = DEFAULT_QUERIES,
) -> EvalReport:
    report = EvalReport()

    manifest = store.read_json(manifest_path, default=None)
    if not isinstance(manifest, dict):
        report.add("index manifest present", False, f"missing/invalid: {manifest_path}")
        return report
    report.add("index manifest present", True)

    missing = [f for f in REQUIRED_MANIFEST_FIELDS if f not in manifest]
    report.add("required manifest fields", not missing, f"missing: {missing}" if missing else "")

    try:
        import chromadb  # type: ignore
    except Exception as exc:  # noqa: BLE001
        report.add("chromadb available", False, str(exc))
        return report

    collection_name = str(manifest.get("collection", "ai_brief"))
    try:
        client = chromadb.PersistentClient(path=str(index_dir))
        collection = client.get_collection(collection_name)
        count = collection.count()
    except Exception as exc:  # noqa: BLE001
        report.add("index opens", False, str(exc))
        return report
    report.add("index opens", True, f"collection={collection_name}")
    report.add("index non-empty", count > 0, f"count={count}")

    manifest_count = manifest.get("chunk_count")
    report.add(
        "manifest count matches collection",
        manifest_count == count,
        f"manifest={manifest_count} collection={count}",
    )

    admitted = _admitted_paths(snapshot_manifest)
    got = collection.get(include=["documents", "metadatas"])
    ids = got.get("ids", [])
    documents = got.get("documents", []) or []
    metadatas = got.get("metadatas", []) or []

    empty = 0
    missing_provenance = 0
    unresolved = 0
    for doc, meta in zip(documents, metadatas):
        if not (isinstance(doc, str) and doc.strip()):
            empty += 1
        meta = meta or {}
        source_path = str(meta.get("source_path", ""))
        document_id = str(meta.get("document_id", ""))
        if not source_path or not document_id:
            missing_provenance += 1
            continue
        if admitted and source_path not in admitted:
            unresolved += 1

    report.add("no empty chunks", empty == 0, f"empty={empty}")
    report.add("every chunk has provenance", missing_provenance == 0, f"missing={missing_provenance}")
    report.add(
        "chunk sources resolve to snapshot",
        unresolved == 0,
        f"unresolved={unresolved} of {len(ids)}",
    )

    # Representative retrieval — top result must be a real, non-empty passage.
    retrieval_ok = True
    detail = []
    for query in queries:
        try:
            res = collection.query(query_texts=[query], n_results=1)
            docs = (res.get("documents") or [[]])[0]
            hit = bool(docs and isinstance(docs[0], str) and docs[0].strip())
        except Exception as exc:  # noqa: BLE001
            hit = False
            detail.append(f"{query!r}: {exc}")
        retrieval_ok = retrieval_ok and hit
        if not hit:
            detail.append(f"no hit for {query!r}")
    report.add("representative retrieval", retrieval_ok, "; ".join(detail))

    return report
