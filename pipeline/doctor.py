#!/usr/bin/env python3
"""ai-brief: doctor — report whether the environment is ready on this machine.

This is intentionally NOT a stub: it must make the real state obvious and
actionable, including honestly reporting the PyTorch/glibc failure. Exit
status is nonzero if any critical check fails so it is usable in CI.
"""
import argparse
import importlib
import importlib.metadata
import os
import platform
import shutil
import sys

OK, FAIL, WARN = "OK", "FAIL", "WARN"


def status(mark: str, label: str, detail: str = "") -> None:
    print(f"  [{mark:^4}] {label}" + (f" — {detail}" if detail else ""))


def import_version(package_name: str) -> str:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return "installed"


def check_python_import(module_name: str, label: str, package_name: str | None = None, *, critical: bool = True) -> int:
    try:
        importlib.import_module(module_name)
        status(OK, label, import_version(package_name or module_name))
        return 0
    except Exception as exc:  # noqa: BLE001
        mark = FAIL if critical else WARN
        detail = (str(exc).strip().splitlines() or [exc.__class__.__name__])[-1]
        status(mark, label, f"cannot import {module_name}: {detail[:180]}")
        return 1 if critical else 0


def main() -> int:
    argparse.ArgumentParser(prog="ai-doctor", description="environment readiness report").parse_args()

    backend = os.environ.get("AI_BACKEND", "cpu")
    fails = 0

    print("ai-brief — environment doctor")
    print(f"  OS/arch : {platform.system()} {platform.machine()}")
    print(f"  backend : AI_BACKEND={backend}")
    print()

    # --- toolchain ---
    status(OK, "Python", sys.version.split()[0])
    if shutil.which("uv"):
        status(OK, "uv", shutil.which("uv"))
    else:
        fails += 1
        status(FAIL, "uv", "not found on PATH")

    # --- strict pipeline dependencies ---
    print()
    fails += check_python_import("semchunk", "semchunk import", "semchunk")
    fails += check_python_import("sentence_transformers", "sentence-transformers import", "sentence-transformers")
    fails += check_python_import("chromadb", "ChromaDB import", "chromadb")
    # ONNX runtime powers the default (torch-free) embedding engine.
    fails += check_python_import("onnxruntime", "onnxruntime import (default embedder)", "onnxruntime")
    # MCP SDK powers the optional "Ask Flox" retrieval server; not required for
    # the core pipeline, so a miss warns rather than failing overall readiness.
    check_python_import("mcp.server.fastmcp", "MCP SDK import (ask-flox server)", "mcp", critical=False)

    # --- PyTorch (honest about the glibc mismatch) ---
    torch = None
    try:
        import torch as _torch  # noqa: N813
        torch = _torch
        status(OK, "PyTorch import", torch.__version__)
    except Exception as exc:  # noqa: BLE001
        fails += 1
        reason = (str(exc).strip().splitlines() or [exc.__class__.__name__])[-1]
        print(f"  [{FAIL:^4}] PyTorch import")
        print(f"           Reason: {reason[:180]}")
        print("           Impact: index/brief cannot run with the current PyTorch backend")
        print("           Next step: align the Flox base/glibc or use a compatible torch build")

    # --- accelerator for the selected backend ---
    if torch is not None and backend == "cuda":
        avail = bool(getattr(torch, "cuda", None) and torch.cuda.is_available())
        status(OK if avail else WARN, "CUDA available", "yes" if avail else "no (would fall back to CPU)")
    if torch is not None and backend == "mps":
        mps = getattr(getattr(torch, "backends", None), "mps", None)
        avail = bool(mps and mps.is_available())
        status(OK if avail else WARN, "MPS available", "yes" if avail else "no")
    if backend == "mlx":
        try:
            import mlx.core  # noqa: F401
            status(OK, "MLX import", "ok")
        except Exception as exc:  # noqa: BLE001
            fails += 1
            status(FAIL, "MLX import", exc.__class__.__name__)

    # --- project layout ---
    print()
    for name in ("SOURCES_DIR", "WORK_DIR", "REPORTS_DIR", "PROMPTS_DIR",
                 "EVALS_DIR", "NOTEBOOKS_DIR", "PIPELINE_DIR"):
        path = os.environ.get(name)
        if not path:
            status(WARN, name, "unset")
        elif os.path.isdir(path):
            status(OK, name, path)
        else:
            status(WARN, name, f"missing: {path}")

    # --- model/cache paths ---
    print()
    for name in ("HF_HOME", "TORCH_HOME", "XDG_CACHE_HOME"):
        val = os.environ.get(name)
        status(OK if val else WARN, name, val or "unset (using defaults)")

    # --- verdict + next step ---
    print()
    if fails:
        print(f"NOT READY — {fails} critical check(s) failed. Address the FAILs above.")
        print("Next: activate the composed Flox environment, make sure strict-path packages import, then run `ai-doctor` and plain `ai-eval` again.")
        return 1
    print("READY. Next: `ingest` -> `index` -> `brief`  (or `notebook` to explore).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
