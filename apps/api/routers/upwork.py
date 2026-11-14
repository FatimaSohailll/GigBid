"""Connect a GigBid user to their own Upwork account (A4).

Sign-in happens on Upwork's page; GigBid only ever sees the authorization code and the tokens,
which go straight into the encrypted vault. Nothing here reads or writes marketplace data.
"""

import json
import os

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from agent.upwork_oauth import (
    PROVIDER,
    OAuthSettings,
    UpworkOAuthError,
    begin_sign_in,
    discover,
    exchange_code,
)
from apps.api.auth.security import TokenVault
from apps.api.services import ApiServices, CurrentUser, Services

router = APIRouter(tags=["upwork"])
_PENDING_SECONDS = 600


class UpworkStatusOut(BaseModel):
    configured: bool
    connected: bool


class UpworkStartOut(BaseModel):
    authorize_url: str


def _settings() -> OAuthSettings:
    settings = OAuthSettings.from_env(os.environ)
    if settings is None:
        raise HTTPException(503, "Upwork sign-in is not configured on this server.")
    return settings


def _vault(services: ApiServices) -> TokenVault:
    if services.vault is None:
        raise HTTPException(503, "Token encryption is not configured on this server.")
    return services.vault


def _pending_key(state: str) -> str:
    return f"gigbid:upwork:signin:{state}"


@router.get("/api/upwork/status", response_model=UpworkStatusOut)
async def status(user: CurrentUser, services: Services) -> UpworkStatusOut:
    vault = services.vault
    if OAuthSettings.from_env(os.environ) is None or vault is None:
        return UpworkStatusOut(configured=False, connected=False)
    return UpworkStatusOut(
        configured=True, connected=await vault.load(user["id"], PROVIDER) is not None
    )


@router.post("/api/upwork/connect", response_model=UpworkStartOut)
async def connect(user: CurrentUser, services: Services) -> UpworkStartOut:
    """Start sign-in. The browser goes to the returned address, which is Upwork's own page."""
    settings = _settings()
    _vault(services)
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            pending = begin_sign_in(settings, await discover(settings, http))
    except UpworkOAuthError as error:
        raise HTTPException(502, str(error)) from None
    await services.redis.set(
        _pending_key(pending.state),
        json.dumps({"user_id": user["id"], "verifier": pending.code_verifier}),
        ex=_PENDING_SECONDS,
    )
    return UpworkStartOut(authorize_url=pending.authorize_url)


@router.get("/auth/upwork/callback", include_in_schema=False)
async def callback(
    services: Services,
    state: str = "",
    code: str = "",
    error: str = "",
) -> RedirectResponse:
    """Upwork sends the browser here. The state proves the sign-in started in GigBid."""
    back = os.environ.get("WEB_APP_URL", "http://localhost:3000").rstrip("/") + "/settings"
    raw = await services.redis.getdel(_pending_key(state)) if state else None
    if raw is None:
        return RedirectResponse(f"{back}?upwork=expired", status_code=303)
    if error or not code:
        return RedirectResponse(f"{back}?upwork=declined", status_code=303)
    pending = json.loads(raw)
    settings = _settings()
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            metadata = await discover(settings, http)
            tokens = await exchange_code(settings, metadata, code, pending["verifier"], http)
    except UpworkOAuthError:
        return RedirectResponse(f"{back}?upwork=failed", status_code=303)
    await _vault(services).store(pending["user_id"], PROVIDER, tokens)
    return RedirectResponse(f"{back}?upwork=connected", status_code=303)


@router.delete("/api/upwork/connect", status_code=204)
async def disconnect(user: CurrentUser, services: Services) -> None:
    """Forget the tokens. Revoking access on Upwork itself is done in Upwork's settings."""
    await services.repository.delete_oauth(user["id"], PROVIDER)
