"""Each user's Upwork access token, read from the encrypted vault and refreshed before it expires.

The Marketplace server is the only process that uses these tokens. Agents never see them.
"""

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from agent.marketplace_port import MarketplaceAuthError
from agent.upwork_oauth import (
    PROVIDER,
    OAuthSettings,
    UpworkOAuthError,
    discover,
    needs_refresh,
    refresh,
)


class Vault(Protocol):
    async def load(self, user_id: str, provider: str) -> dict[str, Any] | None: ...

    async def store(self, user_id: str, provider: str, tokens: dict[str, Any]) -> None: ...


class VaultTokens:
    def __init__(
        self,
        vault: Callable[[], Awaitable[Vault]],
        settings: OAuthSettings,
        http: Callable[[], httpx.AsyncClient] = lambda: httpx.AsyncClient(timeout=15),
    ) -> None:
        self._vault = vault
        self._settings = settings
        self._http = http
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def for_user(self, user_id: str) -> Callable[[], Awaitable[str]]:
        async def access_token() -> str:
            async with self._locks[user_id]:  # one refresh at a time per user
                vault = await self._vault()
                tokens = await vault.load(user_id, PROVIDER)
                if tokens is None:
                    raise MarketplaceAuthError("Connect your Upwork account in Settings first.")
                if needs_refresh(tokens):
                    try:
                        async with self._http() as http:
                            metadata = await discover(self._settings, http)
                            tokens = await refresh(self._settings, metadata, tokens, http)
                    except UpworkOAuthError as error:
                        raise MarketplaceAuthError(str(error)) from error
                    await vault.store(user_id, PROVIDER, tokens)
                return str(tokens["access_token"])

        return access_token
