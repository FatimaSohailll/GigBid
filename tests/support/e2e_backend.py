"""The real backend for Playwright, with only model replies scripted.

Runs DevSim, the MCP servers, the API and a worker in one process, on Postgres and Redis.
Usage: python -m tests.support.e2e_backend
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import httpx
import uvicorn
from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Worker, func
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore

from agent.checkpoint import checkpoint_serde
from agent.graphs.bid_graph import build_bid_graph
from agent.graphs.conversation_graph import build_conversation_graph
from agent.memory.store import LongTermMemory
from agent.runtime import AgentRuntime
from apps.api.db import Repository, open_pool
from apps.api.main import create_app
from apps.api.services import DEGRADED_KEY, ApiServices
from tests.support.graph_stack import fast_config, start_stack
from tests.support.rag_fakes import memory_components
from tests.support.scripted_llm import ScriptedLlm
from worker.tasks import (
    WorkerServices,
    delete_evidence,
    ingest_upload,
    run_thread,
    scout_search,
    sync_user,
)

PORT = int(os.environ.get("E2E_API_PORT", "8010"))


async def main() -> None:
    root = Path(tempfile.mkdtemp())
    stack = start_stack(root)
    base = fast_config()
    config = base.model_copy(
        update={"worker": base.worker.model_copy(update={"service_retry_seconds": 0.5})}
    )
    pool = open_pool(os.environ["TEST_DATABASE_URL"])
    await pool.open()
    repository = Repository(pool)
    await repository.setup()
    saver = AsyncPostgresSaver(pool, serde=checkpoint_serde())  # type: ignore[arg-type]
    await saver.setup()
    store = AsyncPostgresStore(pool)  # type: ignore[arg-type]
    await store.setup()
    queue = await create_pool(RedisSettings.from_dsn(os.environ["TEST_REDIS_URL"]))
    await queue.flushdb()
    memory = LongTermMemory(store, config.memory)
    llm = ScriptedLlm()
    runtime = AgentRuntime(config, llm, stack.gateway(config), memory)
    components = memory_components(root)
    services = ApiServices(
        config=config,
        repository=repository,
        queue=queue,
        bid_graph=build_bid_graph(runtime, saver),
        conversation_graph=build_conversation_graph(runtime, saver),
        memory=memory,
        vault=None,
        upload_dir=root / "uploads",
        marketplace_health_url=f"{stack.marketplace.url}/health",
        llm=llm,
        corpus=components.corpus,
    )
    app = create_app(services)

    @app.post("/__test/reset")
    async def reset() -> dict[str, str]:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{stack.simulator.url}/admin/reset",
                headers={"Authorization": "Bearer sim-admin"},
            )
        await queue.delete(DEGRADED_KEY)
        return {"status": "reset"}

    @app.post("/__test/marketplace/stop")
    async def stop_marketplace() -> dict[str, str]:
        await asyncio.to_thread(stack.marketplace.stop)
        return {"status": "stopped"}

    @app.post("/__test/marketplace/start")
    async def start_marketplace() -> dict[str, str]:
        await asyncio.to_thread(stack.restart_marketplace)
        await queue.delete(DEGRADED_KEY)
        return {"status": "started"}

    @app.post("/__test/client-reply")
    async def client_reply(body: dict[str, str]) -> dict[str, str]:
        """The client answers a submitted proposal; the worker then syncs, as its cron would."""
        async with httpx.AsyncClient() as client:
            sent = await client.post(
                f"{stack.simulator.url}/admin/proposals/{body['proposal_id']}/client-message",
                json={"body": body.get("text", "Can you start Monday?")},
                headers={"Authorization": "Bearer sim-admin"},
            )
            sent.raise_for_status()
        await queue.enqueue_job("sync_user", body["user_id"])
        return {"status": "replied"}

    @app.post("/__test/learn-style")
    async def learn_style(body: dict[str, str]) -> dict[str, str]:
        """Three drafts with the same edit, which the promotion rule turns into a style rule."""
        edit = {"before": "We are passionate engineers.", "after": "We are engineers."}
        for number in range(3):
            await memory.record_edits(body["user_id"], f"job-{number}", [edit])
        return {"status": "learned"}

    worker = Worker(
        functions=[
            func(run_thread, max_tries=200),
            func(scout_search, max_tries=200),
            sync_user,
            ingest_upload,
            delete_evidence,
        ],
        redis_pool=queue,
        ctx={
            "services": WorkerServices(
                repository, queue, lambda _: runtime, saver, lambda: components
            ),
            "redis": queue,
        },
        handle_signals=False,
        poll_delay=0.1,
    )
    worker_task = asyncio.create_task(worker.main())
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", loop="none")
    )
    try:
        await server.serve()
    finally:
        worker_task.cancel()
        stack.stop()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
