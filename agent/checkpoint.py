"""Durable checkpoints in Postgres, and replay from any node."""

import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.postgres.aio import AsyncPostgresStore
from pydantic import BaseModel

from agent import handoffs, state


def normalise_url(url: str) -> str:
    """LangGraph uses psycopg directly, without SQLAlchemy's driver suffix."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def checkpoint_serde() -> JsonPlusSerializer:
    """Allow exactly our own state types to be restored from a checkpoint."""
    allowed = [
        (module.__name__, name)
        for module in (handoffs, state)
        for name, value in vars(module).items()
        if inspect.isclass(value)
        and value.__module__ == module.__name__
        and issubclass(value, BaseModel | Enum)
    ]
    return JsonPlusSerializer(allowed_msgpack_modules=allowed)


@asynccontextmanager
async def postgres_checkpointer(url: str) -> AsyncIterator[AsyncPostgresSaver]:
    async with AsyncPostgresSaver.from_conn_string(
        normalise_url(url), serde=checkpoint_serde()
    ) as saver:
        await saver.setup()
        yield saver


@asynccontextmanager
async def postgres_store(url: str) -> AsyncIterator[AsyncPostgresStore]:
    async with AsyncPostgresStore.from_conn_string(normalise_url(url)) as store:
        await store.setup()
        yield store


async def replay_from(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    thread_id: str,
    node: str,
) -> dict[str, Any]:
    """Re-run a thread from the latest checkpoint that is about to execute a node."""
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    async for snapshot in graph.aget_state_history(config):
        if node in snapshot.next:
            result: dict[str, Any] = await graph.ainvoke(None, snapshot.config)
            return result
    raise ValueError(f"Thread '{thread_id}' has no checkpoint before node '{node}'")
