"""Arq worker: `arq worker.main.WorkerSettings`."""

import os
from functools import cache
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from arq.worker import func
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore

from agent.checkpoint import checkpoint_serde
from agent.config import get_config
from agent.mcp_client import Toolbox, ToolGateway, github_connection
from agent.runtime import AgentRuntime, default_runtime
from apps.api.db import Repository, open_pool
from mcp_servers.tenant_auth import mint_user_token
from worker.tasks import (
    WorkerServices,
    delete_evidence,
    ingest_github,
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
    ingest_github,
    delete_evidence,
]


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
    signing_key = os.environ["MCP_TENANT_SIGNING_KEY"]
    runtimes: dict[str, AgentRuntime] = {}

    def runtime_for(user: dict[str, Any]) -> AgentRuntime:
        if user["id"] not in runtimes:
            bearer = mint_user_token(signing_key, user["id"], user["persona"])
            runtimes[user["id"]] = default_runtime(store, config, bearer)
        return runtimes[user["id"]]

    ctx["pool"] = pool

    def github() -> ToolGateway | None:
        connection = github_connection()
        return ToolGateway(Toolbox(connection, config, False)) if connection else None

    ctx["services"] = WorkerServices(
        repository, ctx["redis"], runtime_for, saver, cache(open_components), github
    )


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
