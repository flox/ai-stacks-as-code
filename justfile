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

# Generate the extractive brief. PROOF-OF-CONCEPT ONLY: this shows the
# retrieval+index work end-to-end; it summarizes broadly and its keyword-themed
# output reads as off-topic on pointed questions. For "Ask Flox" Q&A use the
# `mcp` recipe (search_flox_docs) and let the agent synthesize, not `brief`.
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

# Serve retrieval over MCP (stdio): the "Ask Flox" tool for agents.
mcp *args:
    #!/usr/bin/env bash
    set -euo pipefail
    f="{{pipeline}}/mcp_server.py"
    [ -f "$f" ] || { echo "not implemented: create pipeline/mcp_server.py" >&2; exit 1; }
    exec "{{python}}" "$f" "$@"

# --- Durable orchestration (Temporal + Flox) -------------------------------
# The correctness spine: snapshot -> identity -> generation -> workflow ->
# artifact -> eval -> authorized publish. See orchestrator/ and docs/ARCHITECTURE.md.

# Run the Temporal worker (workflows + activities). Start the dev server first
# with `flox activate -s` (the temporal-dev service), then run this.
worker *args:
    #!/usr/bin/env bash
    set -euo pipefail
    exec "{{python}}" -m orchestrator.worker "$@"

# Submit a build. Pass source refs, e.g.: just pipeline-submit --docs-ref HEAD --wait
pipeline-submit *args:
    #!/usr/bin/env bash
    set -euo pipefail
    exec "{{python}}" -m orchestrator.cli submit "$@"

# Inspect a running build workflow's status.
pipeline-status *args:
    #!/usr/bin/env bash
    set -euo pipefail
    exec "{{python}}" -m orchestrator.cli status "$@"

# Show the active published index + its provenance.
pipeline-current *args:
    #!/usr/bin/env bash
    set -euo pipefail
    exec "{{python}}" -m orchestrator.cli current "$@"

# Query the singleton publication authority (generations, current winner).
pipeline-authority *args:
    #!/usr/bin/env bash
    set -euo pipefail
    exec "{{python}}" -m orchestrator.cli authority "$@"

# List builds awaiting human review.
pipeline-reviews *args:
    #!/usr/bin/env bash
    set -euo pipefail
    exec "{{python}}" -m orchestrator.cli reviews "$@"

# Submit a review decision: just pipeline-decide <workflow-id> approve|reject
pipeline-decide *args:
    #!/usr/bin/env bash
    set -euo pipefail
    exec "{{python}}" -m orchestrator.cli decide "$@"

# Orchestrator unit tests (pure correctness spine — no server needed).
otests *args:
    #!/usr/bin/env bash
    set -euo pipefail
    exec "{{python}}" -m unittest discover -s orchestrator/tests -t . "$@"

# Open JupyterLab on the same env/paths/backend (exploration surface).
notebook *args:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! command -v jupyter-lab >/dev/null 2>&1; then
        echo "not available: jupyter-lab is not on PATH (is ai-notebook composed in?)" >&2
        exit 1
    fi
    exec jupyter-lab --notebook-dir="{{notebooks}}" "$@"
