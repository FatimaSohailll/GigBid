"""The Portfolio server over real HTTP with bearer auth, through the agent toolbox."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from agent.config import get_config
from agent.mcp_client import Toolbox, connections_from_env
from mcp_servers.marketplace.tests.harness import LiveServer, free_port
from mcp_servers.portfolio.server import TenantBearerVerifier, build_server
from rag.ingest.pipeline import ingest_documents
from tests.support.rag_fakes import document, memory_components


@pytest.fixture
def live(tmp_path: Path) -> Iterator[LiveServer]:
    components = memory_components(tmp_path)
    ingest_documents(
        [document("case:alice:churn", "Churn model with XGBoost.")], "alice", components
    )
    ingest_documents([document("case:bob:secret", "Churn model for Bob.")], "bob", components)
    port = free_port()
    url = AnyHttpUrl(f"http://127.0.0.1:{port}")
    app = build_server(
        components,
        token_verifier=TenantBearerVerifier({"tok-alice": "alice"}),
        auth=AuthSettings(issuer_url=url, resource_server_url=url, validate_token_resource=False),
        host="127.0.0.1",
        port=port,
    ).streamable_http_app()
    server = LiveServer(app, port)
    server.start()
    yield server
    server.stop()


async def test_requests_without_a_valid_bearer_are_refused(live: LiveServer) -> None:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = {"Accept": "application/json, text/event-stream"}
    async with httpx.AsyncClient() as client:
        response = await client.post(f"{live.url}/mcp", json=body, headers=headers)
    assert response.status_code == 401


async def test_fit_matcher_gets_only_its_tools_and_only_its_tenant(live: LiveServer) -> None:
    box = Toolbox(
        connections_from_env(
            {"MCP_PORTFOLIO_URL": f"{live.url}/mcp", "MCP_PORTFOLIO_BEARER_TOKEN": "tok-alice"}
        ),
        get_config(),
    )
    tools = {tool.name: tool for tool in await box.tools_for("fit_matcher")}
    assert set(tools) == {"search_evidence", "get_case_study", "list_skills"}
    assert await box.tools_for("analyst") == []

    result: Any = await tools["search_evidence"].ainvoke({"requirement": "churn model"})
    block = result[0] if isinstance(result, list) else result
    items = json.loads(block["text"])["items"]
    assert [item["evidence_id"] for item in items] == ["case:alice:churn"]
