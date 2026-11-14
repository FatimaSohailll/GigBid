"""Fails when a Workspace tool signature changes unintentionally.

After an intended change, regenerate with: UPDATE_SNAPSHOTS=1 make test
"""

import json
import os
from pathlib import Path

from mcp.shared.memory import create_connected_server_and_client_session as connect

from mcp_servers.workspace.server import WorkspaceStore, build_server

SNAPSHOT = Path(__file__).parent / "snapshots" / "workspace_tools.json"


async def test_workspace_tool_schemas_match_snapshot(tmp_path: Path) -> None:
    server = build_server(WorkspaceStore(tmp_path / "schema.sqlite3"), lambda _: "t")
    async with connect(server) as session:
        tools = (await session.list_tools()).tools
    current = {
        tool.name: {
            "description": tool.description,
            "annotations": tool.annotations.model_dump(exclude_none=True),
            "input": tool.inputSchema,
            "output": tool.outputSchema,
        }
        for tool in sorted(tools, key=lambda item: item.name)
    }
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOT.write_bytes((json.dumps(current, indent=2, sort_keys=True) + "\n").encode())
    assert current == json.loads(SNAPSHOT.read_text(encoding="utf-8"))
