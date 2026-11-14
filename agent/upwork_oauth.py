"""Signing in to Upwork's official MCP server: OAuth 2.1 authorization code with PKCE.

GigBid is a public client: it has no secret. Its client id is the HTTPS address of a client
metadata document (docs/oauth/client-metadata.json, published on GitHub Pages), which Upwork's
authorization server says it supports. The endpoints are read from the server's own metadata,
so nothing about Upwork's OAuth is hard-coded except the MCP server address in the environment.
"""

import base64
import hashlib
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

PROVIDER = "upwork"
_REFRESH_MARGIN_SECONDS = 120


class UpworkOAuthError(Exception):
    """Sign-in failed or the stored tokens can no longer be used. The user must sign in again."""


@dataclass(frozen=True)
class OAuthSettings:
    mcp_url: str
    client_id: str
    redirect_uri: str
    client_secret: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "OAuthSettings | None":
        """None until the server, client id and redirect address are all configured."""
        mcp_url = env.get("MCP_UPWORK_URL", "")
        client_id = env.get("UPWORK_OAUTH_CLIENT_ID", "")
        redirect_uri = env.get("UPWORK_OAUTH_REDIRECT_URI", "")
        if not (mcp_url and client_id and redirect_uri):
            return None
        return cls(mcp_url, client_id, redirect_uri, env.get("UPWORK_OAUTH_CLIENT_SECRET") or None)


@dataclass(frozen=True)
class ServerMetadata:
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str | None = None


@dataclass(frozen=True)
class PendingSignIn:
    """What the callback needs. Kept server side for a few minutes, keyed by state."""

    state: str
    code_verifier: str
    authorize_url: str


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


async def discover(settings: OAuthSettings, http: httpx.AsyncClient) -> ServerMetadata:
    """Follow the MCP server's protected-resource metadata to its authorization server."""
    try:
        resource = await http.get(
            f"{_origin(settings.mcp_url)}/.well-known/oauth-protected-resource"
            f"{urlsplit(settings.mcp_url).path}"
        )
        resource.raise_for_status()
        issuer = resource.json()["authorization_servers"][0]
        server = await http.get(f"{issuer.rstrip('/')}/.well-known/oauth-authorization-server")
        server.raise_for_status()
        metadata = server.json()
        if "S256" not in metadata.get("code_challenge_methods_supported", []):
            raise UpworkOAuthError("The authorization server does not support PKCE with S256.")
        return ServerMetadata(
            metadata["authorization_endpoint"],
            metadata["token_endpoint"],
            metadata.get("revocation_endpoint"),
        )
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as error:
        raise UpworkOAuthError(f"Could not read Upwork's sign-in settings: {error}") from error


def begin_sign_in(settings: OAuthSettings, metadata: ServerMetadata) -> PendingSignIn:
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.client_id,
            "redirect_uri": settings.redirect_uri,
            "state": state,
            "code_challenge": code_challenge(verifier),
            "code_challenge_method": "S256",
            "resource": settings.mcp_url,
        }
    )
    return PendingSignIn(state, verifier, f"{metadata.authorization_endpoint}?{query}")


def _with_expiry(tokens: dict[str, Any], now: float) -> dict[str, Any]:
    if "access_token" not in tokens:
        raise UpworkOAuthError("Upwork returned no access token.")
    stored: dict[str, Any] = {
        key: tokens[key] for key in ("access_token", "refresh_token", "scope") if key in tokens
    }
    stored["expires_at"] = now + float(tokens.get("expires_in", 3600))
    return stored


async def _token_request(
    settings: OAuthSettings,
    metadata: ServerMetadata,
    form: dict[str, str],
    http: httpx.AsyncClient,
) -> dict[str, Any]:
    form = {**form, "client_id": settings.client_id, "resource": settings.mcp_url}
    if settings.client_secret:
        form["client_secret"] = settings.client_secret
    response = await http.post(metadata.token_endpoint, data=form)
    if response.status_code >= 400:
        raise UpworkOAuthError(f"Upwork refused the token request ({response.status_code}).")
    tokens: dict[str, Any] = response.json()
    return tokens


async def exchange_code(
    settings: OAuthSettings,
    metadata: ServerMetadata,
    code: str,
    code_verifier: str,
    http: httpx.AsyncClient,
    now: float | None = None,
) -> dict[str, Any]:
    tokens = await _token_request(
        settings,
        metadata,
        {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": settings.redirect_uri,
        },
        http,
    )
    return _with_expiry(tokens, time.time() if now is None else now)


def needs_refresh(tokens: Mapping[str, Any], now: float | None = None) -> bool:
    current = time.time() if now is None else now
    return float(tokens.get("expires_at", 0)) - _REFRESH_MARGIN_SECONDS <= current


async def refresh(
    settings: OAuthSettings,
    metadata: ServerMetadata,
    tokens: Mapping[str, Any],
    http: httpx.AsyncClient,
    now: float | None = None,
) -> dict[str, Any]:
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise UpworkOAuthError("The Upwork session has expired. Sign in to Upwork again.")
    renewed = await _token_request(
        settings,
        metadata,
        {"grant_type": "refresh_token", "refresh_token": str(refresh_token)},
        http,
    )
    renewed.setdefault("refresh_token", refresh_token)  # some servers do not rotate it
    return _with_expiry(renewed, time.time() if now is None else now)
