"""Fails when a tool signature changes unintentionally.

After an intended change, regenerate with: UPDATE_SNAPSHOTS=1 make test
"""

import json
import os
from pathlib import Path
from typing import Any

from mcp.shared.memory import create_connected_server_and_client_session as connect

from mcp_servers.marketplace.server import CallContext, build_server
from mcp_servers.marketplace.store import ServerStore

SNAPSHOT = Path(__file__).parent / "snapshots" / "marketplace_tools.json"


async def current_schema(tmp_path: Path) -> dict[str, Any]:
    def no_port(_: str) -> Any:
        raise AssertionError("schema listing must not touch the marketplace")

    store = ServerStore(tmp_path / "schema.sqlite3")
    try:
        server = build_server(no_port, store, lambda _: CallContext("u", "t"))
        async with connect(server) as session:
            tools = (await session.list_tools()).tools
    finally:
        store.close()
    return {
        tool.name: {
            "description": tool.description,
            "annotations": tool.annotations.model_dump(exclude_none=True),
            "input": tool.inputSchema,
            "output": tool.outputSchema,
        }
        for tool in sorted(tools, key=lambda item: item.name)
    }


async def test_marketplace_tool_schemas_match_snapshot(tmp_path: Path) -> None:
    current = await current_schema(tmp_path)
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_bytes((json.dumps(current, indent=2, sort_keys=True) + "\n").encode())
    assert current == json.loads(SNAPSHOT.read_text(encoding="utf-8"))
