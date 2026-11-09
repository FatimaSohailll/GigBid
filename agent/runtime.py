"""Everything a node needs, passed in so that graphs can run against any backend."""

from dataclasses import dataclass
from typing import Any, Protocol

from langgraph.store.base import BaseStore

from agent.config import AppConfig, get_config
from agent.llm import LlmGateway, ModelRouter
from agent.mcp_client import Toolbox, ToolGateway, connections_from_env
from agent.memory.store import LongTermMemory
from agent.prompt_loader import load_prompt


class Tools(Protocol):
    async def call(self, agent: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class AgentRuntime:
    config: AppConfig
    llm: LlmGateway
    tools: Tools
    memory: LongTermMemory

    def prompt(self, node: str) -> str:
        return load_prompt(node, self.config.prompt_versions.get(node))


def default_runtime(store: BaseStore, config: AppConfig | None = None) -> AgentRuntime:
    """The production wiring: configured models and MCP servers from the environment."""
    app = config or get_config()
    toolbox = Toolbox(connections_from_env(), app, handle_tool_errors=False)
    memory = LongTermMemory(store, app.memory)
    return AgentRuntime(app, ModelRouter(app), ToolGateway(toolbox), memory)
