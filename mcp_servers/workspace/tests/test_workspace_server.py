"""Workspace tools through an in-memory MCP client."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from mcp_servers.workspace.server import WorkspaceStore, build_server

MONDAY = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)


def server(tmp_path: Path, tenant: list[str]) -> Any:
    return build_server(
        WorkspaceStore(tmp_path / "w.sqlite3"),
        lambda _: tenant[0],
        clock=lambda: MONDAY,
    )


def hold(start: str, key: str) -> dict[str, Any]:
    return {
        "request": {
            "start": start,
            "duration_minutes": 30,
            "title": "Interview",
            "idempotency_key": key,
        }
    }


async def test_slots_holds_and_tenants(tmp_path: Path) -> None:
    tenant = ["alice"]
    async with connect(server(tmp_path, tenant)) as session:
        first = (await session.call_tool("free_slots", {})).structuredContent["items"]
        assert [slot["start"][:16] for slot in first] == [
            "2026-11-09T09:00",
            "2026-11-09T09:30",
            "2026-11-09T10:00",
        ]

        args = hold(first[0]["start"], "hold-key-1")
        created = await session.call_tool("create_interview_hold", args)
        assert created.structuredContent["status"] == "tentative"
        replay = await session.call_tool("create_interview_hold", args)
        assert replay.structuredContent["replayed"] is True

        after = (await session.call_tool("free_slots", {})).structuredContent["items"]
        assert after[0]["start"] != first[0]["start"]

        overlap = await session.call_tool(
            "create_interview_hold", hold(first[0]["start"], "hold-key-2")
        )
        assert overlap.isError and "conflict" in overlap.content[0].text

        tenant[0] = "bob"
        other = (await session.call_tool("free_slots", {})).structuredContent["items"]
        assert other[0]["start"] == first[0]["start"]


async def test_email_is_only_ever_a_draft(tmp_path: Path) -> None:
    async with connect(server(tmp_path, ["alice"])) as session:
        args = {"request": {"subject": "S", "body": "B", "idempotency_key": "email-key-1"}}
        first = await session.call_tool("create_email_draft", args)
        again = await session.call_tool("create_email_draft", args)
    assert first.structuredContent["status"] == "draft"
    assert again.structuredContent["replayed"] is True


@pytest.mark.parametrize("key", ["short", "has spaces in it"])
async def test_bad_keys_are_rejected(tmp_path: Path, key: str) -> None:
    async with connect(server(tmp_path, ["a"])) as session:
        result = await session.call_tool("create_interview_hold", hold("2026-11-09T09:00:00Z", key))
    assert result.isError
