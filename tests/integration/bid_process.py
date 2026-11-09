"""A separate OS process running the bid graph, so that tests can kill it for real.

Usage: bid_process.py start|approve|continue <thread>, configured by environment variables.
With CRASH_AFTER_SUBMIT=1 the process dies right after the marketplace accepts the submission,
before LangGraph can record that the node finished.
"""

import asyncio
import os
import sys
from typing import Any

from langgraph.types import Command

from agent.checkpoint import postgres_checkpointer
from agent.graphs.bid_graph import build_bid_graph
from agent.mcp_client import Toolbox, ToolGateway, connections_from_env
from agent.runtime import AgentRuntime
from agent.state import UserSettings
from tests.support.graph_stack import USER, fast_config
from tests.support.scripted_llm import ScriptedLlm


class CrashingTools:
    def __init__(self, inner: ToolGateway) -> None:
        self._inner = inner

    async def call(self, agent: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._inner.call(agent, tool, arguments)
        if tool == "submit_proposal" and os.environ.get("CRASH_AFTER_SUBMIT"):
            os._exit(9)
        return result


async def main(command: str, thread_id: str) -> None:
    config = fast_config()
    tools = CrashingTools(
        ToolGateway(Toolbox(connections_from_env(), config, handle_tool_errors=False))
    )
    runtime = AgentRuntime(config, ScriptedLlm(), tools)
    thread = {"configurable": {"thread_id": thread_id}}
    async with postgres_checkpointer(os.environ["TEST_DATABASE_URL"]) as saver:
        graph = build_bid_graph(runtime, saver)
        if command == "start":
            state = {
                "job_id": "job-001",
                "user_id": USER,
                "trace_id": "trace-crash",
                "settings": UserSettings(floor_usd=200, weekly_connects_budget=50),
            }
            result = await graph.ainvoke(state, thread)
        elif command == "approve":
            result = await graph.ainvoke(Command(resume={"action": "approve"}), thread)
        else:
            result = await graph.ainvoke(None, thread)
    print(f"STATUS={result['status'].value}", flush=True)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(sys.argv[1], sys.argv[2]))
