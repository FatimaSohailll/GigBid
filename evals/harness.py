"""The real stack for evaluation: DevSim with full seed data, MCP servers and the real index."""

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from agent.checkpoint import checkpoint_serde
from agent.config import AppConfig
from agent.graphs.bid_graph import build_bid_graph
from agent.llm import LlmGateway
from agent.mcp_client import Toolbox, ToolGateway, connections_from_env
from agent.memory.store import LongTermMemory
from agent.runtime import AgentRuntime
from devsim.app import create_app
from devsim.config import RateLimitConfig, Settings, SimConfig
from evals.job_runner import GraphJobRunner
from mcp_servers.marketplace.server import StaticBearerVerifier, UserAccess, gigsim_port_factory
from mcp_servers.marketplace.server import build_server as build_marketplace
from mcp_servers.marketplace.store import ServerStore
from mcp_servers.marketplace.tests.harness import LiveServer, free_port
from mcp_servers.portfolio.server import build_server as build_portfolio
from mcp_servers.tenant_auth import TenantBearerVerifier
from mcp_servers.workspace.server import WorkspaceStore
from mcp_servers.workspace.server import build_server as build_workspace
from rag.types import RagComponents

PERSONAS = ("ai_ml_engineer", "fullstack_developer", "data_analyst")
ADMIN_TOKEN = "eval-admin"
_CORE_SEED_START = datetime(2026, 11, 1, tzinfo=UTC)
_UNLIMITED_RPM = 100_000


def _token(persona: str) -> str:
    return f"eval-{persona}"


def _start_time(seed_dir: Path | None) -> datetime:
    if seed_dir is None:
        return _CORE_SEED_START
    jobs = json.loads((seed_dir / "jobs.json").read_text(encoding="utf-8"))
    latest = max(datetime.fromisoformat(job["posted_at"]) for job in jobs)
    return latest + timedelta(days=1)


def eval_config(base: AppConfig) -> AppConfig:
    """One process runs every persona, so the per-user marketplace limit must not apply."""
    return base.model_copy(
        update={
            "marketplace": base.marketplace.model_copy(
                update={"max_requests_per_minute": _UNLIMITED_RPM}
            )
        }
    )


def _serving(port: int) -> dict[str, Any]:
    url = AnyHttpUrl(f"http://127.0.0.1:{port}")
    return {
        "auth": AuthSettings(
            issuer_url=url, resource_server_url=url, validate_token_resource=False
        ),
        "host": "127.0.0.1",
        "port": port,
    }


@dataclass
class EvalStack:
    simulator: LiveServer
    servers: list[LiveServer]
    env: dict[str, str]
    config: AppConfig

    def runtime(self, persona: str, llm: LlmGateway) -> AgentRuntime:
        connections = connections_from_env(self.env, bearer=_token(persona))
        toolbox = Toolbox(connections, self.config, handle_tool_errors=False)
        memory = LongTermMemory(InMemoryStore(), self.config.memory)
        return AgentRuntime(self.config, llm, ToolGateway(toolbox), memory)

    def toolbox(self, persona: str) -> Toolbox:
        """For agents that hold LangChain tools directly, such as the single ReAct agent."""
        connections = connections_from_env(self.env, bearer=_token(persona))
        return Toolbox(connections, self.config, handle_tool_errors=True)

    def runner(self, persona: str, llm: LlmGateway, defences: bool = True) -> GraphJobRunner:
        runtime = self.runtime(persona, llm)
        graph = build_bid_graph(runtime, InMemorySaver(serde=checkpoint_serde()))
        return GraphJobRunner(graph, runtime, defences)

    def simulate_client(self, proposal_id: str, seed: int) -> dict[str, Any]:
        """DevSim only. The official GigSim runs its own client model when it is added."""
        response = httpx.post(
            f"{self.simulator.url}/admin/proposals/{proposal_id}/simulate-client",
            json={"seed": seed},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        response.raise_for_status()
        outcome: dict[str, Any] = response.json()
        return outcome

    def stop(self) -> None:
        for server in [*self.servers, self.simulator]:
            server.stop()


def start_eval_stack(
    workdir: Path,
    components: RagComponents,
    config: AppConfig,
    seed_dir: Path | None,
    variant: str | None = None,
    personas: tuple[str, ...] = PERSONAS,
) -> EvalStack:
    sim_tokens = {f"sim-{persona}": persona for persona in personas}
    simulator = LiveServer(
        create_app(
            Settings(
                config=SimConfig(
                    start_time=_start_time(seed_dir),
                    seed_dir=seed_dir,
                    rate_limit=RateLimitConfig(requests_per_minute=_UNLIMITED_RPM),
                ),
                api_tokens=sim_tokens,
                admin_token=ADMIN_TOKEN,
            )
        ),
        free_port(),
    )
    simulator.start()

    users = {_token(persona): UserAccess(persona, f"sim-{persona}") for persona in personas}
    tenants = {_token(persona): persona for persona in personas}
    market_port, portfolio_port, workspace_port = free_port(), free_port(), free_port()
    apps = {
        market_port: build_marketplace(
            gigsim_port_factory(users, simulator.url, config.marketplace),
            ServerStore(workdir / "marketplace.sqlite3"),
            token_verifier=StaticBearerVerifier(users),
            **_serving(market_port),
        ),
        portfolio_port: build_portfolio(
            components,
            variant=variant,
            token_verifier=TenantBearerVerifier(tenants),
            **_serving(portfolio_port),
        ),
        workspace_port: build_workspace(
            WorkspaceStore(workdir / "workspace.sqlite3"),
            token_verifier=TenantBearerVerifier(tenants),
            **_serving(workspace_port),
        ),
    }
    servers = [LiveServer(app.streamable_http_app(), port) for port, app in apps.items()]
    for server in servers:
        server.start()
    env = {
        "MCP_MARKETPLACE_URL": f"http://127.0.0.1:{market_port}/mcp",
        "MCP_PORTFOLIO_URL": f"http://127.0.0.1:{portfolio_port}/mcp",
        "MCP_WORKSPACE_URL": f"http://127.0.0.1:{workspace_port}/mcp",
    }
    return EvalStack(simulator, servers, env, config)


def load_gold(data: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((data / "dev_set").glob("*.jsonl")):
        rows += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return rows


def select_jobs(gold: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    """Take jobs in turn from each persona and job type, so a small sample stays balanced."""
    if limit is None:
        return gold
    groups: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(gold, key=lambda item: item["job_id"]):
        groups[(row["persona"], row["job_type"])].append(row)
    ordered = [group for _, group in sorted(groups.items())]
    chosen: list[dict[str, Any]] = []
    depth = 0
    while len(chosen) < min(limit, len(gold)):
        chosen += [group[depth] for group in ordered if depth < len(group)]
        depth += 1
    return chosen[:limit]


def read_finished_rows(path: Path) -> dict[str, dict[str, Any]]:
    """Rows from an interrupted run. Failed rows are left out, so they run again."""
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return {f"{row['persona']}:{row['job_id']}": row for row in rows if not row.get("error")}


def append_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def rewrite_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    path.write_bytes(text.encode("utf-8"))
