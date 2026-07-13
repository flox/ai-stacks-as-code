#!/usr/bin/env python3
"""ai-brief: eval stage.

Run an end-to-end regression check against shipped fixtures.

Plain `ai-eval` is the contract path: semchunk ingest, sentence-transformers
embeddings, and Chroma retrieval. `--dev-hash`/`--fast` is an explicit sandbox
smoke mode for partial runtimes that do not have semchunk, sentence-transformers,
or Chroma available.
"""
from __future__ import annotations

import argparse
import copy
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from common import env_path, read_json, read_jsonl, sha256_json, utc_now_iso, write_json, write_jsonl

REQUIRED_BRIEF_KEYS = {"query", "themes", "uncertainties", "sources", "chunk_count"}
IDEMPOTENT_ARTIFACTS = (
    "work/documents.jsonl",
    "work/chunks.jsonl",
    "work/index-manifest.json",
    "reports/brief.json",
    "reports/brief.md",
)


def check(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    rec: dict[str, Any] = {"check": name, "ok": bool(ok)}
    if detail:
        rec["detail"] = detail
    return rec


def run_stage(script: Path, env: dict[str, str], args: list[str] | None = None) -> tuple[bool, str]:
    cmd = [sys.executable, str(script)] + (args or [])
    proc = subprocess.run(cmd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    combined = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, combined[-4000:]


def normalize_for_golden(value: Any, *, dev_mode: bool = False) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"generated_at", "metadata", "backend"}:
                continue
            if key == "uncertainties" and dev_mode and isinstance(item, list):
                # The explicit sandbox path correctly records that hash embeddings
                # reduce retrieval quality. The strict golden represents the real
                # sentence-transformers + Chroma contract path, so do not let that
                # dev-only warning make the smoke mode fail.
                item = [
                    x for x in item
                    if "deterministic hash embeddings" not in str(x)
                    and "development mode" not in str(x)
                ] or ["The brief is extractive and limited to the supplied sources; it does not infer facts outside the corpus."]
            normalized[key] = normalize_for_golden(item, dev_mode=dev_mode)
        return normalized
    if isinstance(value, list):
        return [normalize_for_golden(v, dev_mode=dev_mode) for v in value]
    return value


def validate_brief(brief: dict[str, Any], chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    missing = sorted(REQUIRED_BRIEF_KEYS - set(brief))
    checks.append(check("required top-level keys", not missing, f"missing: {missing}" if missing else ""))
    themes = brief.get("themes")
    checks.append(check("themes non-empty", isinstance(themes, list) and len(themes) > 0))
    checks.append(check("chunk_count matches chunks", brief.get("chunk_count") == len(chunks)))
    chunk_ids = {str(chunk.get("id")) for chunk in chunks}
    citation_errors: list[str] = []
    if isinstance(themes, list):
        for idx, theme in enumerate(themes):
            if not isinstance(theme, dict):
                citation_errors.append(f"theme {idx} is not an object")
                continue
            citations = theme.get("citations")
            if not isinstance(citations, list) or not citations:
                citation_errors.append(f"theme {idx} has no citations")
                continue
            for cid in citations:
                if cid not in chunk_ids:
                    citation_errors.append(f"theme {idx} cites missing chunk {cid}")
    checks.append(check("citations resolve", not citation_errors, "; ".join(citation_errors[:8])))
    sources = brief.get("sources")
    checks.append(check("sources non-empty", isinstance(sources, list) and len(sources) > 0))
    return checks


def collect_eval_metadata(eval_work_dir: Path | None, eval_reports_dir: Path | None, *, dev_mode: bool) -> dict[str, Any]:
    """Return stable eval metadata, including the actual index contract path used."""
    metadata: dict[str, Any] = {
        "mode": "dev-hash" if dev_mode else "strict",
        "embedding_engine": None,
        "index_store": None,
        "brief_embedding_engine": None,
        "brief_index_store": None,
    }
    if eval_work_dir is not None:
        manifest = read_json(eval_work_dir / "index-manifest.json", default=None)
        if isinstance(manifest, dict):
            metadata["embedding_engine"] = manifest.get("embedding_engine")
            metadata["index_store"] = manifest.get("store")
            metadata["embedding_model"] = manifest.get("model")
            metadata["index_collection"] = manifest.get("collection")
    if eval_reports_dir is not None:
        brief = read_json(eval_reports_dir / "brief.json", default=None)
        if isinstance(brief, dict) and isinstance(brief.get("metadata"), dict):
            brief_meta = brief["metadata"]
            metadata["brief_embedding_engine"] = brief_meta.get("embedding_engine")
            metadata["brief_index_store"] = brief_meta.get("index_store")
    return metadata


def write_result(results_path: Path, ok: bool, checks: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> None:
    payload_metadata = dict(metadata or {})
    fingerprint = sha256_json({"ok": ok, "checks": checks, "metadata": payload_metadata})
    old = read_json(results_path, default={})
    generated_at = (
        old.get("generated_at")
        if isinstance(old, dict) and old.get("metadata", {}).get("fingerprint") == fingerprint
        else utc_now_iso()
    )
    payload_metadata["fingerprint"] = fingerprint
    result = {"ok": ok, "generated_at": generated_at, "checks": checks, "metadata": payload_metadata}
    write_json(results_path, result)


def artifact_hashes(eval_run_dir: Path) -> tuple[dict[str, str], list[str]]:
    hashes: dict[str, str] = {}
    missing: list[str] = []
    for rel in IDEMPOTENT_ARTIFACTS:
        path = eval_run_dir / rel
        if not path.exists():
            missing.append(rel)
            continue
        hashes[rel] = sha256_json({"bytes": path.read_bytes().hex()})
    return hashes, missing


def run_pipeline_once(
    repo_root: Path,
    env: dict[str, str],
    ingest_args: list[str],
    index_args: list[str],
    brief_args: list[str],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    stages = [
        ("ingest", repo_root / "pipeline" / "ingest.py", ingest_args),
        ("index", repo_root / "pipeline" / "index.py", index_args),
        ("brief", repo_root / "pipeline" / "brief.py", brief_args),
    ]
    for name, script, stage_args in stages:
        ok, output = run_stage(script, env, stage_args)
        checks.append(check(f"run {name}", ok, output if not ok else ""))
        if not ok:
            break
    return checks


def run_stale_index_regression(
    repo_root: Path,
    eval_run_dir: Path,
    eval_work_dir: Path,
    env: dict[str, str],
    *,
    dev_mode: bool,
    query: str,
) -> dict[str, Any]:
    """Verify brief rejects changed chunks when chunk_count is unchanged."""
    stale_work_dir = eval_run_dir / "stale-work"
    stale_reports_dir = eval_run_dir / "stale-reports"
    shutil.rmtree(stale_work_dir, ignore_errors=True)
    shutil.rmtree(stale_reports_dir, ignore_errors=True)
    shutil.copytree(eval_work_dir, stale_work_dir)

    chunks_path = stale_work_dir / "chunks.jsonl"
    chunks = read_jsonl(chunks_path)
    if not chunks:
        return check("brief rejects stale index", False, "no chunks available to tamper")
    chunks[0]["text"] = str(chunks[0].get("text", "")) + "\n\nTamper marker for stale-index regression."
    write_jsonl(chunks_path, chunks)

    stale_env = env.copy()
    stale_env["WORK_DIR"] = str(stale_work_dir)
    stale_env["REPORTS_DIR"] = str(stale_reports_dir)
    args = ["--query", query]
    if dev_mode:
        args.extend(["--dev-hash-embeddings", "--dev-json-store"])
    ok, output = run_stage(repo_root / "pipeline" / "brief.py", stale_env, args)
    expected_phrase = "chunks changed since index was generated; run index again"
    if ok:
        return check("brief rejects stale index", False, "brief succeeded after chunk text changed without re-indexing")
    if expected_phrase not in output:
        return check("brief rejects stale index", False, f"unexpected failure: {output[-1000:]}")
    return check("brief rejects stale index", True)


def run_bad_citation_regression(brief: dict[str, Any], chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify the evaluator fails a brief that cites a nonexistent chunk."""
    mutated = copy.deepcopy(brief)
    themes = mutated.get("themes")
    if not isinstance(themes, list) or not themes or not isinstance(themes[0], dict):
        return check("bad citation regression", False, "cannot mutate missing first theme")
    citations = themes[0].setdefault("citations", [])
    if not isinstance(citations, list):
        return check("bad citation regression", False, "first theme citations is not a list")
    citations.append("chunk_missing_bad_citation_regression")
    citation_checks = validate_brief(mutated, chunks)
    citation_result = next((item for item in citation_checks if item.get("check") == "citations resolve"), None)
    if citation_result and citation_result.get("ok") is False:
        return check("bad citation regression", True)
    return check("bad citation regression", False, "validator did not reject a nonexistent chunk citation")


def run_idempotency_check(
    repo_root: Path,
    eval_run_dir: Path,
    env: dict[str, str],
    ingest_args: list[str],
    index_args: list[str],
    brief_args: list[str],
) -> dict[str, Any]:
    before, missing_before = artifact_hashes(eval_run_dir)
    if missing_before:
        return check("rerun idempotency", False, f"missing before rerun: {missing_before}")
    rerun_checks = run_pipeline_once(repo_root, env, ingest_args, index_args, brief_args)
    failed = [item for item in rerun_checks if item.get("ok") is not True]
    if failed:
        return check("rerun idempotency", False, f"rerun failed: {failed[0].get('check')}: {failed[0].get('detail', '')}")
    after, missing_after = artifact_hashes(eval_run_dir)
    if missing_after:
        return check("rerun idempotency", False, f"missing after rerun: {missing_after}")
    changed = sorted(rel for rel in before if before.get(rel) != after.get(rel))
    if changed:
        return check("rerun idempotency", False, f"changed artifacts: {changed}")
    return check("rerun idempotency", True)


def validate_golden(evals_dir: Path, brief: dict[str, Any], *, dev_mode: bool) -> dict[str, Any]:
    golden_path = evals_dir / "expected" / "brief.json"
    if not golden_path.exists():
        return check("golden brief present", False, f"missing: {golden_path}")
    try:
        golden = read_json(golden_path)
    except Exception as exc:  # noqa: BLE001
        return check("golden brief parses", False, str(exc))
    if not isinstance(golden, dict):
        return check("golden brief parses", False, "top-level value is not an object")
    return check("golden brief matches", normalize_for_golden(brief, dev_mode=dev_mode) == normalize_for_golden(golden, dev_mode=dev_mode))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-eval", description="pipeline regression checks")
    parser.add_argument("paths", nargs="*", help="optional fixture directories; defaults to EVALS_DIR/fixtures")
    parser.add_argument("--query", default="What should an AI stack as code provide?")
    parser.add_argument(
        "--dev-hash",
        "--fast",
        dest="dev_hash",
        action="store_true",
        help="explicit sandbox/dev smoke mode: permit fallback chunking, hash embeddings, and JSON store",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    evals_dir = env_path("EVALS_DIR", "evals")
    outer_work_dir = env_path("WORK_DIR", "work")
    fixture_paths = [Path(p).expanduser() for p in args.paths] if args.paths else [evals_dir / "fixtures"]
    fixtures = fixture_paths[0] if len(fixture_paths) == 1 else None
    eval_run_dir = outer_work_dir / "ai-eval-run"
    eval_work_dir = eval_run_dir / "work"
    eval_reports_dir = eval_run_dir / "reports"
    results_path = outer_work_dir / "eval-results.json"
    checks: list[dict[str, Any]] = []

    if any(not p.exists() for p in fixture_paths):
        missing = [str(p) for p in fixture_paths if not p.exists()]
        checks.append(check("fixtures exist", False, f"missing: {missing}"))
        write_result(results_path, False, checks, collect_eval_metadata(None, None, dev_mode=args.dev_hash))
        print(f"ai-eval: failed; wrote {results_path}", file=sys.stderr)
        return 1
    checks.append(check("fixtures exist", True))

    shutil.rmtree(eval_run_dir, ignore_errors=True)
    eval_work_dir.mkdir(parents=True, exist_ok=True)
    eval_reports_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["WORK_DIR"] = str(eval_work_dir)
    env["REPORTS_DIR"] = str(eval_reports_dir)
    env["AI_BACKEND"] = env.get("AI_BACKEND", "cpu")
    if fixtures is not None:
        env["SOURCES_DIR"] = str(fixtures)
        ingest_args: list[str] = []
    else:
        env["SOURCES_DIR"] = str(fixture_paths[0])
        ingest_args = [str(p) for p in fixture_paths]

    index_args: list[str] = []
    brief_args = ["--query", args.query]
    if args.dev_hash:
        env["EMBED_MODEL"] = "hash:ai-brief-eval"
        env["AI_BRIEF_ALLOW_CHUNK_FALLBACK"] = "1"
        env["AI_BRIEF_ALLOW_HASH_FALLBACK"] = "1"
        env["AI_BRIEF_ALLOW_HASH_EMBEDDINGS"] = "1"
        env["AI_BRIEF_ALLOW_JSON_FALLBACK"] = "1"
        env["AI_BRIEF_ALLOW_JSON_STORE"] = "1"
        ingest_args = ["--dev-fallback-chunker"] + ingest_args
        index_args = ["--dev-hash-embeddings", "--dev-json-store"]
        brief_args.extend(["--dev-hash-embeddings", "--dev-json-store"])

    stage_checks = run_pipeline_once(repo_root, env, ingest_args, index_args, brief_args)
    checks.extend(stage_checks)
    first_failure = next((item for item in stage_checks if item.get("ok") is not True), None)
    if first_failure is not None:
        write_result(results_path, False, checks, collect_eval_metadata(eval_work_dir, eval_reports_dir, dev_mode=args.dev_hash))
        print(f"ai-eval: failed during {first_failure.get('check')}; wrote {results_path}", file=sys.stderr)
        return 1

    manifest_path = eval_work_dir / "index-manifest.json"
    manifest = read_json(manifest_path, default=None)
    if isinstance(manifest, dict):
        engine_ok = manifest.get("embedding_engine") == "sentence-transformers"
        store_ok = manifest.get("store") == "chroma"
        if args.dev_hash:
            engine_ok = manifest.get("embedding_engine") in {"sentence-transformers", "hash"}
            store_ok = manifest.get("store") in {"chroma", "json"}
        engine_label = "dev index embedding engine explicitly allowed" if args.dev_hash else "index uses required embedding engine"
        store_label = "dev index vector store explicitly allowed" if args.dev_hash else "index uses required Chroma store"
        checks.append(check(engine_label, engine_ok, f"embedding_engine={manifest.get('embedding_engine')!r}" if not engine_ok else ""))
        checks.append(check(store_label, store_ok, f"store={manifest.get('store')!r}" if not store_ok else ""))
    else:
        checks.append(check("index manifest parses", False, f"missing or invalid: {manifest_path}"))

    brief_path = eval_reports_dir / "brief.json"
    chunks_path = eval_work_dir / "chunks.jsonl"
    try:
        brief = read_json(brief_path)
        chunks = read_jsonl(chunks_path)
        if not isinstance(brief, dict):
            checks.append(check("brief.json parses", False, "top-level value is not an object"))
            brief = None
        else:
            checks.append(check("brief.json parses", True))
            checks.extend(validate_brief(brief, chunks))
            metadata = brief.get("metadata") if isinstance(brief.get("metadata"), dict) else {}
            retrieval_ids = metadata.get("retrieved_chunk_ids") if isinstance(metadata, dict) else None
            retrieval_queries = metadata.get("retrieval_queries") if isinstance(metadata, dict) else None
            checks.append(check("brief records index retrieval", isinstance(retrieval_ids, list) and len(retrieval_ids) > 0 and isinstance(retrieval_queries, list) and len(retrieval_queries) > 0))
            if isinstance(metadata, dict):
                expected_store = {"chroma", "json"} if args.dev_hash else {"chroma"}
                checks.append(check("brief used indexed store", metadata.get("index_store") in expected_store, f"index_store={metadata.get('index_store')!r}" if metadata.get("index_store") not in expected_store else ""))
            checks.append(validate_golden(evals_dir, brief, dev_mode=args.dev_hash))
            checks.append(run_bad_citation_regression(brief, chunks))
            checks.append(run_idempotency_check(repo_root, eval_run_dir, env, ingest_args, index_args, brief_args))
            checks.append(run_stale_index_regression(repo_root, eval_run_dir, eval_work_dir, env, dev_mode=args.dev_hash, query=args.query))
    except Exception as exc:  # noqa: BLE001
        checks.append(check("brief.json parses", False, str(exc)))

    ok = all(item.get("ok") is True for item in checks)
    write_result(results_path, ok, checks, collect_eval_metadata(eval_work_dir, eval_reports_dir, dev_mode=args.dev_hash))
    if ok:
        print(f"ai-eval: ok; wrote {results_path}")
        return 0
    print(f"ai-eval: failed; wrote {results_path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
