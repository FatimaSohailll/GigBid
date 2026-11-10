"""MCP connections, per-agent tool allowlists, timeouts, retries and degraded-mode detection."""

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx
from langchain_core.tools import BaseTool, ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from langchain_mcp_adapters.sessions import StreamableHttpConnection
from mcp.shared.exceptions import McpError

from agent.config import AppConfig, McpClientConfig, get_config
from agent.marketplace_port import current_trace_id

Handler = Callable[[MCPToolCallRequest], Awaitable[Any]]
_TRANSPORT_FAILURES = (
    httpx.TransportError,
    httpx.HTTPStatusError,
    McpError,
    TimeoutError,
    ConnectionError,
)


class ToolServerUnavailable(Exception):
    """An MCP server could not be reached after retries. Work should queue and recover."""

    def __init__(self, server: str, tool: str) -> None:
        super().__init__(f"MCP server '{server}' is unavailable (tool '{tool}').")
        self.server = server
        self.tool = tool


class ToolRateLimited(ToolServerUnavailable):
    """The service is healthy but busy. Work waits and retries, with no outage banner."""


def _is_transport_failure(error: BaseException) -> bool:
    if isinstance(error, BaseExceptionGroup):
        return all(_is_transport_failure(item) for item in error.exceptions)
    return isinstance(error, _TRANSPORT_FAILURES)


class ResilienceInterceptor:
    """Adds the trace id and retries failed connections.

    A tool that ran and reported an error is not retried here. Retrying a write is safe
    because the caller repeats the same idempotency key.
    """

    def __init__(
        self,
        config: McpClientConfig,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._config = config
        self._sleep = sleep

    async def __call__(self, request: MCPToolCallRequest, handler: Handler) -> Any:
        trace_id = current_trace_id.get()
        if trace_id is not None:
            request = request.override(headers={**(request.headers or {}), "X-Trace-Id": trace_id})
        for attempt in range(self._config.max_retries + 1):
            try:
                return await asyncio.wait_for(handler(request), self._config.tool_timeout_seconds)
            except Exception as error:
                if not _is_transport_failure(error):
                    raise
                if attempt == self._config.max_retries:
                    raise ToolServerUnavailable(request.server_name, request.name) from error
                await self._sleep(self._config.backoff_base_seconds * (2**attempt))
        raise AssertionError("unreachable")


def connections_from_env(
    environ: Mapping[str, str] | None = None,
    bearer: str | None = None,
) -> dict[str, Any]:
    """Build one connection per configured MCP server, optionally as one specific user."""
    env = os.environ if environ is None else environ
    connections: dict[str, Any] = {}
    for server in ("marketplace", "portfolio", "workspace"):
        prefix = f"MCP_{server.upper()}"
        url = env.get(f"{prefix}_URL")
        if url:
            token = bearer or env.get(f"{prefix}_BEARER_TOKEN", "")
            connection: StreamableHttpConnection = {
                "transport": "streamable_http",
                "url": url,
                "headers": {"Authorization": f"Bearer {token}"},
            }
            connections[server] = connection
    return connections


def github_connection(environ: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """The official GitHub MCP server, read-only. None until both settings exist.

    It is kept out of the agents' connections: only ingestion uses it, and a GitHub outage must
    not stop the agents from listing their own tools.
    """
    env = os.environ if environ is None else environ
    url = env.get("MCP_GITHUB_URL")
    token = env.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if not url or not token:
        return None
    connection: StreamableHttpConnection = {
        "transport": "streamable_http",
        "url": url,
        "headers": {"Authorization": f"Bearer {token}", "X-MCP-Readonly": "true"},
    }
    return {"github": connection}


def upwork_connection(
    access_token: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """The official Upwork MCP server, as one user. None until the URL and a token exist.

    Agents never connect to it directly. Only the live adapter behind the marketplace port does.
    """
    env = os.environ if environ is None else environ
    url = env.get("MCP_UPWORK_URL")
    if not url or not access_token:
        return None
    connection: StreamableHttpConnection = {
        "transport": "streamable_http",
        "url": url,
        "headers": {"Authorization": f"Bearer {access_token}"},
    }
    return {"upwork": connection}


class Toolbox:
    """Gives each agent only the tools named in its allowlist."""

    def __init__(
        self,
        connections: dict[str, Any],
        config: AppConfig | None = None,
        handle_tool_errors: bool = True,
    ) -> None:
        self._config = config or get_config()
        self._client = MultiServerMCPClient(
            connections,
            tool_interceptors=[ResilienceInterceptor(self._config.mcp_client)],
            handle_tool_errors=handle_tool_errors,
        )
        self._tools: list[BaseTool] | None = None

    async def _all_tools(self) -> list[BaseTool]:
        if self._tools is None:
            try:
                self._tools = await self._client.get_tools()
            except Exception as error:
                if _is_transport_failure(error):
                    raise ToolServerUnavailable("all", "list_tools") from error
                raise
        return self._tools

    async def tools_for(self, agent: str) -> list[BaseTool]:
        allowed = set(self._config.tools_for(agent))
        return [tool for tool in await self._all_tools() if tool.name in allowed]


class ToolCallError(Exception):
    """A tool ran and refused the request, so retrying the same call will not help."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ToolGateway:
    """How graph nodes call tools: enforces the allowlist and returns the JSON result."""

    def __init__(self, toolbox: Toolbox) -> None:
        self._toolbox = toolbox

    async def _allowed(self, agent: str, tool: str) -> BaseTool:
        match = next((t for t in await self._toolbox.tools_for(agent) if t.name == tool), None)
        if match is None:
            raise PermissionError(f"Agent '{agent}' may not use tool '{tool}'")
        return match

    async def call_text(self, agent: str, tool: str, arguments: dict[str, Any]) -> list[str]:
        """For third-party tools that return plain text blocks rather than one JSON object."""
        result = await (await self._allowed(agent, tool)).ainvoke(arguments)
        blocks = result if isinstance(result, list) else [result]
        return [block["text"] if isinstance(block, dict) else str(block) for block in blocks]

    async def call(self, agent: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        match = await self._allowed(agent, tool)
        try:
            result = await match.ainvoke(arguments)
        except ToolException as error:
            # The MCP SDK wraps the server's "code: message" text in its own prefix.
            text = re.sub(r"^Error executing tool \S+: ", "", str(error))
            code, _, message = text.partition(": ")
            if code == "rate_limited":
                raise ToolRateLimited("marketplace", tool) from error
            if code == "marketplace_unavailable":
                raise ToolServerUnavailable("marketplace", tool) from error
            raise ToolCallError(code, message or str(error)) from error
        block = result[0] if isinstance(result, list) else result
        text = block["text"] if isinstance(block, dict) else block
        parsed: dict[str, Any] = json.loads(text)
        return parsed
