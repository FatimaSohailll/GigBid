"""Shared application services, and read access to graph threads."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from fastapi import Cookie, Depends, Header, HTTPException, Request
from langgraph.graph.state import CompiledStateGraph
from redis.asyncio import Redis

from agent.config import AppConfig
from agent.llm import LlmGateway
from agent.memory.store import LongTermMemory
from apps.api.auth.security import TokenVault, hash_token
from apps.api.db import Repository

SESSION_COOKIE = "gigbid_session"
DEGRADED_KEY = "gigbid:degraded:marketplace"


@dataclass
class ApiServices:
    config: AppConfig
    repository: Repository
    queue: Any  # an ArqRedis pool
    bid_graph: CompiledStateGraph[Any, Any, Any, Any]
    conversation_graph: CompiledStateGraph[Any, Any, Any, Any]
    memory: LongTermMemory
    vault: TokenVault | None
    upload_dir: Path
    marketplace_health_url: str | None
    cookie_secure: bool = False
    llm: LlmGateway | None = None

    @property
    def redis(self) -> Redis:
        redis: Redis = self.queue
        return redis

    def graph(self, kind: str) -> CompiledStateGraph[Any, Any, Any, Any]:
        return self.bid_graph if kind == "bid" else self.conversation_graph


def marketplace_health_url(env: Mapping[str, str] | None = None) -> str | None:
    environ = os.environ if env is None else env
    url = environ.get("MCP_MARKETPLACE_URL")
    return f"{url.removesuffix('/mcp')}/health" if url else None


async def read_thread(
    services: ApiServices,
    kind: str,
    thread_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return a thread's state and any pending approval, always read from the checkpoint."""
    config: Any = {"configurable": {"thread_id": thread_id}}
    snapshot = await services.graph(kind).aget_state(config)
    pending = [item.value for task in snapshot.tasks for item in task.interrupts]
    return dict(snapshot.values), (pending[0] if pending else None)


def get_services(request: Request) -> ApiServices:
    services: ApiServices = request.app.state.services
    return services


Services = Annotated[ApiServices, Depends(get_services)]


async def current_user(
    services: Services,
    session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    token = session
    if token is None and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    user = await services.repository.session_user(hash_token(token)) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return user


CurrentUser = Annotated[dict[str, Any], Depends(current_user)]


def bid_thread(user_id: str, job_id: str) -> str:
    return f"bid:{user_id}:{job_id}"


def conversation_thread(user_id: str, conversation_id: str) -> str:
    return f"conv:{user_id}:{conversation_id}"
