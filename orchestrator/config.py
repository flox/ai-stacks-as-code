"""Central configuration, resolved from the environment with defaults.

The Flox `ai-orchestration` component exports the runtime variables consumed
here (TEMPORAL_*, AI_BRIEF_STATE_DIR, ASK_FLOX_*_REPO, ...). Nothing in this
module performs IO; it only resolves paths and static config so both the pure
core and the Temporal layer share one source of truth.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# --- Schema versions --------------------------------------------------------
# Bump when the *meaning* of a hashed structure changes, so old and new
# identities never silently collide. These participate in the hashes below.
IDENTITY_SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA_VERSION = 1
INDEX_SCHEMA_VERSION = 1

# Stable Workflow ID of the singleton publication authority (§2.3 / §8).
AUTHORITY_WORKFLOW_ID = "ask-flox-pub-authority"

# --- Output-affecting build configuration (participates in candidate id) -----
# These mirror the existing pipeline defaults (pipeline/ingest.py, index.py).
CHUNKER = {"chunker": "semchunk", "target_tokens": 220, "max_tokens": 300}
EMBEDDING = {"engine": "onnx", "model": "onnx/all-MiniLM-L6-v2", "dimension": 384}
CHROMA = {"collection": "ai_brief", "space": "cosine"}

# Pipeline source files whose content defines the deterministic transform, and
# therefore participates in candidate identity (a code change => new candidate).
PIPELINE_CODE_FILES = ("ingest.py", "index.py", "common.py")


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def project_root() -> Path:
    return Path(_env("AI_BRIEF_ROOT", _env("FLOX_ENV_PROJECT", os.getcwd()))).resolve()


def state_dir() -> Path:
    return Path(_env("AI_BRIEF_STATE_DIR", str(project_root() / "state")))


def store_dir() -> Path:
    return Path(_env("AI_BRIEF_STORE_DIR", str(state_dir() / "store")))


def log_dir() -> Path:
    return Path(_env("AI_BRIEF_LOG_DIR", str(state_dir() / "logs")))


def pipeline_dir() -> Path:
    return Path(_env("PIPELINE_DIR", str(project_root() / "pipeline")))


def python_bin() -> str:
    return _env("AI_BRIEF_PYTHON", "python3.13")


@dataclass(frozen=True)
class TemporalConfig:
    address: str
    namespace: str
    task_queue: str


def temporal_config() -> TemporalConfig:
    return TemporalConfig(
        address=_env("TEMPORAL_ADDRESS", "127.0.0.1:7233"),
        namespace=_env("TEMPORAL_NAMESPACE", "default"),
        task_queue=_env("TEMPORAL_TASK_QUEUE", "ask-flox-pipeline"),
    )


def default_source_specs() -> list[dict]:
    """The two independently-changing content repositories (§ prompt).

    Content roots and admission rules are faithful to today's corpus/:
    docs = the flox/docs .mdx tree (minus repo-meta files); blog = the
    flox/floxwebsite src/posts .mdx.
    """
    home = Path.home()
    return [
        {
            "name": "docs",
            "repo": _env("ASK_FLOX_DOCS_REPO", str(home / "dev" / "flox-docs")),
            "include": ["**/*.mdx", "**/*.md"],
            "exclude": ["**/README.md", "**/AGENTS.md", "**/CONTRIBUTING.md",
                        "README.md", "AGENTS.md", "CONTRIBUTING.md"],
        },
        {
            "name": "blog",
            "repo": _env("ASK_FLOX_BLOG_REPO", str(home / "dev" / "floxwebsite")),
            "include": ["src/posts/**/*.mdx"],
            "exclude": [],
        },
    ]
