"""Bearer authentication for MCP servers whose tokens map directly to one tenant."""

import hashlib
import hmac
from collections.abc import Mapping
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

_TOKEN_PREFIX = "gb1"


def bearer_tenant(_: Context[Any, Any, Any]) -> str:
    token = get_access_token()
    if token is None:
        raise ToolError("unauthorized: a valid bearer token is required")
    return token.client_id


def _signature(signing_key: str, user_id: str, account: str) -> str:
    message = f"{user_id}.{account}".encode()
    return hmac.new(signing_key.encode(), message, hashlib.sha256).hexdigest()


def mint_user_token(signing_key: str, user_id: str, account: str) -> str:
    """A bearer for one GigBid user, naming the marketplace account that user acts as."""
    return f"{_TOKEN_PREFIX}.{user_id}.{account}.{_signature(signing_key, user_id, account)}"


def read_user_token(signing_key: str | None, token: str) -> tuple[str, str] | None:
    """Return (user_id, account) if the token was signed with this key."""
    parts = token.split(".")
    if signing_key is None or len(parts) != 4 or parts[0] != _TOKEN_PREFIX:
        return None
    _, user_id, account, signature = parts
    if not hmac.compare_digest(signature, _signature(signing_key, user_id, account)):
        return None
    return user_id, account


class TenantBearerVerifier(TokenVerifier):
    """Portfolio and Workspace data belongs to the individual GigBid user."""

    def __init__(self, tenants: Mapping[str, str], signing_key: str | None = None) -> None:
        self._tenants = tenants
        self._signing_key = signing_key

    async def verify_token(self, token: str) -> AccessToken | None:
        signed = read_user_token(self._signing_key, token)
        if signed is not None:
            return AccessToken(token=token, client_id=signed[0], scopes=[])
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
