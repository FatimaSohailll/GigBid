"""GigSim adapter: rate limiting, timeouts, bounded retries and error mapping over REST.

The same adapter serves the official GigSim and DevSim, chosen by GIGSIM_BASE_URL.
Writes are safe to retry because every attempt repeats the same Idempotency-Key.
"""

import asyncio
import random
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import httpx

from agent.config import MarketplaceConfig
from agent.marketplace_port import (
    ClientHistory,
    ConnectsBalance,
    Conversation,
    InsufficientConnects,
    JobPage,
    MarketEvent,
    MarketJob,
    MarketplaceAuthError,
    MarketplaceConflict,
    MarketplaceError,
    MarketplaceNotFound,
    MarketplaceRateLimited,
    MarketplaceRejected,
    MarketplaceUnavailable,
    Message,
    PriceStats,
    Proposal,
    ProposalDraftRequest,
    current_trace_id,
)

Sleep = Callable[[float], Awaitable[None]]
_WINDOW_SECONDS = 60.0


class PerUserRateLimiter:
    """Waits for a slot, so GigBid stays under the marketplace limit instead of hitting 429s."""

    def __init__(
        self,
        requests_per_minute: int,
        max_wait_seconds: float,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = requests_per_minute
        self._max_wait = max_wait_seconds
        self._sleep = sleep
        self._clock = clock
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, user: str) -> None:
        async with self._locks[user]:
            hits = self._hits[user]
            while True:
                now = self._clock()
                while hits and now - hits[0] >= _WINDOW_SECONDS:
                    hits.popleft()
                if len(hits) < self._limit:
                    hits.append(now)
                    return
                wait = _WINDOW_SECONDS - (now - hits[0])
                if wait > self._max_wait:
                    raise MarketplaceRateLimited(f"Rate limit reached; next slot in {wait:.0f}s.")
                await self._sleep(wait)


_ERRORS_BY_STATUS: dict[int, type[MarketplaceError]] = {
    400: MarketplaceRejected,
    401: MarketplaceAuthError,
    402: InsufficientConnects,
    403: MarketplaceAuthError,
    404: MarketplaceNotFound,
    409: MarketplaceConflict,
    422: MarketplaceRejected,
}


def _error_message(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return f"The marketplace returned HTTP {response.status_code}."


def _segment(value: str) -> str:
    return quote(value, safe="")


class GigSimAdapter:
    def __init__(
        self,
        base_url: str,
        api_token: str,
        user_id: str,
        config: MarketplaceConfig,
        limiter: PerUserRateLimiter,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._user_id = user_id
        self._config = config
        self._limiter = limiter
        self._sleep = sleep
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=config.request_timeout_seconds,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        delay = self._config.backoff_base_seconds * (2**attempt) * (1 + random.random() / 2)
        if response is not None:
            try:
                delay = max(delay, float(response.headers.get("Retry-After", 0)))
            except ValueError:
                pass
        return float(min(delay, self._config.max_retry_wait_seconds))

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        headers: dict[str, str] = {}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        trace_id = current_trace_id.get()
        if trace_id is not None:
            headers["X-Trace-Id"] = trace_id

        for attempt in range(self._config.max_retries + 1):
            await self._limiter.acquire(self._user_id)
            response: httpx.Response | None = None
            try:
                response = await self._client.request(
                    method,
                    path,
                    params=params,
                    json=body,
                    headers=headers,
                )
            except httpx.TransportError as error:
                failure: MarketplaceError = MarketplaceUnavailable(
                    f"The marketplace could not be reached ({type(error).__name__})."
                )
            else:
                if response.status_code < 400:
                    return response.json()
                message = _error_message(response)
                if response.status_code == 429:
                    failure = MarketplaceRateLimited(message)
                elif response.status_code >= 500:
                    failure = MarketplaceUnavailable(message)
                else:
                    raise _ERRORS_BY_STATUS.get(response.status_code, MarketplaceError)(message)
            if attempt < self._config.max_retries:
                await self._sleep(self._retry_delay(attempt, response))
        raise failure

    async def search_jobs(
        self,
        query: str | None,
        skills: list[str],
        limit: int,
        cursor: str | None,
    ) -> JobPage:
        params: dict[str, Any] = {"limit": limit, "skills": skills}
        if query:
            params["query"] = query
        if cursor:
            params["cursor"] = cursor
        return JobPage.model_validate(await self._request("GET", "/v1/jobs", params=params))

    async def get_job(self, job_id: str) -> MarketJob:
        data = await self._request("GET", f"/v1/jobs/{_segment(job_id)}")
        return MarketJob.model_validate(data)

    async def get_client_history(self, client_id: str) -> ClientHistory:
        data = await self._request("GET", f"/v1/clients/{_segment(client_id)}/history")
        return ClientHistory.model_validate(data)

    async def get_connects_balance(self) -> ConnectsBalance:
        return ConnectsBalance.model_validate(await self._request("GET", "/v1/connects/balance"))

    async def get_price_stats(self, category: str, skills: list[str]) -> PriceStats:
        params = {"category": category, "skills": skills}
        data = await self._request("GET", "/v1/market/price-stats", params=params)
        return PriceStats.model_validate(data)

    async def get_proposal(self, proposal_id: str) -> Proposal:
        data = await self._request("GET", f"/v1/proposals/{_segment(proposal_id)}")
        return Proposal.model_validate(data)

    async def list_conversations(self) -> list[Conversation]:
        data = await self._request("GET", "/v1/conversations")
        return [Conversation.model_validate(item) for item in data]

    async def list_messages(self, conversation_id: str) -> list[Message]:
        path = f"/v1/conversations/{_segment(conversation_id)}/messages"
        return [Message.model_validate(item) for item in await self._request("GET", path)]

    async def list_events(self, after: int, limit: int) -> list[MarketEvent]:
        data = await self._request("GET", "/v1/events", params={"after": after, "limit": limit})
        return [MarketEvent.model_validate(item) for item in data]

    async def create_proposal_draft(
        self,
        draft: ProposalDraftRequest,
        idempotency_key: str,
    ) -> Proposal:
        data = await self._request(
            "POST",
            "/v1/proposals/drafts",
            body=draft.model_dump(),
            idempotency_key=idempotency_key,
        )
        return Proposal.model_validate(data)

    async def submit_proposal(self, proposal_id: str, idempotency_key: str) -> Proposal:
        data = await self._request(
            "POST",
            f"/v1/proposals/{_segment(proposal_id)}/submit",
            idempotency_key=idempotency_key,
        )
        return Proposal.model_validate(data)

    async def send_message(
        self,
        conversation_id: str,
        body: str,
        idempotency_key: str,
    ) -> Message:
        data = await self._request(
            "POST",
            f"/v1/conversations/{_segment(conversation_id)}/messages",
            body={"body": body},
            idempotency_key=idempotency_key,
        )
        return Message.model_validate(data)
