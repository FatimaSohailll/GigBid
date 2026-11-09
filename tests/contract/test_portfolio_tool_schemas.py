"""Fails when a Portfolio tool signature changes unintentionally.

After an intended change, regenerate with: UPDATE_SNAPSHOTS=1 make test
"""

import json
import os
from pathlib import Path

from mcp.shared.memory import create_connected_server_and_client_session as connect

from mcp_servers.portfolio.server import build_server
from tests.support.rag_fakes import memory_components

SNAPSHOT = Path(__file__).parent / "snapshots" / "portfolio_tools.json"


async def test_portfolio_tool_schemas_match_snapshot(tmp_path: Path) -> None:
    server = build_server(memory_components(tmp_path), lambda _: "t")
    async with connect(server) as session:
        tools = (await session.list_tools()).tools
        prompts = (await session.list_prompts()).prompts
        templates = (await session.list_resource_templates()).resourceTemplates
    current = {
        "tools": {
            tool.name: {
                "description": tool.description,
                "annotations": tool.annotations.model_dump(exclude_none=True),
                "input": tool.inputSchema,
                "output": tool.outputSchema,
            }
            for tool in sorted(tools, key=lambda item: item.name)
        },
        "prompts": sorted(prompt.name for prompt in prompts),
        "resource_templates": sorted(template.uriTemplate for template in templates),
    }
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOT.write_bytes((json.dumps(current, indent=2, sort_keys=True) + "\n").encode())
    assert current == json.loads(SNAPSHOT.read_text(encoding="utf-8"))
