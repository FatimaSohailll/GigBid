from collections.abc import Callable

import httpx
import pytest

from agent.config import get_config
from agent.marketplace_port import (
    InsufficientConnects,
    MarketplaceAuthError,
    MarketplaceConflict,
    MarketplaceError,
    MarketplaceNotFound,
    MarketplaceRateLimited,
    MarketplaceRejected,
    MarketplaceUnavailable,
    ProposalDraftRequest,
    current_trace_id,
)
from devsim.config import FaultConfig
from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter, PerUserRateLimiter
from mcp_servers.marketplace.tests.harness import adapter, devsim_app

CONFIG = get_config().marketplace
Respond = Callable[[httpx.Request], httpx.Response]


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def mocked(respond: Respond, sleeps: Sleeps | None = None) -> GigSimAdapter:
    return GigSimAdapter(
        "http://sim",
        "tok",
        "user",
        CONFIG,
        PerUserRateLimiter(100_000, 30),
        transport=httpx.MockTransport(respond),
        sleep=sleeps or Sleeps(),
    )


BALANCE = {"freelancer_id": "u", "balance": 5, "spent": 1}


def sequence(*responses: httpx.Response | Exception) -> tuple[Respond, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    remaining = list(responses)

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = remaining.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return respond, seen


async def test_retries_server_errors_then_succeeds() -> None:
    respond, seen = sequence(httpx.Response(503), httpx.Response(200, json=BALANCE))
    assert (await mocked(respond).get_connects_balance()).balance == 5
    assert len(seen) == 2


async def test_gives_up_after_bounded_retries() -> None:
    respond, seen = sequence(*[httpx.Response(500) for _ in range(CONFIG.max_retries + 1)])
    with pytest.raises(MarketplaceUnavailable):
        await mocked(respond).get_connects_balance()
    assert len(seen) == CONFIG.max_retries + 1


async def test_connection_failure_becomes_unavailable() -> None:
    respond, _ = sequence(*[httpx.ConnectError("down") for _ in range(CONFIG.max_retries + 1)])
    with pytest.raises(MarketplaceUnavailable):
        await mocked(respond).get_connects_balance()


async def test_honours_retry_after_up_to_the_cap() -> None:
    sleeps = Sleeps()
    respond, _ = sequence(
        httpx.Response(429, headers={"Retry-After": "4"}),
        httpx.Response(429, headers={"Retry-After": "9999"}),
        httpx.Response(200, json=BALANCE),
    )
    await mocked(respond, sleeps).get_connects_balance()
    assert sleeps.calls[0] >= 4
    assert sleeps.calls[1] == CONFIG.max_retry_wait_seconds


async def test_exhausted_rate_limit_is_reported() -> None:
    respond, _ = sequence(*[httpx.Response(429) for _ in range(CONFIG.max_retries + 1)])
    with pytest.raises(MarketplaceRateLimited):
        await mocked(respond).get_connects_balance()


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (400, MarketplaceRejected),
        (401, MarketplaceAuthError),
        (402, InsufficientConnects),
        (404, MarketplaceNotFound),
        (409, MarketplaceConflict),
        (422, MarketplaceRejected),
        (418, MarketplaceError),
    ],
)
async def test_client_errors_map_to_types_and_are_not_retried(
    status: int,
    error: type[MarketplaceError],
) -> None:
    body = {"error": {"code": "x", "message": "the detail"}}
    respond, seen = sequence(httpx.Response(status, json=body))
    with pytest.raises(error, match="the detail"):
        await mocked(respond).get_connects_balance()
    assert len(seen) == 1


async def test_write_retries_repeat_the_same_key_and_send_trace() -> None:
    proposal = {
        "proposal_id": "p",
        "job_id": "j",
        "freelancer_id": "u",
        "cover_letter": "c",
        "bid_amount": 1,
        "status": "submitted",
        "created_at": "2026-11-01T00:00:00Z",
    }
    respond, seen = sequence(httpx.Response(503), httpx.Response(200, json=proposal))
    token = current_trace_id.set("trace-1")
    try:
        await mocked(respond).submit_proposal("p", "key-12345")
    finally:
        current_trace_id.reset(token)
    assert [r.headers["Idempotency-Key"] for r in seen] == ["key-12345", "key-12345"]
    assert seen[0].headers["X-Trace-Id"] == "trace-1"


async def test_path_segments_are_escaped() -> None:
    respond, seen = sequence(httpx.Response(404, json={}))
    with pytest.raises(MarketplaceNotFound):
        await mocked(respond).get_job("../admin/reset")
    assert seen[0].url.raw_path == b"/v1/jobs/..%2Fadmin%2Freset"


async def test_limiter_waits_for_a_slot() -> None:
    now = [0.0]
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = PerUserRateLimiter(2, 120, sleep=sleep, clock=lambda: now[0])
    for _ in range(3):
        await limiter.acquire("a")
    await limiter.acquire("b")
    assert sleeps == [60.0]


async def test_limiter_refuses_a_wait_beyond_the_maximum() -> None:
    limiter = PerUserRateLimiter(1, 5, clock=lambda: 0.0)
    await limiter.acquire("a")
    with pytest.raises(MarketplaceRateLimited):
        await limiter.acquire("a")


async def test_against_devsim_including_post_commit_failure() -> None:
    app = devsim_app(FaultConfig(post_commit_error_rate=0))
    port = adapter(app)
    page = await port.search_jobs("churn", ["Python"], 5, None)
    assert {job.job_id for job in page.items} == {"job-001", "job-002"}
    draft = await port.create_proposal_draft(
        ProposalDraftRequest(
            job_id="job-001",
            cover_letter="c",
            screening_answers=["a"],
            bid_amount=900,
        ),
        "draft-key-1",
    )
    submitted = await port.submit_proposal(draft.proposal_id, "submit-key-1")
    assert submitted.connects_spent == 8
    assert (await port.get_connects_balance()).spent == 8
    assert [event.type for event in await port.list_events(0, 10)] == [
        "proposal_drafted",
        "proposal_submitted",
    ]
