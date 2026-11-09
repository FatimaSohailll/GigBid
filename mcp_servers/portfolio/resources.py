"""Resource templates, so that a host can load full portfolio items."""

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from mcp_servers.portfolio.tools import PortfolioBackend, TenantResolver


def register_resources(mcp: FastMCP, backend: PortfolioBackend, tenant_of: TenantResolver) -> None:
    @mcp.resource("portfolio://case/{id}", mime_type="text/markdown")
    async def case_resource(id: str, ctx: Context[Any, Any, Any]) -> str:
        """A full case study."""
        document = await backend.item(tenant_of(ctx), "case", id)
        return f"# {document.title}\n\n{document.text}"

    @mcp.resource("portfolio://proposal/{id}", mime_type="text/plain")
    async def proposal_resource(id: str, ctx: Context[Any, Any, Any]) -> str:
        """A past proposal with its outcome."""
        document = await backend.item(tenant_of(ctx), "proposal", id)
        return f"Outcome: {document.outcome}\nJob: {document.title}\n\n{document.text}"
