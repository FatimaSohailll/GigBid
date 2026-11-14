"""Rules every GigBid tool must follow, checked on all three servers through in-memory clients.

The snapshots catch any change; these tests say which changes are never allowed.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect
from mcp.types import Tool

from mcp_servers.marketplace.server import CallContext
from mcp_servers.marketplace.server import build_server as build_marketplace
from mcp_servers.marketplace.store import ServerStore
from mcp_servers.portfolio.server import build_server as build_portfolio
from mcp_servers.tool_descriptions import TOOL_DESCRIPTIONS
from mcp_servers.workspace.server import WorkspaceStore
from mcp_servers.workspace.server import build_server as build_workspace
from tests.support.rag_fakes import memory_components


def _no_port(_: str) -> Any:
    raise AssertionError("listing tools must not touch the marketplace")


@asynccontextmanager
async def _servers(tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    store = ServerStore(tmp_path / "m.sqlite3")
    try:
        yield {
            "marketplace": build_marketplace(_no_port, store, lambda _: CallContext("u", "t")),
            "portfolio": build_portfolio(memory_components(tmp_path), lambda _: "t"),
            "workspace": build_workspace(WorkspaceStore(tmp_path / "w.sqlite3"), lambda _: "t"),
        }
    finally:
        store.close()


@pytest.fixture
async def tools(tmp_path: Path) -> list[tuple[str, Tool]]:
    listed = []
    async with _servers(tmp_path) as servers:
        for name, server in servers.items():
            async with connect(server) as session:
                listed += [(name, tool) for tool in (await session.list_tools()).tools]
    return listed


def _writes(tools: list[tuple[str, Tool]]) -> list[tuple[str, Tool]]:
    return [(server, tool) for server, tool in tools if not tool.annotations.readOnlyHint]


async def test_every_tool_is_annotated_and_has_an_output_schema(
    tools: list[tuple[str, Tool]],
) -> None:
    for server, tool in tools:
        assert tool.annotations is not None, f"{server}.{tool.name} has no annotations"
        assert tool.annotations.readOnlyHint is not None, f"{server}.{tool.name}: read or write?"
        assert tool.outputSchema, f"{server}.{tool.name} has no output schema"


async def test_every_write_takes_an_idempotency_key(tools: list[tuple[str, Tool]]) -> None:
    writes = _writes(tools)
    assert writes, "expected write tools"
    for server, tool in writes:
        assert "idempotency_key" in str(tool.inputSchema), f"{server}.{tool.name} has no key"
        assert tool.annotations.idempotentHint, f"{server}.{tool.name} should be idempotent"


async def test_marketplace_writes_can_be_dry_run(tools: list[tuple[str, Tool]]) -> None:
    for server, tool in _writes(tools):
        if server == "marketplace":
            assert "dry_run" in str(tool.inputSchema), f"{tool.name} cannot be previewed"


async def test_portfolio_is_read_only(tools: list[tuple[str, Tool]]) -> None:
    assert all(tool.annotations.readOnlyHint for server, tool in tools if server == "portfolio")


async def test_served_descriptions_are_the_frozen_ones(tools: list[tuple[str, Tool]]) -> None:
    served = {tool.name: tool.description for _, tool in tools}
    assert served == TOOL_DESCRIPTIONS
