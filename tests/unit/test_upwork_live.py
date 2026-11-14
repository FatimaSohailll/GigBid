"""A4: Upwork sign-in, the live adapter against a fake Upwork, and the second approval.

The fake returns the response shapes Upwork's MCP server returned live on 2026-11-14, with
made-up values. Nothing here touches the real Upwork.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect
from mcp.types import TextContent

from agent.config import get_config
from agent.graphs import bid_graph
from agent.handoffs import ProposalDraft
from agent.marketplace_port import (
    MarketplaceAuthError,
    MarketplaceConflict,
    MarketplaceNotFound,
    MarketplaceRateLimited,
    MarketplaceRejected,
    ProposalDraftRequest,
)
from agent.nodes import approval
from agent.runtime import AgentRuntime
from agent.state import ApprovalAction, ApprovalRecord, ThreadStatus
from agent.upwork_oauth import (
    OAuthSettings,
    ServerMetadata,
    UpworkOAuthError,
    begin_sign_in,
    code_challenge,
    discover,
    exchange_code,
    needs_refresh,
    refresh,
)
from mcp_servers.marketplace.adapters.gigsim import PerUserRateLimiter
from mcp_servers.marketplace.adapters.upwork_live import (
    UpworkDraftStore,
    UpworkLiveAdapter,
    _error_for,
    parse_budget_text,
    parse_tool_text,
    upwork_port_factory,
)
from mcp_servers.marketplace.server import CallContext, build_server
from mcp_servers.marketplace.store import ServerStore
from mcp_servers.marketplace.upwork_tokens import VaultTokens

SETTINGS = OAuthSettings(
    mcp_url="https://mcp.example.test/mcp",
    client_id="https://gigbid.example.test/oauth/client-metadata.json",
    redirect_uri="http://localhost:8000/auth/upwork/callback",
)
METADATA = ServerMetadata("https://auth.example.test/authorize", "https://auth.example.test/token")

# ---- Sign-in -------------------------------------------------------------------------------


def test_pkce_challenge_matches_the_rfc_example() -> None:
    # RFC 7636, appendix B
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert code_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_the_sign_in_address_carries_pkce_state_and_resource() -> None:
    pending = begin_sign_in(SETTINGS, METADATA)
    query = {
        key: value[0] for key, value in parse_qs(urlsplit(pending.authorize_url).query).items()
    }
    assert pending.authorize_url.startswith(METADATA.authorization_endpoint)
    assert query["code_challenge"] == code_challenge(pending.code_verifier)
    assert query["code_challenge_method"] == "S256"
    assert query["state"] == pending.state
    assert query["resource"] == SETTINGS.mcp_url
    assert query["client_id"] == SETTINGS.client_id
    assert "client_secret" not in query


def mock_http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_discovery_follows_the_resource_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(
                200, json={"authorization_servers": ["https://auth.example.test"]}
            )
        assert (
            str(request.url) == "https://auth.example.test/.well-known/oauth-authorization-server"
        )
        return httpx.Response(
            200,
            json={
                "authorization_endpoint": METADATA.authorization_endpoint,
                "token_endpoint": METADATA.token_endpoint,
                "code_challenge_methods_supported": ["S256"],
            },
        )

    async with mock_http(handler) as http:
        assert await discover(SETTINGS, http) == METADATA


async def test_discovery_refuses_a_server_without_pkce() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "protected-resource" in request.url.path:
            return httpx.Response(
                200, json={"authorization_servers": ["https://auth.example.test"]}
            )
        return httpx.Response(200, json={"authorization_endpoint": "a", "token_endpoint": "t"})

    async with mock_http(handler) as http:
        with pytest.raises(UpworkOAuthError):
            await discover(SETTINGS, http)


async def test_the_code_is_exchanged_with_its_verifier_and_no_secret() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({key: value[0] for key, value in parse_qs(request.content.decode()).items()})
        return httpx.Response(
            200, json={"access_token": "a1", "refresh_token": "r1", "expires_in": 60}
        )

    async with mock_http(handler) as http:
        tokens = await exchange_code(SETTINGS, METADATA, "code-1", "verifier-1", http, now=1000)
    assert seen["grant_type"] == "authorization_code"
    assert seen["code_verifier"] == "verifier-1"
    assert seen["resource"] == SETTINGS.mcp_url
    assert "client_secret" not in seen
    assert tokens == {"access_token": "a1", "refresh_token": "r1", "expires_at": 1060}


async def test_a_refused_exchange_is_an_error() -> None:
    async with mock_http(lambda _: httpx.Response(400, json={"error": "invalid_grant"})) as http:
        with pytest.raises(UpworkOAuthError):
            await exchange_code(SETTINGS, METADATA, "c", "v", http)


async def test_refresh_keeps_the_refresh_token_when_not_rotated() -> None:
    async with mock_http(lambda _: httpx.Response(200, json={"access_token": "a2"})) as http:
        tokens = await refresh(SETTINGS, METADATA, {"refresh_token": "r1"}, http, now=0)
    assert tokens["access_token"] == "a2" and tokens["refresh_token"] == "r1"
    assert needs_refresh({"expires_at": 100}, now=0)
    assert not needs_refresh({"expires_at": 10_000}, now=0)


class FakeVault:
    def __init__(self, tokens: dict[str, Any] | None) -> None:
        self.tokens = tokens
        self.stored: list[dict[str, Any]] = []

    async def load(self, user_id: str, provider: str) -> dict[str, Any] | None:
        return self.tokens

    async def store(self, user_id: str, provider: str, tokens: dict[str, Any]) -> None:
        self.stored.append(tokens)
        self.tokens = tokens


async def test_tokens_are_refreshed_and_stored_back_before_they_expire() -> None:
    vault = FakeVault({"access_token": "old", "refresh_token": "r", "expires_at": 0})

    def handler(request: httpx.Request) -> httpx.Response:
        if "protected-resource" in request.url.path:
            return httpx.Response(
                200, json={"authorization_servers": ["https://auth.example.test"]}
            )
        if request.url.path.endswith("oauth-authorization-server"):
            return httpx.Response(
                200,
                json={
                    "authorization_endpoint": "a",
                    "token_endpoint": "https://auth.example.test/token",
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        return httpx.Response(200, json={"access_token": "new", "expires_in": 3600})

    async def get_vault() -> FakeVault:
        return vault

    tokens = VaultTokens(get_vault, SETTINGS, http=lambda: mock_http(handler))
    assert await tokens.for_user("u1")() == "new"
    assert vault.stored and vault.stored[0]["refresh_token"] == "r"


async def test_a_user_who_never_signed_in_gets_a_clear_auth_error() -> None:
    async def get_vault() -> FakeVault:
        return FakeVault(None)

    with pytest.raises(MarketplaceAuthError):
        await VaultTokens(get_vault, SETTINGS).for_user("u1")()


# ---- Parsing ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("60.00–120.00/hr", (60.0, 120.0)),
        ("$1,500", (1500.0, 1500.0)),
        ("up to 40/hr", (0.0, 40.0)),
        ("30+/hr", (30.0, 30.0)),
        (None, (0.0, 0.0)),
    ],
)
def test_upwork_budget_text(text: str | None, expected: tuple[float, float]) -> None:
    assert parse_budget_text(text) == expected


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("invalid_token: expired", MarketplaceAuthError),
        ("Rate limit exceeded", MarketplaceRateLimited),
        ("Job not found", MarketplaceNotFound),
        ("VJ-JA-10: a proposal already exists", MarketplaceConflict),
        ("charged_amount must be a number", MarketplaceRejected),
    ],
)
def test_upwork_errors_map_onto_port_errors(text: str, error: type[Exception]) -> None:
    assert isinstance(_error_for(text), error)


def test_tool_text_must_be_a_json_object() -> None:
    assert parse_tool_text([TextContent(type="text", text='{"a": 1}')]) == {"a": 1}
    with pytest.raises(MarketplaceRejected):
        parse_tool_text([TextContent(type="text", text="not json")])


# ---- The adapter against a fake Upwork -----------------------------------------------------


def job_detail(job_id: str, applied: bool = False) -> dict[str, Any]:
    return {
        "status": "ok",
        "connects_cost": 16,
        "connects_balance": 80,
        "can_apply": not applied,
        "applied": applied,
        "client_record": {"contracts_total": 12, "feedback_score": 4.8},
        "data": {
            "marketplaceJobPosting": {
                "id": job_id,
                "content": {"title": "Churn model", "description": "Predict churn in Python."},
                "classification": {"category": {"preferredLabel": "Data Science & Analytics"}},
                "clientCompanyPublic": {"id": "c-9", "country": {"name": "Canada"}},
                "contractTerms": {
                    "contractType": "HOURLY",
                    "hourlyContractTerms": {"hourlyBudgetMin": 40, "hourlyBudgetMax": 70},
                },
            }
        },
    }


class FakeUpwork:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, arguments))
        action = arguments.get("action")
        params = arguments.get("params", {})
        if tool == "list_accounts":
            return {"accounts": [{"name": "Me", "org_uid": "org-1", "role": "TALENT"}]}
        assert arguments["org_uid"] == "org-1"
        if (tool, action) == ("find_jobs", "search"):
            rows = [
                {
                    "id": "111",
                    "title": "Churn model",
                    "skills": ["Python", "scikit-learn"],
                    "duration": "1 to 3 months",
                    "published_date": "2026-11-13T10:00:00Z",
                    "client": {"total_spent": "$12,500.00", "verification_status": "VERIFIED"},
                },
                {"id": "222", "title": "Already applied", "applied": True},
            ]
            return {"jobs": rows, "pageInfo": {"hasNextPage": True, "endCursor": "next-1"}}
        if (tool, action) == ("find_jobs", "get"):
            return job_detail(params["id"])
        if (tool, action) == ("manage_proposals", "create"):
            return {"preview": {"preview_id": "pv-1", "connects_cost": 16, "connects_balance": 80}}
        if (tool, action) == ("confirm_preview", "confirm"):
            return {"status": "ok", "proposal_id": "upwork-555"}
        if (tool, action) == ("send_message", "send"):
            return {"story_id": "s-1"}
        raise AssertionError(f"unexpected {tool} {action}")


def adapter(tmp_path: Path, fake: FakeUpwork) -> UpworkLiveAdapter:
    drafts = UpworkDraftStore(tmp_path / "drafts.sqlite3")
    return UpworkLiveAdapter(fake, "u1", drafts, PerUserRateLimiter(1000, 0))


async def test_search_reads_full_jobs_and_never_offers_one_already_applied_to(
    tmp_path: Path,
) -> None:
    fake = FakeUpwork()
    page = await adapter(tmp_path, fake).search_jobs("churn", [], 50, None)
    assert [job.job_id for job in page.items] == ["111"]
    job = page.items[0]
    assert job.connects_cost == 16
    assert job.budget.min == 40 and job.budget.max == 70
    assert job.required_skills == ["Python", "scikit-learn"]
    assert job.duration_days == 60
    assert job.description == "Predict churn in Python."
    assert job.posted_at == datetime(2026, 11, 13, 10, tzinfo=UTC)
    assert page.next_cursor == "next-1"
    search = next(args for tool, args in fake.calls if tool == "find_jobs")
    assert search["params"]["limit"] == 10  # Upwork's own maximum


async def test_client_balance_and_missing_price_data(tmp_path: Path) -> None:
    port = adapter(tmp_path, FakeUpwork())
    await port.search_jobs("churn", [], 10, None)
    client = await port.get_client_history("c-9")
    assert client.completed_projects == 12 and client.average_rating == 4.8
    assert client.payment_verified and client.total_spent_usd == 12500
    assert (await port.get_connects_balance()).balance == 80
    with pytest.raises(MarketplaceNotFound):
        await port.get_price_stats("Data", [])


async def test_sending_takes_a_preview_and_then_a_separate_confirmation(tmp_path: Path) -> None:
    fake = FakeUpwork()
    port = adapter(tmp_path, fake)
    await port.get_job("111")
    draft = await port.create_proposal_draft(
        ProposalDraftRequest(job_id="111", cover_letter="Hello", bid_amount=55), "key-draft-1"
    )
    assert draft.status == "drafted" and draft.requires_platform_confirmation
    assert not any(tool == "manage_proposals" for tool, _ in fake.calls)  # a draft stays local

    prepared = await port.submit_proposal(draft.proposal_id, "key-submit-1")
    assert prepared.status == "awaiting_platform_confirmation"
    assert (prepared.connects_cost, prepared.connects_balance) == (16, 80)
    create = next(args for tool, args in fake.calls if tool == "manage_proposals")
    assert create["params"] == {
        "job_reference": "111",
        "cover_letter": "Hello",
        "charged_amount": 55,
    }
    assert not any(tool == "confirm_preview" for tool, _ in fake.calls)  # nothing sent yet

    again = await port.submit_proposal(draft.proposal_id, "key-submit-1")
    assert again.status == "awaiting_platform_confirmation"
    assert sum(tool == "manage_proposals" for tool, _ in fake.calls) == 1

    sent = await port.confirm_submission(draft.proposal_id, "key-confirm-1")
    assert sent.status == "submitted" and sent.connects_spent == 16
    confirm = next(args for tool, args in fake.calls if tool == "confirm_preview")
    assert confirm["params"] == {"type": "proposal", "preview_id": "pv-1"}
    assert (await port.get_connects_balance()).balance == 64


async def test_confirming_without_a_preview_is_refused(tmp_path: Path) -> None:
    port = adapter(tmp_path, FakeUpwork())
    draft = await port.create_proposal_draft(
        ProposalDraftRequest(job_id="111", cover_letter="Hello", bid_amount=55), "key-draft-2"
    )
    with pytest.raises(MarketplaceConflict):
        await port.confirm_submission(draft.proposal_id, "key-confirm-2")


async def test_another_users_proposal_is_not_found(tmp_path: Path) -> None:
    drafts = UpworkDraftStore(tmp_path / "d.sqlite3")
    limiter = PerUserRateLimiter(1000, 0)
    alice = UpworkLiveAdapter(FakeUpwork(), "alice", drafts, limiter)
    bob = UpworkLiveAdapter(FakeUpwork(), "bob", drafts, limiter)
    draft = await alice.create_proposal_draft(
        ProposalDraftRequest(job_id="111", cover_letter="Hi", bid_amount=5), "key-draft-3"
    )
    with pytest.raises(MarketplaceNotFound):
        await bob.get_proposal(draft.proposal_id)


async def test_messages_go_to_the_room(tmp_path: Path) -> None:
    fake = FakeUpwork()
    message = await adapter(tmp_path, fake).send_message("room-1", "Thanks!", "key-msg-1")
    assert message.message_id == "s-1"
    assert fake.calls[-1] == (
        "send_message",
        {
            "org_uid": "org-1",
            "params": {"room_id": "room-1", "message": "Thanks!"},
            "action": "send",
        },
    )


# ---- Through the Marketplace MCP server ----------------------------------------------------


async def test_confirm_proposal_previews_then_sends_once(tmp_path: Path) -> None:
    fake = FakeUpwork()
    port_for = upwork_port_factory(
        lambda _: fake, UpworkDraftStore(tmp_path / "d.sqlite3"), get_config().marketplace
    )
    store = ServerStore(tmp_path / "s.sqlite3")
    try:
        server = build_server(port_for, store, lambda _: CallContext("u1", "t"))
        async with connect(server) as session:
            await session.call_tool("get_job", {"job_id": "111"})
            created = await session.call_tool(
                "create_proposal_draft",
                {
                    "request": {
                        "job_id": "111",
                        "cover_letter": "Hi",
                        "bid_amount": 50,
                        "idempotency_key": "key-draft-9",
                    }
                },
            )
            proposal_id = created.structuredContent["proposal"]["proposal_id"]
            submitted = await session.call_tool(
                "submit_proposal",
                {"request": {"proposal_id": proposal_id, "idempotency_key": "key-submit-9"}},
            )
            assert submitted.structuredContent["proposal"]["requires_platform_confirmation"]
            confirm = {"proposal_id": proposal_id, "idempotency_key": "key-confirm-9"}
            preview = await session.call_tool(
                "confirm_proposal", {"request": {**confirm, "dry_run": True}}
            )
            assert preview.structuredContent["preview"]["connects_cost"] == 16
            assert preview.structuredContent["preview"]["can_execute"] is True
            assert not any(tool == "confirm_preview" for tool, _ in fake.calls)
            first = await session.call_tool("confirm_proposal", {"request": confirm})
            second = await session.call_tool("confirm_proposal", {"request": confirm})
    finally:
        store.close()
    assert first.structuredContent["proposal"]["status"] == "submitted"
    assert second.structuredContent["replayed"] is True
    assert sum(tool == "confirm_preview" for tool, _ in fake.calls) == 1


# ---- The second approval in the graph -------------------------------------------------------


def platform_state(action: ApprovalAction) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "user_id": "u",
        "job": {"job_id": "j"},
        "proposal_id": "upw-1",
        "status": ThreadStatus.AWAITING_PLATFORM_CONFIRMATION,
        "drafts": [ProposalDraft(job_id="j", cover_letter="Letter", bid_amount=100)],
        "approvals": [ApprovalRecord(action=action, user_id="u", decided_at=now)],
    }


def test_the_graph_waits_for_a_second_approval_before_sending() -> None:
    waiting = platform_state(ApprovalAction.APPROVE)
    assert bid_graph._after_submit(waiting) == "platform_approval"  # type: ignore[arg-type]
    assert bid_graph._after_submit({**waiting, "status": ThreadStatus.SUBMITTED}) == "learn"  # type: ignore[arg-type]
    assert bid_graph._after_platform_approval(waiting) == "send_on_platform"  # type: ignore[arg-type]
    rejected = platform_state(ApprovalAction.REJECT)
    assert bid_graph._after_platform_approval(rejected) == "learn"  # type: ignore[arg-type]


class ConfirmTools:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call(self, agent: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert (agent, tool) == ("submission", "confirm_proposal")
        self.calls.append(arguments["request"])
        return {"proposal": {"status": "submitted", "connects_spent": 16}}


async def test_sending_on_the_platform_needs_the_second_approval() -> None:
    tools = ConfirmTools()
    runtime = AgentRuntime(get_config(), None, tools, None)  # type: ignore[arg-type]
    with pytest.raises(PermissionError):
        await approval.send_on_platform(platform_state(ApprovalAction.REJECT), runtime)  # type: ignore[arg-type]
    assert tools.calls == []
    update = await approval.send_on_platform(platform_state(ApprovalAction.APPROVE), runtime)  # type: ignore[arg-type]
    assert update["status"] is ThreadStatus.SUBMITTED
    assert tools.calls == [{"proposal_id": "upw-1", "idempotency_key": "u.j.v1.confirm"}]


def test_the_confirm_tool_is_held_only_by_the_submission_step() -> None:
    config = get_config()
    holders = [
        agent for agent in config.agent_tools if "confirm_proposal" in config.tools_for(agent)
    ]
    assert holders == ["submission"]


def test_the_client_metadata_document_names_itself() -> None:
    document = json.loads(Path("docs/oauth/client-metadata.json").read_text(encoding="utf-8"))
    assert document["token_endpoint_auth_method"] == "none"
    assert document["client_id"].startswith("https://")
    assert document["client_id"].endswith("/oauth/client-metadata.json")
    assert "http://localhost:8000/auth/upwork/callback" in document["redirect_uris"]
