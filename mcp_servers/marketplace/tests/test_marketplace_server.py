"""Tool behaviour through an in-memory MCP client, against real DevSim."""

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session as connect

from agent.marketplace_port import MarketplacePort
from mcp_servers.marketplace.server import (
    CallContext,
    StaticBearerVerifier,
    UserAccess,
    build_server,
    parse_users,
)
from mcp_servers.marketplace.store import ServerStore, mask_pii
from mcp_servers.marketplace.tests.harness import adapter, devsim_app

DRAFT = {
    "job_id": "job-001",
    "cover_letter": "I built a churn baseline.",
    "screening_answers": ["Use precision-recall curves."],
    "bid_amount": 1200,
}


class Harness:
    def __init__(self, app: FastAPI, path: Path) -> None:
        self.app = app
        self.path = path
        self.store = ServerStore(path)
        self.user = "ai_ml_engineer"
        self.ports: dict[str, MarketplacePort] = {
            "ai_ml_engineer": adapter(app, "sim-ai"),
            "fullstack_developer": adapter(app, "sim-web"),
        }
        self.server = self.new_server(self.store)

    def new_server(self, store: ServerStore) -> Any:
        return build_server(
            lambda user: self.ports[user],
            store,
            lambda _: CallContext(user_id=self.user, trace_id="trace-test"),
        )


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    built = Harness(devsim_app(), tmp_path / "server.sqlite3")
    yield built
    built.store.close()


@pytest.fixture
async def session(harness: Harness) -> AsyncIterator[ClientSession]:
    # The MCP connection must open and close in one task, but fixtures span several.
    ready: asyncio.Future[ClientSession] = asyncio.get_running_loop().create_future()
    finished = asyncio.Event()

    async def hold_open() -> None:
        async with connect(harness.server) as client:
            ready.set_result(client)
            await finished.wait()

    holder = asyncio.create_task(hold_open())
    yield await ready
    finished.set()
    await holder


async def write(session: ClientSession, tool: str, **request: Any) -> Any:
    return await session.call_tool(tool, {"request": request})


async def make_draft(session: ClientSession, key: str = "draft-key-1", **overrides: Any) -> str:
    result = await write(
        session, "create_proposal_draft", idempotency_key=key, **{**DRAFT, **overrides}
    )
    assert not result.isError, result.content
    return str(result.structuredContent["proposal"]["proposal_id"])


async def reply_thread(harness: Harness, session: ClientSession) -> str:
    proposal_id = await make_draft(session)
    await write(session, "submit_proposal", proposal_id=proposal_id, idempotency_key="submit-key")
    transport = harness.ports["ai_ml_engineer"]
    response = await transport._client.post(  # type: ignore[attr-defined]
        f"/admin/proposals/{proposal_id}/client-message",
        json={"body": "Can you start Monday?"},
        headers={"Authorization": "Bearer sim-admin"},
    )
    return str(response.json()["conversation_id"])


READ_TOOLS = {
    "search_jobs",
    "get_job",
    "get_client_history",
    "get_connects_balance",
    "get_price_stats",
    "get_proposal",
    "list_conversations",
    "list_messages",
    "list_events",
}
WRITE_TOOLS = {"create_proposal_draft", "submit_proposal", "confirm_proposal", "send_message"}


async def test_annotations_are_correct(session: ClientSession) -> None:
    tools = {tool.name: tool for tool in (await session.list_tools()).tools}
    assert set(tools) == READ_TOOLS | WRITE_TOOLS
    for name in READ_TOOLS:
        hints = tools[name].annotations
        assert hints.readOnlyHint and not hints.destructiveHint and hints.idempotentHint
    for name in WRITE_TOOLS:
        hints = tools[name].annotations
        assert not hints.readOnlyHint and hints.idempotentHint
    assert not tools["create_proposal_draft"].annotations.destructiveHint
    assert tools["submit_proposal"].annotations.destructiveHint
    assert tools["confirm_proposal"].annotations.destructiveHint
    assert tools["send_message"].annotations.destructiveHint


async def test_every_tool_declares_an_output_schema(session: ClientSession) -> None:
    for tool in (await session.list_tools()).tools:
        assert tool.outputSchema, tool.name


async def test_read_tools_return_typed_data(session: ClientSession) -> None:
    jobs = await session.call_tool("search_jobs", {"query": "react"})
    assert {job["job_id"] for job in jobs.structuredContent["items"]} == {"job-006"}
    client = await session.call_tool("get_client_history", {"client_id": "client-001"})
    assert "hidden_preferences" not in client.structuredContent
    assert (await session.call_tool("list_conversations", {})).structuredContent == {"items": []}


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"bid_amount": -5}, "bid_amount"),
        ({"cover_letter": ""}, "cover_letter"),
        ({"cover_letter": "x" * 5001}, "cover_letter"),
        ({"idempotency_key": "short"}, "idempotency_key"),
        ({"idempotency_key": "has spaces in it"}, "idempotency_key"),
        ({"unexpected": 1}, "unexpected"),
    ],
)
async def test_input_constraints_are_enforced(
    session: ClientSession,
    change: dict[str, Any],
    fragment: str,
) -> None:
    arguments = {**DRAFT, "idempotency_key": "valid-key-1", **change}
    result = await session.call_tool("create_proposal_draft", {"request": arguments})
    assert result.isError
    assert fragment in result.content[0].text


async def test_write_without_key_is_rejected(session: ClientSession) -> None:
    result = await session.call_tool("create_proposal_draft", {"request": DRAFT})
    assert result.isError


async def test_draft_dry_run_previews_and_writes_nothing(session: ClientSession) -> None:
    result = await write(
        session, "create_proposal_draft", idempotency_key="dry-key-1", dry_run=True, **DRAFT
    )
    content = result.structuredContent
    assert content["dry_run"] is True and content["proposal"] is None
    assert content["preview"]["payload"]["cover_letter"] == DRAFT["cover_letter"]
    assert content["preview"]["connects_cost"] == 0
    events = await session.call_tool("list_events", {})
    assert events.structuredContent == {"items": []}


async def test_submit_dry_run_shows_exact_cost(session: ClientSession) -> None:
    proposal_id = await make_draft(session)
    result = await write(
        session,
        "submit_proposal",
        proposal_id=proposal_id,
        idempotency_key="same-key-1",
        dry_run=True,
    )
    preview = result.structuredContent["preview"]
    assert preview["connects_cost"] == 8
    assert (preview["connects_balance"], preview["connects_balance_after"]) == (80, 72)
    assert preview["can_execute"] is True
    assert preview["payload"]["bid_amount"] == 1200
    assert (await session.call_tool("get_connects_balance", {})).structuredContent["spent"] == 0


async def test_dry_run_key_can_then_be_used_for_the_real_write(session: ClientSession) -> None:
    proposal_id = await make_draft(session)
    arguments = {"proposal_id": proposal_id, "idempotency_key": "same-key-1"}
    await write(session, "submit_proposal", dry_run=True, **arguments)
    real = await write(session, "submit_proposal", **arguments)
    assert real.structuredContent["replayed"] is False
    assert real.structuredContent["proposal"]["status"] == "submitted"


async def test_submit_dry_run_reports_blockers(session: ClientSession) -> None:
    proposal_id = await make_draft(session, screening_answers=[])
    result = await write(
        session,
        "submit_proposal",
        proposal_id=proposal_id,
        idempotency_key="dry-key-2",
        dry_run=True,
    )
    preview = result.structuredContent["preview"]
    assert preview["can_execute"] is False
    assert "Screening answers" in preview["blockers"][0]


async def test_retried_submit_never_spends_twice(session: ClientSession) -> None:
    proposal_id = await make_draft(session)
    arguments = {"proposal_id": proposal_id, "idempotency_key": "submit-key-1"}
    first = await write(session, "submit_proposal", **arguments)
    second = await write(session, "submit_proposal", **arguments)
    assert first.structuredContent["replayed"] is False
    assert second.structuredContent["replayed"] is True
    assert second.structuredContent["proposal"] == first.structuredContent["proposal"]
    assert (await session.call_tool("get_connects_balance", {})).structuredContent["spent"] == 8


async def test_concurrent_identical_submits_spend_once(session: ClientSession) -> None:
    proposal_id = await make_draft(session)
    arguments = {"proposal_id": proposal_id, "idempotency_key": "submit-race-1"}
    results = await asyncio.gather(
        *(write(session, "submit_proposal", **arguments) for _ in range(8))
    )
    replayed = [result.structuredContent["replayed"] for result in results]
    assert replayed.count(False) == 1
    assert (await session.call_tool("get_connects_balance", {})).structuredContent["spent"] == 8


async def test_key_reused_with_different_arguments_is_rejected(session: ClientSession) -> None:
    first = await make_draft(session, "draft-key-1")
    second = await make_draft(session, "draft-key-2", job_id="job-003", screening_answers=[])
    await write(session, "submit_proposal", proposal_id=first, idempotency_key="shared-key")
    result = await write(
        session, "submit_proposal", proposal_id=second, idempotency_key="shared-key"
    )
    assert result.isError
    assert "idempotency_key_reused" in result.content[0].text


async def test_ledger_survives_a_server_restart(harness: Harness, session: ClientSession) -> None:
    proposal_id = await make_draft(session)
    arguments = {"proposal_id": proposal_id, "idempotency_key": "submit-key-1"}
    await write(session, "submit_proposal", **arguments)

    harness.store.close()
    reopened = ServerStore(harness.path)
    try:
        async with connect(harness.new_server(reopened)) as restarted:
            again = await write(restarted, "submit_proposal", **arguments)
            spent = (await restarted.call_tool("get_connects_balance", {})).structuredContent
        assert again.structuredContent["replayed"] is True
        assert spent["spent"] == 8
    finally:
        reopened.close()
        harness.store = ServerStore(harness.path)


async def test_failed_write_can_be_retried_with_the_same_key(
    harness: Harness,
    session: ClientSession,
) -> None:
    proposal_id = await make_draft(session, screening_answers=[])
    arguments = {"proposal_id": proposal_id, "idempotency_key": "submit-key-1"}
    failed = await write(session, "submit_proposal", **arguments)
    assert failed.isError
    assert "invalid_request" in failed.content[0].text
    retried = await write(session, "submit_proposal", **arguments)
    assert retried.isError
    assert "idempotency_key_reused" not in retried.content[0].text


async def test_send_message_is_idempotent_and_previewable(
    harness: Harness,
    session: ClientSession,
) -> None:
    conversation_id = await reply_thread(harness, session)
    arguments = {"conversation_id": conversation_id, "body": "Yes, Monday works."}
    preview = await write(
        session, "send_message", idempotency_key="msg-key-1", dry_run=True, **arguments
    )
    assert preview.structuredContent["preview"]["payload"]["body"] == arguments["body"]
    first = await write(session, "send_message", idempotency_key="msg-key-1", **arguments)
    second = await write(session, "send_message", idempotency_key="msg-key-1", **arguments)
    assert second.structuredContent["replayed"] is True
    assert second.structuredContent["message"] == first.structuredContent["message"]
    thread = await session.call_tool("list_messages", {"conversation_id": conversation_id})
    assert [item["sender"] for item in thread.structuredContent["items"]] == [
        "client",
        "freelancer",
    ]


async def test_users_cannot_reach_each_others_data(
    harness: Harness,
    session: ClientSession,
) -> None:
    proposal_id = await make_draft(session)
    harness.user = "fullstack_developer"
    result = await session.call_tool("get_proposal", {"proposal_id": proposal_id})
    assert result.isError and "not_found" in result.content[0].text
    denied = await write(
        session, "submit_proposal", proposal_id=proposal_id, idempotency_key="other-key"
    )
    assert denied.isError and "not_found" in denied.content[0].text


async def test_marketplace_errors_are_typed(session: ClientSession) -> None:
    result = await session.call_tool("get_job", {"job_id": "job-404"})
    assert result.isError and result.content[0].text.endswith("Job 'job-404' does not exist.")
    assert "not_found" in result.content[0].text


async def test_audit_log_records_user_tool_arguments_result_and_trace(
    harness: Harness,
    session: ClientSession,
) -> None:
    await session.call_tool("get_job", {"job_id": "job-001"})
    await session.call_tool("get_job", {"job_id": "job-404"})
    success, failure = harness.store.audit_rows()
    assert (success["user_id"], success["tool"], success["trace_id"]) == (
        "ai_ml_engineer",
        "get_job",
        "trace-test",
    )
    assert json.loads(success["arguments"]) == {"job_id": "job-001"}
    assert json.loads(success["result"])["job_id"] == "job-001"
    assert success["outcome"] == "ok"
    assert failure["outcome"] == "not_found"


async def test_audit_log_masks_personal_data(harness: Harness, session: ClientSession) -> None:
    letter = "Email me at someone@example.com or call +1 415 555 0100."
    await make_draft(session, cover_letter=letter)
    row = harness.store.audit_rows()[0]
    assert "someone@example.com" not in row["arguments"] + row["result"]
    assert "555 0100" not in row["arguments"] + row["result"]
    assert "[email]" in row["arguments"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a.b+c@mail.example.org", "[email]"),
        ("card 4111 1111 1111 1111", "card [payment]"),
        ("phone (415) 555-0100", "phone [phone]"),
        ("posted 2026-11-01", "posted 2026-11-01"),
        ("job-001 costs 8", "job-001 costs 8"),
    ],
)
def test_mask_pii(value: str, expected: str) -> None:
    assert mask_pii({"nested": [value]}) == {"nested": [expected]}


@pytest.mark.parametrize(
    "statement",
    ["update audit_log set tool='x'", "delete from audit_log"],
)
async def test_audit_log_is_append_only(harness: Harness, statement: str) -> None:
    harness.store.audit("u", "t", {}, "ok", {}, "trace")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        harness.store._db.execute(statement)


async def test_bearer_verifier_maps_token_to_user() -> None:
    verifier = StaticBearerVerifier({"tok": UserAccess("u1", "m1")})
    verified = await verifier.verify_token("tok")
    assert verified is not None and verified.client_id == "u1"
    assert await verifier.verify_token("wrong") is None


def test_parse_users() -> None:
    assert parse_users("b:u:m")["b"] == UserAccess("u", "m")


@pytest.mark.parametrize("raw", ["", "b:u", "b::m", "a:b:c:d"])
def test_parse_users_rejects_bad_entries(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_users(raw)
