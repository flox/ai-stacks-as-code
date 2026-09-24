"""Temporal worker: hosts both workflows and all activities on one task queue.

    flox activate -s -- python -m orchestrator.worker

Services (the local Temporal dev server) start with `-s`; this process then
polls the task queue. Kill it and restart it any time — durable workflow state
lives in the Temporal server, so builds resume where they left off.
"""
from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from . import activities as A
from . import config
from .workflows import BuildWorkflow, PublicationAuthorityWorkflow

ACTIVITIES = [
    A.resolve_snapshot_activity,
    A.compute_candidate_activity,
    A.existing_artifact_activity,
    A.hydrate_activity,
    A.ingest_activity,
    A.index_activity,
    A.package_activity,
    A.evaluate_activity,
    A.is_published_activity,
    A.fence_generation_activity,
    A.verify_artifact_activity,
    A.promote_activity,
    A.register_generation_activity,
    A.request_authorization_activity,
]

# Bound concurrent activity execution (§12) so parallel builds cannot exhaust
# memory / file descriptors / embedding throughput on one worker.
MAX_CONCURRENT_ACTIVITIES = int(os.environ.get("AI_BRIEF_MAX_CONCURRENT_ACTIVITIES", "8"))


async def main() -> None:
    cfg = config.temporal_config()
    client = await Client.connect(cfg.address, namespace=cfg.namespace)
    worker = Worker(
        client,
        task_queue=cfg.task_queue,
        workflows=[BuildWorkflow, PublicationAuthorityWorkflow],
        activities=ACTIVITIES,
        max_concurrent_activities=MAX_CONCURRENT_ACTIVITIES,
    )
    print(f"ask-flox worker: task_queue={cfg.task_queue} address={cfg.address} ns={cfg.namespace}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
