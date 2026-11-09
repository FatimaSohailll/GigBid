"""The full tool path over real sockets: Toolbox -> MCP over HTTP -> adapter -> DevSim."""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from agent.config import get_config, load_config
from agent.marketplace_port import current_trace_id
from agent.mcp_client import Toolbox, ToolServerUnavailable, connections_from_env
from devsim.config import FaultConfig
from mcp_servers.marketplace.server import (
    StaticBearerVerifier,
    UserAccess,
    build_server,
    gigsim_port_factory,
)
from mcp_servers.marketplace.store import ServerStore
from mcp_servers.marketplace.tests.harness import LiveServer, devsim_app, free_port

USERS = {"mcp-ai": UserAccess("ai_ml_engineer", "sim-ai")}


@dataclass
class Stack:
    mcp_server: LiveServer
    simulator: LiveServer
    store: ServerStore
    connections: dict[str, Any]
    build_app: Any

    def toolbox(self) -> Toolbox:
        config = get_config().model_copy(
            update={
                "mcp_client": get_config().mcp_client.model_copy(
                    update={"backoff_base_seconds": 0.01, "max_retries": 1}
                )
            }
        )
        return Toolbox(self.connections, config)


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[Stack]:
    simulator = LiveServer(devsim_app(FaultConfig()), free_port())
    simulator.start()
    store = ServerStore(tmp_path / "audit.sqlite3")
    port = free_port()
    url = f"http://127.0.0.1:{port}"

    def build_app() -> Any:
        return build_server(
            gigsim_port_factory(USERS, simulator.url, get_config().marketplace),
            store,
            token_verifier=StaticBearerVerifier(USERS),
            auth=AuthSettings(
                issuer_url=AnyHttpUrl(url),
                resource_server_url=AnyHttpUrl(url),
                validate_token_resource=False,
            ),
            host="127.0.0.1",
            port=port,
        ).streamable_http_app()

    server = LiveServer(build_app(), port)
    server.start()
    connections = connections_from_env(
        {"MCP_MARKETPLACE_URL": f"{url}/mcp", "MCP_MARKETPLACE_BEARER_TOKEN": "mcp-ai"}
    )
    built = Stack(server, simulator, store, connections, build_app)
    yield built
    server.stop()
    simulator.stop()


def tool_named(tools: list[Any], name: str) -> Any:
    return next(tool for tool in tools if tool.name == name)


def structured(result: Any) -> Any:
    """Tool output arrives as content blocks whose first block is the JSON result."""
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, list):
        result = result[0]["text"]
    return json.loads(result)


async def test_requests_without_a_valid_bearer_are_refused(stack: Stack) -> None:
    url = f"{stack.mcp_server.url}/mcp"
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = {"Accept": "application/json, text/event-stream"}
    async with httpx.AsyncClient() as client:
        missing = await client.post(url, json=body, headers=headers)
        wrong = await client.post(url, json=body, headers={**headers, "Authorization": "Bearer no"})
    assert missing.status_code == 401
    assert wrong.status_code == 401


async def test_health_is_public(stack: Stack) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{stack.mcp_server.url}/health")
    assert response.json()["status"] == "ok"


async def test_each_agent_gets_only_its_allowlisted_tools(stack: Stack) -> None:
    box = stack.toolbox()
    assert [tool.name for tool in await box.tools_for("scout")] == ["search_jobs"]
    assert await box.tools_for("analyst") == []
    submission = {tool.name for tool in await box.tools_for("submission")}
    assert submission == {
        "create_proposal_draft",
        "submit_proposal",
        "send_message",
        "get_connects_balance",
    }
    liaison = {tool.name for tool in await box.tools_for("client_liaison")}
    assert "submit_proposal" not in liaison and "send_message" not in liaison


async def test_tool_call_works_end_to_end_and_carries_the_trace(stack: Stack) -> None:
    box = stack.toolbox()
    search = tool_named(await box.tools_for("scout"), "search_jobs")
    token = current_trace_id.set("trace-e2e-1")
    try:
        result = structured(await search.ainvoke({"query": "churn"}))
    finally:
        current_trace_id.reset(token)
    assert {job["job_id"] for job in result["items"]} == {"job-001", "job-002"}
    row = stack.store.audit_rows()[-1]
    assert (row["tool"], row["user_id"], row["trace_id"]) == (
        "search_jobs",
        "ai_ml_engineer",
        "trace-e2e-1",
    )


async def test_retried_write_over_http_spends_once(stack: Stack) -> None:
    box = stack.toolbox()
    tools = await box.tools_for("submission")
    draft = structured(
        await tool_named(tools, "create_proposal_draft").ainvoke(
            {
                "request": {
                    "job_id": "job-001",
                    "cover_letter": "Grounded letter.",
                    "screening_answers": ["Answer."],
                    "bid_amount": 900,
                    "idempotency_key": "http-draft-1",
                }
            }
        )
    )
    request = {
        "request": {
            "proposal_id": draft["proposal"]["proposal_id"],
            "idempotency_key": "http-submit-1",
        }
    }
    submit = tool_named(tools, "submit_proposal")
    first = structured(await submit.ainvoke(request))
    second = structured(await submit.ainvoke(request))
    balance = structured(await tool_named(tools, "get_connects_balance").ainvoke({}))
    assert first["replayed"] is False and second["replayed"] is True
    assert balance["spent"] == 8


async def test_killing_the_server_is_detected_and_recovers(stack: Stack) -> None:
    box = stack.toolbox()
    search = tool_named(await box.tools_for("scout"), "search_jobs")
    assert structured(await search.ainvoke({"query": "churn"}))["items"]

    stack.mcp_server.stop()
    with pytest.raises(ToolServerUnavailable) as caught:
        await search.ainvoke({"query": "churn"})
    assert caught.value.server == "marketplace"

    restarted = LiveServer(stack.build_app(), stack.mcp_server.port)
    restarted.start()
    stack.mcp_server = restarted
    assert structured(await search.ainvoke({"query": "churn"}))["items"]


async def test_simulator_outage_is_reported_as_marketplace_unavailable(stack: Stack) -> None:
    box = stack.toolbox()
    search = tool_named(await box.tools_for("scout"), "search_jobs")
    stack.simulator.stop()
    try:
        result = await search.ainvoke({"query": "churn"})
    finally:
        stack.simulator.start()
    assert "marketplace_unavailable" in str(result)


def test_live_mode_fails_clearly_until_its_adapter_exists() -> None:
    config = load_config(env={"MARKETPLACE_MODE": "upwork_live"}).marketplace
    with pytest.raises(ValueError, match="Phase 13"):
        gigsim_port_factory(USERS, "http://unused", config)
