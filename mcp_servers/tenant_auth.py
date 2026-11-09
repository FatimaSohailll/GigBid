"""Bearer authentication for MCP servers whose tokens map directly to one tenant."""

import hmac
from collections.abc import Mapping
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError


def bearer_tenant(_: Context[Any, Any, Any]) -> str:
    token = get_access_token()
    if token is None:
        raise ToolError("unauthorized: a valid bearer token is required")
    return token.client_id


class TenantBearerVerifier(TokenVerifier):
    def __init__(self, tenants: Mapping[str, str]) -> None:
        self._tenants = tenants

    async def verify_token(self, token: str) -> AccessToken | None:
        for known, tenant in self._tenants.items():
            if hmac.compare_digest(token.encode(), known.encode()):
                return AccessToken(token=token, client_id=tenant, scopes=[])
        return None


def parse_tenants(raw: str) -> dict[str, str]:
    """Parse ``bearer:tenant`` entries separated by commas."""
    tenants: dict[str, str] = {}
    for entry in filter(None, (item.strip() for item in raw.split(","))):
        bearer, separator, tenant = entry.partition(":")
        if not separator or not bearer or not tenant or ":" in tenant:
            raise ValueError("User entries must be bearer:tenant")
        tenants[bearer] = tenant
    if not tenants:
        raise ValueError("At least one bearer:tenant user is required")
    return tenants
