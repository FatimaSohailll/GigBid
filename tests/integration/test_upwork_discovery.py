"""The official Upwork MCP server's public OAuth discovery. Uses no credentials.

Set MCP_UPWORK_URL to run it. It is off by default so CI does not depend on an outside service.
"""

import os
from urllib.parse import urlsplit

import httpx
import pytest

URL = os.environ.get("MCP_UPWORK_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="MCP_UPWORK_URL is not set")


def origin() -> str:
    parts = urlsplit(URL)
    return f"{parts.scheme}://{parts.netloc}"


def test_the_server_requires_a_token_and_points_to_its_metadata() -> None:
    response = httpx.post(URL, json={}, timeout=20)
    assert response.status_code == 401
    assert "resource_metadata" in response.headers["www-authenticate"]


def test_authorization_uses_code_flow_with_pkce() -> None:
    metadata = httpx.get(f"{origin()}/.well-known/oauth-authorization-server", timeout=20).json()
    assert metadata["response_types_supported"] == ["code"]
    assert "S256" in metadata["code_challenge_methods_supported"]
    assert "refresh_token" in metadata["grant_types_supported"]
