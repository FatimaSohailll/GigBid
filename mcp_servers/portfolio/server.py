"""Portfolio MCP server: read-only access to a freelancer's verified evidence.

Runs over Streamable HTTP with bearer auth (for GigBid) or stdio (for a desktop MCP host):
PORTFOLIO_TRANSPORT=stdio PORTFOLIO_TENANT=<id> python -m mcp_servers.portfolio.server
"""

import os
import threading
from collections.abc import Mapping
from typing import Any

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_servers.portfolio.prompts import register_prompts
from mcp_servers.portfolio.resources import register_resources
from mcp_servers.portfolio.tools import PortfolioBackend, TenantResolver, register_tools
from mcp_servers.tenant_auth import TenantBearerVerifier, bearer_tenant, parse_tenants
from rag.retriever import build_retriever, open_components
from rag.types import RagComponents


def build_server(
    components: RagComponents,
    tenant_of: TenantResolver = bearer_tenant,
    variant: str | None = None,
    **fastmcp_options: Any,
) -> FastMCP:
    mcp = FastMCP(
        "gigbid-portfolio",
        instructions="Search a freelancer's portfolio. Report only what the evidence shows.",
        stateless_http=True,
        json_response=True,
        **fastmcp_options,
    )
    backend = PortfolioBackend(
        components=components,
        retriever=build_retriever(components, variant),
        lock=threading.Lock(),
    )
    register_tools(mcp, backend, tenant_of)
    register_resources(mcp, backend, tenant_of)
    register_prompts(mcp)

    @mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "gigbid-portfolio-mcp"})

    return mcp


def create_server(environ: Mapping[str, str] | None = None) -> tuple[FastMCP, str]:
    env = os.environ if environ is None else environ
    if env.get("PORTFOLIO_TRANSPORT") == "stdio":
        tenant = env["PORTFOLIO_TENANT"]
        return build_server(open_components(environ=env), lambda _: tenant), "stdio"

    tenants = parse_tenants(env.get("MCP_PORTFOLIO_USERS", ""))
    host = env.get("MCP_PORTFOLIO_HOST", "127.0.0.1")
    port = int(env.get("MCP_PORTFOLIO_PORT", "8091"))
    url = AnyHttpUrl(env.get("MCP_PORTFOLIO_PUBLIC_URL", f"http://{host}:{port}"))
    server = build_server(
        open_components(environ=env),
        token_verifier=TenantBearerVerifier(tenants),
        auth=AuthSettings(issuer_url=url, resource_server_url=url, validate_token_resource=False),
        host=host,
        port=port,
    )
    return server, "streamable-http"


if __name__ == "__main__":
    application, transport = create_server()
    application.run(transport=transport)  # type: ignore[arg-type]
