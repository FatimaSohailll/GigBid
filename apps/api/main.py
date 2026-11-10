"""FastAPI application: `uvicorn apps.api.main:app`."""

import os
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore

from agent.checkpoint import checkpoint_serde
from agent.config import get_config
from agent.graphs.bid_graph import build_bid_graph
from agent.graphs.conversation_graph import build_conversation_graph
from agent.llm import ModelRouter
from agent.marketplace_port import current_trace_id
from agent.memory.store import LongTermMemory
from agent.runtime import AgentRuntime
from apps.api.auth.security import TokenVault
from apps.api.db import Repository, open_pool
from apps.api.routers import analytics, approvals, auth, jobs, messages, proposals, runs, settings
from apps.api.services import ApiServices, marketplace_health_url
from rag.index.corpus import CorpusStore

_TRACE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


async def build_services() -> tuple[ApiServices, Callable[[], Awaitable[None]]]:
    config = get_config()
    pool = open_pool(os.environ["DATABASE_URL"])
    await pool.open()
    repository = Repository(pool)
    await repository.setup()
    saver = AsyncPostgresSaver(pool, serde=checkpoint_serde())  # type: ignore[arg-type]
    await saver.setup()
    store = AsyncPostgresStore(pool)  # type: ignore[arg-type]
    await store.setup()
    queue = await create_pool(RedisSettings.from_dsn(os.environ["REDIS_URL"]))
    memory = LongTermMemory(store, config.memory)
    # The API only reads graph state; the worker runs the nodes.
    reader: Any = None
    runtime = AgentRuntime(config, reader, reader, memory)
    key = os.environ.get("TOKEN_ENCRYPTION_KEY", "")
    services = ApiServices(
        config=config,
        repository=repository,
        queue=queue,
        bid_graph=build_bid_graph(runtime, saver),
        conversation_graph=build_conversation_graph(runtime, saver),
        memory=memory,
        vault=TokenVault(key, repository) if key and key != "replace_me" else None,
        upload_dir=Path(os.environ.get("UPLOAD_DIR", "data/processed/uploads")),
        marketplace_health_url=marketplace_health_url(),
        cookie_secure=os.environ.get("APP_ENV") == "production",
        llm=ModelRouter(config),
        corpus=CorpusStore(
            Path(os.environ.get("RAG_DATA_DIR", "data/processed/rag")) / "corpus.sqlite3"
        ),
    )

    async def close() -> None:
        await queue.aclose()
        await pool.close()

    return services, close


def create_app(services: ApiServices | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if services is not None:
            app.state.services = services
            yield
            return
        app.state.services, close = await build_services()
        yield
        await close()

    app = FastAPI(title="GigBid API", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def trace(request: Request, call_next: Any) -> Response:
        """One trace id per request, carried from here to the graph and the audit log."""
        supplied = request.headers.get("x-trace-id", "")
        trace_id = supplied if _TRACE_ID.match(supplied) else uuid.uuid4().hex
        request.state.trace_id = trace_id
        token = current_trace_id.set(trace_id)
        try:
            response: Response = await call_next(request)
        finally:
            current_trace_id.reset(token)
        response.headers["X-Trace-Id"] = trace_id
        return response

    @app.get("/health", tags=["system"])
    async def health(request: Request) -> JSONResponse:
        current: ApiServices = request.app.state.services
        try:
            await current.repository.ping()
            await current.redis.ping()
        except Exception:
            return JSONResponse({"status": "degraded"}, status_code=503)
        return JSONResponse({"status": "ok", "service": "gigbid-api"})

    for module in (auth, jobs, proposals, approvals, messages, runs, analytics, settings):
        app.include_router(module.router)
    return app


app = create_app()
