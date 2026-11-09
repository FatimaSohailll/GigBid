"""Arq worker: `arq worker.main.WorkerSettings`."""

import os
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from arq.worker import func
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore

from agent.checkpoint import checkpoint_serde
from agent.config import get_config
from agent.runtime import AgentRuntime, default_runtime
from apps.api.db import Repository, open_pool
from worker.tasks import (
    WorkerServices,
    ingest_upload,
    run_thread,
    scout_search,
    scout_tick,
    sync_tick,
    sync_user,
)

_JOB_TIMEOUT_SECONDS = 1_200
FUNCTIONS = [
    func(run_thread, max_tries=get_config().worker.service_max_retries),
    func(scout_search, max_tries=get_config().worker.service_max_retries),
    sync_user,
    ingest_upload,
]


def parse_persona_tokens(raw: str) -> dict[str, str]:
    """Parse ``persona=bearer`` pairs: the token each persona uses on the MCP servers."""
    tokens = dict(pair.strip().split("=", 1) for pair in raw.split(",") if pair.strip())
    if not tokens:
        raise ValueError("MCP_PERSONA_TOKENS must define at least one persona=token pair")
    return tokens


async def startup(ctx: dict[str, Any]) -> None:
    from rag.retriever import open_components

    config = get_config()
    pool = open_pool(os.environ["DATABASE_URL"])
    await pool.open()
    repository = Repository(pool)
    await repository.setup()
    saver = AsyncPostgresSaver(pool, serde=checkpoint_serde())  # type: ignore[arg-type]
    await saver.setup()
    store = AsyncPostgresStore(pool)  # type: ignore[arg-type]
    await store.setup()
    tokens = parse_persona_tokens(os.environ["MCP_PERSONA_TOKENS"])
    runtimes: dict[str, AgentRuntime] = {}

    def runtime_for(persona: str) -> AgentRuntime:
        if persona not in runtimes:
            runtimes[persona] = default_runtime(store, config, tokens[persona])
        return runtimes[persona]

    ctx["pool"] = pool
    ctx["services"] = WorkerServices(repository, ctx["redis"], runtime_for, saver, open_components)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["pool"].close()


class WorkerSettings:
    functions = FUNCTIONS
    cron_jobs = [
        cron(scout_tick, second=0, run_at_startup=False),
        cron(sync_tick, second=30, run_at_startup=False),
    ]
    on_startup = startup
    on_shutdown = shutdown
    job_timeout = _JOB_TIMEOUT_SECONDS
    keep_result = 3_600
    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379"))
