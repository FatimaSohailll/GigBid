"""The official GitHub MCP server, reached with a real read-only token when one is available."""

import os

import pytest
from dotenv import load_dotenv

from agent.config import get_config
from agent.mcp_client import Toolbox, ToolGateway, github_connection
from rag.ingest.github import read_repository

load_dotenv()
ENV = {**os.environ, "MCP_GITHUB_URL": "https://api.githubcopilot.com/mcp/"}
pytestmark = pytest.mark.skipif(
    not ENV.get("GITHUB_PERSONAL_ACCESS_TOKEN"), reason="GITHUB_PERSONAL_ACCESS_TOKEN is not set"
)


def toolbox() -> Toolbox:
    connection = github_connection(ENV)
    assert connection is not None
    return Toolbox(connection, get_config(), handle_tool_errors=False)


async def test_ingestion_sees_only_its_allowlisted_read_tools() -> None:
    names = {tool.name for tool in await toolbox().tools_for("github_ingest")}
    assert names == {"get_me", "search_repositories", "get_file_contents"}


async def test_reads_a_public_file() -> None:
    blocks = await ToolGateway(toolbox()).call_text(
        "github_ingest",
        "get_file_contents",
        {"owner": "github", "repo": "github-mcp-server", "path": "LICENSE"},
    )
    assert any("MIT License" in block for block in blocks)


async def test_reads_a_real_repository_as_portfolio_documents() -> None:
    settings = get_config().rag.model_copy(update={"github_max_files": 2, "github_max_depth": 1})
    items = await read_repository(ToolGateway(toolbox()), "github", "github-mcp-server", settings)
    assert items[0].type == "readme"
    assert items[0].project == "github-mcp-server"
    assert items[0].source.startswith("github.com/github/github-mcp-server/")
    assert len(items[0].text) > 200


async def test_a_tool_outside_the_allowlist_is_refused() -> None:
    with pytest.raises(PermissionError):
        await ToolGateway(toolbox()).call_text("github_ingest", "search_code", {"query": "x"})
