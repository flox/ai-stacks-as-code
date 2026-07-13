# ai-brief — code bundle

Self-contained context for implementing the pipeline. Read alongside
pipeline/SPEC.md (the contract). Files are separated by 'FILE:' markers;
content is verbatim. doctor.py is a reference implementation; the other
four pipeline/*.py are stubs to replace.

================================================================
RESOLVED PACKAGES (composed ai-brief env — exact available libs/versions)
================================================================
chromadb: python313Packages.chromadb (1.5.9)
torch-cuda: flox-cuda/python3Packages.torch (python3.13-torch-2.10.0)
sentence-transformers: python313Packages.sentence-transformers (5.3.0)
bat: bat (0.26.1)
curl: curl (8.20.0)
dos2unix: dos2unix (7.5.5)
fd: fd (10.4.2)
git: git (2.54.0)
html2text: html2text (2.4.0)
ipykernel: python313Packages.ipykernel (7.1.0)
jq: jq (1.8.1)
jupyterlab: python313Packages.jupyterlab (4.5.8)
jupyterlab-execute-time: python313Packages.jupyterlab-execute-time (3.3.0)
jupyterlab-lsp: python313Packages.jupyterlab-lsp (5.3.0)
jupyterlab-pygments: python313Packages.jupyterlab-pygments (0.3.0)
jupyterlab-server: python313Packages.jupyterlab-server (2.28.0)
jupyterlab-widgets: python313Packages.jupyterlab-widgets (3.0.16)
just: just (1.55.1)
matplotlib: python313Packages.matplotlib (3.10.9)
mupdf: mupdf (1.27.2)
notebook: python313Packages.notebook (7.5.6)
pandas: python313Packages.pandas (2.3.3)
pandoc: pandoc (3.7.0.2)
plotly: python313Packages.plotly (6.8.0)
poppler-utils: poppler-utils (25.10.0)
pyarrow: python313Packages.pyarrow (23.0.0)
pydot: python313Packages.pydot (4.0.1)
python3: python3 (3.13.13)
ripgrep: ripgrep (15.1.0)
sympy: python313Packages.sympy (1.14.0)
tree: tree (2.3.2)
uv: uv (0.11.25)
yt-dlp: yt-dlp (2026.06.09)
beautifulsoup4: python313Packages.beautifulsoup4 (python3.13-beautifulsoup4-4.14.3)
ftfy: python313Packages.ftfy (6.3.1)
langchain-text-splitters: python313Packages.langchain-text-splitters (1.1.2)
lxml: python313Packages.lxml (6.0.2)
markdown: python313Packages.markdown (3.10.2)
markdown-it-py: python313Packages.markdown-it-py (4.0.0)
pdfplumber: python313Packages.pdfplumber (0.11.9)
pymupdf: python313Packages.pymupdf (1.27.2.3)
readability-lxml: python313Packages.readability-lxml (0.8.4.1)
semchunk: python313Packages.semchunk (4.1.0)
srt: python313Packages.srt (3.5.3)
tiktoken: python313Packages.tiktoken (0.12.0)
unstructured: python313Packages.unstructured (0.18.31)

================================================================
FILE: README.md
================================================================
# ai-brief

An **AI stack as code** — a composed [Flox](https://flox.dev) environment that
turns a folder of raw documents into an evidence-backed brief. It is
reproducible, cross-platform (x86-64 / aarch64 Linux and Apple Silicon), and
adapts its compute backend (CUDA / MPS / CPU) to the machine it runs on.

The demo pipeline is a small local RAG flow: **ingest → index → brief**, with
`ai-eval`, `notebook`, and `ai-doctor` around it.

## Architecture

`ai-brief` is the top-level composing environment. It merges four
single-responsibility component environments at build time:

| Component | Provides |
|---|---|
| `envs/ai-base` | Python 3.13, `uv`, and core tooling (`git`, `jq`, `ripgrep`, `just`) |
| `envs/ai-ingest` | Document parsers — `poppler-utils`, `mupdf`, `pandoc`, `html2text`, `yt-dlp` + Python parsing/chunking libs |
| `envs/ai-embeddings` | `torch`, `sentence-transformers`, and a `chromadb` vector store |
| `envs/ai-notebook` | JupyterLab + the scientific stack, on the same 3.13 interpreter |

Every dependency comes from the Flox catalog and is pinned in the composed
lockfile, so the stack resolves identically on every machine.

## Repository layout

```
.
├── .flox/            # the ai-brief composing environment (activate here)
├── justfile          # routing layer: command -> just recipe -> pipeline script
├── pipeline/         # ingest.py, index.py, brief.py, eval.py, doctor.py
├── envs/             # the four component environments
│   ├── ai-base/  ai-ingest/  ai-embeddings/  ai-notebook/
└── sources/ work/ reports/ prompts/ evals/ notebooks/   # created on activation
```

## Quickstart

```bash
flox activate        # from the repo root; first run realises a large closure
ai-doctor            # confirm the environment is ready on this machine
# drop files into sources/, then:
ingest               # sources/          -> work/documents.jsonl, work/chunks.jsonl
index                # work/chunks.jsonl -> work/index/
brief                # chunks + index    -> reports/brief.md, reports/brief.json
notebook             # open JupyterLab on the same env/paths/backend
```

## Command surface

| Command | Purpose |
|---|---|
| `ingest` | Parse and normalize raw sources into deterministic JSONL chunks |
| `index` | Embed chunks (per `AI_BACKEND`) and build the local vector index |
| `brief` | Retrieve, cluster, summarize, and cite — the main output |
| `ai-eval` | Lightweight pipeline regression check against fixtures |
| `notebook` | JupyterLab exploration surface |
| `ai-doctor` | Report whether the environment is ready (backend, torch, paths) |

Commands are thin shell wrappers (defined in the Flox manifest) that call `just`
recipes, which call `pipeline/<stage>.py`. `just` is the routing layer, so an
implementation can move (Python → shell → Rust) without changing the command
surface. The wrappers are interactive-shell functions; for scripting/CI call the
recipe directly, e.g. `flox activate -- just ai-doctor`.

## Backends and configuration

`AI_BACKEND` is auto-detected (`cuda` on Linux+NVIDIA, `mps` on Apple Silicon,
else `cpu`) and overridable, as are all project paths:

```bash
AI_BACKEND=cpu flox activate
SOURCES_DIR=/data/corpus flox activate
```

On Linux the CUDA-enabled torch build is used and falls back to CPU at runtime
when no GPU is present; on Apple Silicon the stock build provides the Metal/MPS
backend.

## Status

The scaffolding is complete and green: the environment composes, resolves across
all three systems, and `ai-doctor` reports `READY`. The pipeline stages
(`ingest`/`index`/`brief`/`ai-eval`) are **stubs** — each fails clearly with an
actionable message until `pipeline/<stage>.py` is implemented.

## Requirements

- [Flox](https://flox.dev) installed.
- For CUDA on Linux, access to the `flox-cuda` catalog (`flox auth login`). CPU
  and MPS need no special access.

================================================================
FILE: justfile
================================================================
# ai-brief routing layer.
#
# The stable command surface (ingest/index/brief/ai-eval/notebook/ai-doctor)
# lives as shell wrappers in the Flox manifest; those wrappers call these
# recipes. Recipes call pipeline/<name>.py today, but the implementation can
# move to shell/Rust/etc. here WITHOUT changing the command surface.

set positional-arguments

python    := env_var_or_default("AI_BRIEF_PYTHON", "python3.13")
pipeline  := env_var_or_default("PIPELINE_DIR", justfile_directory() / "pipeline")
notebooks := env_var_or_default("NOTEBOOKS_DIR", justfile_directory() / "notebooks")

# Raw sources -> normalized documents/chunks JSONL.
ingest *args:
    #!/usr/bin/env bash
    set -euo pipefail
    f="{{pipeline}}/ingest.py"
    [ -f "$f" ] || { echo "not implemented: create pipeline/ingest.py" >&2; exit 1; }
    exec "{{python}}" "$f" "$@"

# Embed chunks and build/update the local vector index.
index *args:
    #!/usr/bin/env bash
    set -euo pipefail
    f="{{pipeline}}/index.py"
    [ -f "$f" ] || { echo "not implemented: create pipeline/index.py" >&2; exit 1; }
    exec "{{python}}" "$f" "$@"

# Generate the evidence-backed brief (main demo command).
brief *args:
    #!/usr/bin/env bash
    set -euo pipefail
    f="{{pipeline}}/brief.py"
    [ -f "$f" ] || { echo "not implemented: create pipeline/brief.py" >&2; exit 1; }
    exec "{{python}}" "$f" "$@"

# Lightweight pipeline regression checks.
ai-eval *args:
    #!/usr/bin/env bash
    set -euo pipefail
    f="{{pipeline}}/eval.py"
    [ -f "$f" ] || { echo "not implemented: create pipeline/eval.py" >&2; exit 1; }
    exec "{{python}}" "$f" "$@"

# Report whether the environment is ready on this machine.
ai-doctor *args:
    #!/usr/bin/env bash
    set -euo pipefail
    f="{{pipeline}}/doctor.py"
    [ -f "$f" ] || { echo "not implemented: create pipeline/doctor.py" >&2; exit 1; }
    exec "{{python}}" "$f" "$@"

# Open JupyterLab on the same env/paths/backend (exploration surface).
notebook *args:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! command -v jupyter-lab >/dev/null 2>&1; then
        echo "not available: jupyter-lab is not on PATH (is ai-notebook composed in?)" >&2
        exit 1
    fi
    exec jupyter-lab --notebook-dir="{{notebooks}}" "$@"

================================================================
FILE: .flox/env/manifest.toml
================================================================
schema-version = "1.13.0"

[install]

[vars]

[hook]
on-activate = '''
# Project layout + backend selection (all overridable at activation time).
export AI_BRIEF_ROOT="$FLOX_ENV_PROJECT"
export SOURCES_DIR="${SOURCES_DIR:-$AI_BRIEF_ROOT/sources}"
export WORK_DIR="${WORK_DIR:-$AI_BRIEF_ROOT/work}"
export REPORTS_DIR="${REPORTS_DIR:-$AI_BRIEF_ROOT/reports}"
export PROMPTS_DIR="${PROMPTS_DIR:-$AI_BRIEF_ROOT/prompts}"
export EVALS_DIR="${EVALS_DIR:-$AI_BRIEF_ROOT/evals}"
export NOTEBOOKS_DIR="${NOTEBOOKS_DIR:-$AI_BRIEF_ROOT/notebooks}"
export PIPELINE_DIR="${PIPELINE_DIR:-$AI_BRIEF_ROOT/pipeline}"
export AI_BRIEF_JUSTFILE="${AI_BRIEF_JUSTFILE:-$AI_BRIEF_ROOT/justfile}"
export AI_BRIEF_PYTHON="${AI_BRIEF_PYTHON:-python3.13}"

# Backend: honor an explicit AI_BACKEND, else detect (cuda / mps / cpu).
if [ -z "${AI_BACKEND:-}" ]; then
  if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    AI_BACKEND="mps"
  elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    AI_BACKEND="cuda"
  else
    AI_BACKEND="cpu"
  fi
fi
export AI_BACKEND

# Idempotent project skeleton.
mkdir -p "$SOURCES_DIR" "$WORK_DIR" "$REPORTS_DIR" "$PROMPTS_DIR" \
         "$EVALS_DIR" "$NOTEBOOKS_DIR" "$PIPELINE_DIR" 2>/dev/null || true

cd "$FLOX_ENV_PROJECT"
'''

[profile]
bash = '''
_ai_brief_just() { just --justfile "${AI_BRIEF_JUSTFILE:-$FLOX_ENV_PROJECT/justfile}" "$@"; }
ingest()    { _ai_brief_just ingest "$@"; }
index()     { _ai_brief_just index "$@"; }
brief()     { _ai_brief_just brief "$@"; }
ai-eval()   { _ai_brief_just ai-eval "$@"; }
notebook()  { _ai_brief_just notebook "$@"; }
ai-doctor() { _ai_brief_just ai-doctor "$@"; }
'''
zsh = '''
_ai_brief_just() { just --justfile "${AI_BRIEF_JUSTFILE:-$FLOX_ENV_PROJECT/justfile}" "$@"; }
ingest()    { _ai_brief_just ingest "$@"; }
index()     { _ai_brief_just index "$@"; }
brief()     { _ai_brief_just brief "$@"; }
ai-eval()   { _ai_brief_just ai-eval "$@"; }
notebook()  { _ai_brief_just notebook "$@"; }
ai-doctor() { _ai_brief_just ai-doctor "$@"; }
'''
fish = '''
function _ai_brief_just
    set -l jf "$FLOX_ENV_PROJECT/justfile"
    test -n "$AI_BRIEF_JUSTFILE"; and set jf "$AI_BRIEF_JUSTFILE"
    just --justfile "$jf" $argv
end
function ingest;    _ai_brief_just ingest $argv;    end
function index;     _ai_brief_just index $argv;     end
function brief;     _ai_brief_just brief $argv;     end
function ai-eval;   _ai_brief_just ai-eval $argv;   end
function notebook;  _ai_brief_just notebook $argv;  end
function ai-doctor; _ai_brief_just ai-doctor $argv; end
'''

[services]

## Composed components ------------------------------------------------
[include]
environments = [
    { dir = "envs/ai-base" },
    { dir = "envs/ai-ingest" },
    { dir = "envs/ai-embeddings" },
    { dir = "envs/ai-notebook" },
]

[build]

[options]
systems = ["x86_64-linux", "aarch64-linux", "aarch64-darwin"]

================================================================
FILE: envs/ai-base/.flox/env/manifest.toml
================================================================
schema-version = "1.13.0"

[install]
## core python
python3.pkg-path = "python3"
python3.version = "3.13.13"

## other essentials
git.pkg-path = "git"
jq.pkg-path = "jq"
ripgrep.pkg-path = "ripgrep"
just.pkg-path = "just"
uv.pkg-path = "uv"

[vars]
ENV_NAME = "ai-base"


[options]
systems = [
   "aarch64-darwin",
   "aarch64-linux",
#   "x86_64-darwin",
   "x86_64-linux",
]
# Uncomment to disable CUDA detection.
# cuda-detection = false

================================================================
FILE: envs/ai-ingest/.flox/env/manifest.toml
================================================================
schema-version = "1.13.0"

## ai-ingest component — parser CLIs + Python parsing/normalization/chunking
## libs. The Python libs share pkg-group "ingest-py" so they co-resolve on
## one catalog page (isolated from the base/embeddings pages), matching the
## coherence pattern used elsewhere in the stack.
## (webvtt-py is not in the catalog — omitted; SRT is covered by `srt`.)
[install]
# --- parser CLIs (toplevel group) ---
poppler-utils.pkg-path = "poppler-utils"   # pdftotext, pdfinfo
mupdf.pkg-path = "mupdf"                    # mutool
pandoc.pkg-path = "pandoc"
html2text.pkg-path = "html2text"
yt-dlp.pkg-path = "yt-dlp"
dos2unix.pkg-path = "dos2unix"

# --- Python: PDF parser ---
pymupdf.pkg-path = "python313Packages.pymupdf"
pymupdf.pkg-group = "ingest-py"
pdfplumber.pkg-path = "python313Packages.pdfplumber"
pdfplumber.version = "0.11.9"   # 0.11.7 pulls a pandas-stubs build with failing tests
pdfplumber.pkg-group = "ingest-py"
# --- Python: Markdown parser ---
markdown.pkg-path = "python313Packages.markdown"
markdown.pkg-group = "ingest-py"
markdown-it-py.pkg-path = "python313Packages.markdown-it-py"
markdown-it-py.pkg-group = "ingest-py"
# --- Python: HTML parser ---
beautifulsoup4.pkg-path = "python313Packages.beautifulsoup4"
beautifulsoup4.pkg-group = "ingest-py"
lxml.pkg-path = "python313Packages.lxml"
lxml.pkg-group = "ingest-py"
readability-lxml.pkg-path = "python313Packages.readability-lxml"
readability-lxml.pkg-group = "ingest-py"
# --- Python: transcript parser ---
srt.pkg-path = "python313Packages.srt"
srt.pkg-group = "ingest-py"
# --- Python: document normalization ---
unstructured.pkg-path = "python313Packages.unstructured"
unstructured.pkg-group = "ingest-py"
ftfy.pkg-path = "python313Packages.ftfy"
ftfy.pkg-group = "ingest-py"
# --- Python: chunking ---
langchain-text-splitters.pkg-path = "python313Packages.langchain-text-splitters"
langchain-text-splitters.pkg-group = "ingest-py"
tiktoken.pkg-path = "python313Packages.tiktoken"
tiktoken.pkg-group = "ingest-py"
semchunk.pkg-path = "python313Packages.semchunk"
semchunk.pkg-group = "ingest-py"

[vars]

[hook]

[profile]

[services]

[include]

[build]

[options]
systems = ["x86_64-linux", "aarch64-linux", "aarch64-darwin"]

================================================================
FILE: envs/ai-embeddings/.flox/env/manifest.toml
================================================================
schema-version = "1.13.0"

## ai-embeddings component — empty scaffold. Add manually, e.g.:
##   torch-cuda.pkg-path  = "flox-cuda/python3Packages.torch"   # Linux, GPU/CPU fallback
##   torch-cuda.systems   = ["x86_64-linux", "aarch64-linux"]
##   torch-cuda.pkg-group = "cuda"     # flox-cuda is a separate catalog page
##   torch-mps.pkg-path   = "python313Packages.torch"           # Apple Silicon (Metal/MPS)
##   torch-mps.systems    = ["aarch64-darwin"]
##   sentence-transformers.pkg-path = "python313Packages.sentence-transformers"
##   chromadb.pkg-path    = "python313Packages.chromadb"
[install]
torch-cuda.pkg-path  = "flox-cuda/python3Packages.torch"   # Linux, GPU/CPU fallback
torch-cuda.systems   = ["x86_64-linux", "aarch64-linux"]
torch-cuda.pkg-group = "cuda"     # flox-cuda is a separate catalog page

torch-mps.pkg-path   = "python313Packages.torch"           # Apple Silicon (Metal/MPS)
torch-mps.systems    = ["aarch64-darwin"]
torch-mps.pkg-group = "mps-torch"
torch-mps.priority = 6

sentence-transformers.pkg-path = "python313Packages.sentence-transformers"
sentence-transformers.pkg-group = "python-deps"
sentence-transformers.priority = 6

chromadb.pkg-path    = "python313Packages.chromadb"
chromadb.pkg-group = "chromadb"
chromadb.priority = 7

[vars]

[hook]

[profile]

[services]

[include]

[build]

[options]
systems = ["x86_64-linux", "aarch64-linux", "aarch64-darwin"]

================================================================
FILE: pipeline/doctor.py
================================================================
#!/usr/bin/env python3
"""ai-brief: doctor — report whether the environment is ready on this machine.

This is intentionally NOT a stub: it must make the real state obvious and
actionable, including honestly reporting the PyTorch/glibc failure. Exit
status is nonzero if any critical check fails so it is usable in CI.
"""
import argparse
import os
import platform
import shutil
import sys

OK, FAIL, WARN = "OK", "FAIL", "WARN"


def status(mark: str, label: str, detail: str = "") -> None:
    print(f"  [{mark:^4}] {label}" + (f" — {detail}" if detail else ""))


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
        print("Next: fix PyTorch (align base/glibc), then run `ai-doctor` again.")
        return 1
    print("READY. Next: `ingest` -> `index` -> `brief`  (or `notebook` to explore).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

================================================================
FILE: pipeline/ingest.py
================================================================
#!/usr/bin/env python3
"""ai-brief: ingest stage (STUB).

Raw sources -> normalized documents/chunks JSONL.
Implement: scan $SOURCES_DIR, parse supported files, normalize text, attach
source metadata, split into chunks, and write deterministic:
    $WORK_DIR/documents.jsonl
    $WORK_DIR/chunks.jsonl
"""
import argparse
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="ingest", description="normalize raw sources into JSONL")
    parser.add_argument("paths", nargs="*", help="optional explicit source paths")
    parser.parse_args()

    sources = os.environ.get("SOURCES_DIR", "sources")
    work = os.environ.get("WORK_DIR", "work")
    print("not implemented: pipeline/ingest.py is a stub", file=sys.stderr)
    print(f"  implement: scan {sources}/ -> write {work}/documents.jsonl, {work}/chunks.jsonl",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

================================================================
FILE: pipeline/index.py
================================================================
#!/usr/bin/env python3
"""ai-brief: index stage (STUB).

Chunks -> embeddings -> local vector index.
Implement: load $WORK_DIR/chunks.jsonl, embed with the active AI_BACKEND,
build/update $WORK_DIR/index/, write $WORK_DIR/index-manifest.json linking
vectors back to source chunks. Be idempotent: unchanged chunks should not
be re-embedded.
"""
import argparse
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="index", description="embed chunks and build the vector index")
    parser.add_argument("paths", nargs="*")
    parser.parse_args()

    work = os.environ.get("WORK_DIR", "work")
    backend = os.environ.get("AI_BACKEND", "cpu")
    print("not implemented: pipeline/index.py is a stub", file=sys.stderr)
    print(f"  implement: {work}/chunks.jsonl -> embed (backend={backend}) -> {work}/index/, {work}/index-manifest.json",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

================================================================
FILE: pipeline/brief.py
================================================================
#!/usr/bin/env python3
"""ai-brief: brief stage (STUB) — the main demo command.

Chunks + index + prompts -> evidence-backed brief.
Implement: retrieve relevant chunks, cluster/group themes, summarize the
important patterns, cite source chunks, flag uncertainty/gaps, and write:
    $REPORTS_DIR/brief.md
    $REPORTS_DIR/brief.json
"""
import argparse
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="brief", description="generate the evidence-backed brief")
    parser.add_argument("paths", nargs="*")
    parser.parse_args()

    reports = os.environ.get("REPORTS_DIR", "reports")
    print("not implemented: pipeline/brief.py is a stub", file=sys.stderr)
    print(f"  implement: retrieve + summarize + cite -> {reports}/brief.md, {reports}/brief.json",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

================================================================
FILE: pipeline/eval.py
================================================================
#!/usr/bin/env python3
"""ai-brief: eval stage (STUB) — invoked via the `ai-eval` command.

Check whether the pipeline still produces acceptable output.
Implement: run the pipeline on $EVALS_DIR/fixtures/, check report shape and
required sections, verify citations point to real chunks, optionally compare
against $EVALS_DIR/expected/, and write $WORK_DIR/eval-results.json. Fail
clearly (nonzero) on regression. Keep it lightweight — no huge model.
"""
import argparse
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="ai-eval", description="pipeline regression checks")
    parser.add_argument("paths", nargs="*")
    parser.parse_args()

    evals = os.environ.get("EVALS_DIR", "evals")
    work = os.environ.get("WORK_DIR", "work")
    print("not implemented: pipeline/eval.py is a stub", file=sys.stderr)
    print(f"  implement: run {evals}/fixtures -> checks -> {work}/eval-results.json (nonzero on regression)",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

