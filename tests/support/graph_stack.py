"""Real DevSim and real MCP servers over HTTP, for running graphs end to end."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.store.memory import InMemoryStore
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from agent.config import AppConfig, get_config
from agent.mcp_client import Toolbox, ToolGateway, connections_from_env
from agent.memory.store import LongTermMemory
from mcp_servers.marketplace.server import (
    StaticBearerVerifier,
    UserAccess,
    gigsim_port_factory,
)
from mcp_servers.marketplace.server import build_server as build_marketplace
from mcp_servers.marketplace.store import ServerStore
from mcp_servers.marketplace.tests.harness import LiveServer, devsim_app, free_port
from mcp_servers.portfolio.server import TenantBearerVerifier
from mcp_servers.portfolio.server import build_server as build_portfolio
from mcp_servers.workspace.server import WorkspaceStore
from mcp_servers.workspace.server import build_server as build_workspace
from rag.ingest.pipeline import ingest_documents
from tests.support.rag_fakes import document, memory_components

USER = "ai_ml_engineer"
EVIDENCE = (
    "A telecom operator was losing prepaid subscribers. I built rolling usage aggregates and "
    "tuned a gradient boosted model with scikit-learn and Python. Model evaluation covered "
    "recall at fixed capacity. The retention team reached 31% more leavers at the same call "
    "volume. React performance profiling and SQL dashboards with data cleaning were also shown."
)


def fast_config() -> AppConfig:
    config = get_config()
    return config.model_copy(
        update={
            "mcp_client": config.mcp_client.model_copy(
                update={"backoff_base_seconds": 0.01, "max_retries": 1}
            ),
            "marketplace": config.marketplace.model_copy(
                update={"backoff_base_seconds": 0.01, "max_retries": 1}
            ),
            "graph": config.graph.model_copy(update={"node_backoff_seconds": 0.0}),
            "verification": config.verification.model_copy(update={"judge": "lexical"}),
        }
    )


def new_memory(config: AppConfig | None = None) -> LongTermMemory:
    return LongTermMemory(InMemoryStore(), (config or fast_config()).memory)


@dataclass
class GraphStack:
    simulator: LiveServer
    marketplace: LiveServer
    portfolio: LiveServer
    store: ServerStore
    env: dict[str, str]
    build_marketplace_app: Any
    workspace: LiveServer
    workspace_store: WorkspaceStore

    def gateway(self, config: AppConfig | None = None) -> ToolGateway:
        toolbox = Toolbox(
            connections_from_env(self.env),
            config or fast_config(),
            handle_tool_errors=False,
        )
        return ToolGateway(toolbox)

    def restart_marketplace(self) -> None:
        self.marketplace = LiveServer(self.build_marketplace_app(), self.marketplace.port)
        self.marketplace.start()

    def stop(self) -> None:
        self.workspace.stop()
        self.portfolio.stop()
        self.marketplace.stop()
        self.simulator.stop()


def start_stack(tmp_path: Path) -> GraphStack:
    simulator = LiveServer(devsim_app(), free_port())
    simulator.start()

    users = {"mcp-ai": UserAccess(USER, "sim-ai")}
    store = ServerStore(tmp_path / "marketplace.sqlite3")
    market_port = free_port()
    market_url = AnyHttpUrl(f"http://127.0.0.1:{market_port}")

    def build_market_app() -> Any:
        return build_marketplace(
            gigsim_port_factory(users, simulator.url, fast_config().marketplace),
            store,
            token_verifier=StaticBearerVerifier(users),
            auth=AuthSettings(
                issuer_url=market_url,
                resource_server_url=market_url,
                validate_token_resource=False,
            ),
            host="127.0.0.1",
            port=market_port,
        ).streamable_http_app()

    marketplace = LiveServer(build_market_app(), market_port)
    marketplace.start()

    components = memory_components(tmp_path)
    ingest_documents(
        [document("case:ai:churn", EVIDENCE, project="churn", skills=["Python"])],
        USER,
        components,
    )
    portfolio_port = free_port()
    portfolio_url = AnyHttpUrl(f"http://127.0.0.1:{portfolio_port}")
    portfolio = LiveServer(
        build_portfolio(
            components,
            token_verifier=TenantBearerVerifier({"pf-ai": USER}),
            auth=AuthSettings(
                issuer_url=portfolio_url,
                resource_server_url=portfolio_url,
                validate_token_resource=False,
            ),
            host="127.0.0.1",
            port=portfolio_port,
        ).streamable_http_app(),
        portfolio_port,
    )
    portfolio.start()

    workspace_store = WorkspaceStore(tmp_path / "workspace.sqlite3")
    workspace_port = free_port()
    workspace_url = AnyHttpUrl(f"http://127.0.0.1:{workspace_port}")
    workspace = LiveServer(
        build_workspace(
            workspace_store,
            token_verifier=TenantBearerVerifier({"ws-ai": USER}),
            auth=AuthSettings(
                issuer_url=workspace_url,
                resource_server_url=workspace_url,
                validate_token_resource=False,
            ),
            host="127.0.0.1",
            port=workspace_port,
        ).streamable_http_app(),
        workspace_port,
    )
    workspace.start()

    env = {
        "MCP_WORKSPACE_URL": f"http://127.0.0.1:{workspace_port}/mcp",
        "MCP_WORKSPACE_BEARER_TOKEN": "ws-ai",
        "MCP_MARKETPLACE_URL": f"http://127.0.0.1:{market_port}/mcp",
        "MCP_MARKETPLACE_BEARER_TOKEN": "mcp-ai",
        "MCP_PORTFOLIO_URL": f"http://127.0.0.1:{portfolio_port}/mcp",
        "MCP_PORTFOLIO_BEARER_TOKEN": "pf-ai",
    }
    return GraphStack(
        simulator, marketplace, portfolio, store, env, build_market_app, workspace, workspace_store
    )
