"""Background work: scouting, graph runs, marketplace sync and portfolio indexing.

Every task is safe to run twice. A run looks at the checkpoint first, and writes use stable
idempotency keys, so a worker restart never double-analyses or double-submits.
"""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arq import Retry
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from redis.asyncio import Redis

from agent.graphs.bid_graph import build_bid_graph
from agent.graphs.conversation_graph import build_conversation_graph
from agent.mcp_client import ToolRateLimited, ToolServerUnavailable
from agent.nodes.scout import discover_jobs
from agent.runtime import AgentRuntime
from agent.state import ApprovalAction, ThreadStatus, UserSettings
from agent.tracing import run_config
from apps.api.db import Repository
from apps.api.services import DEGRADED_KEY, bid_thread, conversation_thread
from apps.api.streaming import publish_event
from rag.ingest.github import TextGateway, read_repository
from rag.ingest.loaders import UploadMetadata, load_upload
from rag.ingest.pipeline import ingest_documents, provision_tenant, remove_item
from rag.types import RagComponents

_WEEK_DAYS = 7
_POSITIVE_STATUSES = {"viewed", "replied", "interview", "hired", "lost"}


@dataclass
class WorkerServices:
    repository: Repository
    redis: Redis
    runtime_for: Callable[[dict[str, Any]], AgentRuntime]
    saver: BaseCheckpointSaver[Any]
    rag: Callable[[], RagComponents]
    github: Callable[[], TextGateway | None] = lambda: None

    def __post_init__(self) -> None:
        self._graphs: dict[tuple[str, str], CompiledStateGraph[Any, Any, Any, Any]] = {}
        self._provisioned: set[str] = set()
        # Portfolio stores are not safe for two threads at once, so writes take turns.
        self._rag_lock = asyncio.Lock()

    async def ensure_portfolio(self, user: dict[str, Any]) -> None:
        """Give this user a private copy of their persona's portfolio before it is needed."""
        async with self._rag_lock:
            if user["id"] not in self._provisioned:
                await asyncio.to_thread(provision_tenant, user["persona"], user["id"], self.rag())
                self._provisioned.add(user["id"])

    async def on_portfolio(self, function: Callable[..., Any], *arguments: Any) -> Any:
        """Run one blocking portfolio change off the event loop, one at a time."""
        async with self._rag_lock:
            return await asyncio.to_thread(function, *arguments)

    def graph(self, kind: str, user: dict[str, Any]) -> CompiledStateGraph[Any, Any, Any, Any]:
        key = (kind, user["id"])
        if key not in self._graphs:
            build = build_bid_graph if kind == "bid" else build_conversation_graph
            self._graphs[key] = build(self.runtime_for(user), self.saver)
        return self._graphs[key]


async def _record_outage(redis: Redis, error: ToolServerUnavailable) -> None:
    """Show the outage banner for a real outage, but not for a healthy, busy marketplace."""
    if not isinstance(error, ToolRateLimited):
        await redis.set(DEGRADED_KEY, error.server, ex=120)


def _services(ctx: dict[str, Any]) -> WorkerServices:
    services: WorkerServices = ctx["services"]
    return services


async def _initial_state(
    services: WorkerServices,
    kind: str,
    user: dict[str, Any],
    ref_id: str,
    trace_id: str,
) -> dict[str, Any]:
    if kind == "conversation":
        return {"conversation_id": ref_id, "user_id": user["id"], "trace_id": trace_id}
    row = await services.repository.job(user["id"], ref_id)
    assert row is not None
    recent = await services.repository.recent_jobs(user["id"])
    spent = await services.repository.connects_spent_since(user["id"], _WEEK_DAYS)
    return {
        "job_id": ref_id,
        "job": row["payload"],
        "recent_jobs": [job for job in recent if job["job_id"] != ref_id],
        "user_id": user["id"],
        "trace_id": trace_id,
        "settings": UserSettings(
            floor_usd=user["floor_usd"],
            weekly_connects_budget=user["weekly_connects_budget"],
            connects_spent_this_week=spent,
        ),
    }


async def _persist_bid(
    services: WorkerServices,
    user: dict[str, Any],
    job_id: str,
    values: dict[str, Any],
    run_status: str,
) -> None:
    repository = services.repository
    decision = values.get("decision")
    risk = values.get("risk_report")
    fit = values.get("fit_report")
    quote = values.get("price_quote")
    action = decision.action.value if decision else None
    auto_skipped = action == "skip" and user["autonomy"] == "auto_skip_low_fit"
    await repository.record_analysis(
        user["id"],
        job_id,
        "dismissed" if auto_skipped else "analyzed",
        fit.score if fit else None,
        risk.score if risk else None,
        action,
        quote.amount if quote else None,
        [flag.value for flag in risk.flags] if risk else [],
    )
    sent = {
        ThreadStatus.SUBMITTED.value: "submitted",
        ThreadStatus.AWAITING_PLATFORM_CONFIRMATION.value: "drafted",  # confirmed on Upwork
    }
    if run_status in sent:
        job = values["job"]
        await repository.upsert_card(
            user["id"],
            values["proposal_id"],
            job_id,
            job["category"],
            job["title"],
            sent[run_status],
            job["connects_cost"],
        )


async def run_thread(
    ctx: dict[str, Any],
    kind: str,
    thread_id: str,
    user_id: str,
    ref_id: str,
    trace_id: str,
    decision: dict[str, Any] | None = None,
    new_message: bool = False,
) -> None:
    """Run or resume one thread, streaming each node's progress."""
    services = _services(ctx)
    repository = services.repository
    user = await repository.user(user_id)
    await services.ensure_portfolio(user)
    graph = services.graph(kind, user)
    config: Any = {"configurable": {"thread_id": thread_id}}
    traced: Any = run_config(
        services.runtime_for(user).config, thread_id, trace_id, user_id, f"{kind}-graph"
    )

    snapshot = await graph.aget_state(config)
    pending = any(task.interrupts for task in snapshot.tasks)
    graph_input: Any
    if decision is not None:
        if pending:
            graph_input = Command(resume=decision)
        elif snapshot.next:
            graph_input = None  # a crash happened after the decision was applied
        else:
            return
    elif pending:
        return
    elif snapshot.next:
        graph_input = None
    elif snapshot.values and not new_message:
        return
    else:
        graph_input = await _initial_state(services, kind, user, ref_id, trace_id)

    await repository.set_run_status(thread_id, "running")
    await publish_event(services.redis, thread_id, {"type": "run_start", "thread_id": thread_id})
    try:
        async for update in graph.astream(graph_input, traced, stream_mode="updates"):
            for node, values in update.items():
                if node == "__interrupt__":
                    continue
                fields = sorted(values) if isinstance(values, dict) else []
                await publish_event(
                    services.redis,
                    thread_id,
                    {"type": "node", "node": node, "fields": fields, "at": datetime.now(UTC)},
                )
    except ToolServerUnavailable as error:
        await _record_outage(services.redis, error)
        await repository.set_run_status(thread_id, "waiting_for_service")
        await publish_event(
            services.redis,
            thread_id,
            {"type": "waiting_for_service", "server": error.server},
        )
        config_worker = services.runtime_for(user).config.worker
        raise Retry(defer=config_worker.service_retry_seconds) from error
    await services.redis.delete(DEGRADED_KEY)

    snapshot = await graph.aget_state(config)
    values = dict(snapshot.values)
    waiting = any(task.interrupts for task in snapshot.tasks)
    status = "awaiting_approval" if waiting else ThreadStatus(values["status"]).value
    approvals = values.get("approvals", [])
    rejection = (
        approvals[-1].reason
        if approvals and approvals[-1].action is ApprovalAction.REJECT
        else None
    )
    if kind == "bid":
        await _persist_bid(services, user, ref_id, values, status)
    await repository.finish_run(
        thread_id,
        status,
        values.get("tokens_used", 0),
        rejection,
        values.get("attention_reason"),
    )
    await publish_event(services.redis, thread_id, {"type": "run_end", "status": status})


async def scout_search(ctx: dict[str, Any], search_id: str, user_id: str) -> int:
    """Run one saved search, store new jobs and queue their analysis."""
    services = _services(ctx)
    repository = services.repository
    user = await repository.user(user_id)
    search = next(s for s in await repository.searches(user_id) if s["id"] == search_id)
    runtime = services.runtime_for(user)
    try:
        fresh = await discover_jobs(
            runtime,
            search["query"],
            search["skills"],
            await repository.recent_jobs(user_id),
        )
    except ToolServerUnavailable as error:
        await _record_outage(services.redis, error)
        raise Retry(defer=runtime.config.worker.service_retry_seconds) from error
    added = 0
    for job in fresh[: runtime.config.worker.max_jobs_per_scout]:
        if await repository.add_job(user_id, job):
            added += 1
            thread_id = bid_thread(user_id, job["job_id"])
            trace_id = uuid.uuid4().hex
            await repository.create_run(thread_id, user_id, "bid", job["job_id"], trace_id)
            await ctx["redis"].enqueue_job(
                "run_thread",
                "bid",
                thread_id,
                user_id,
                job["job_id"],
                trace_id,
                _job_id=f"run:{thread_id}",
            )
    await repository.mark_search_run(search_id)
    return added


async def scout_tick(ctx: dict[str, Any]) -> None:
    """Queue every saved search that is due. The id stops one search queueing twice."""
    for search in await _services(ctx).repository.due_searches():
        bucket = int(datetime.now(UTC).timestamp()) // 60
        await ctx["redis"].enqueue_job(
            "scout_search",
            search["id"],
            search["user_id"],
            _job_id=f"scout:{search['id']}:{bucket}",
        )


async def sync_user(ctx: dict[str, Any], user_id: str) -> int:
    """Move marketplace events into the pipeline and outcome memory, and queue client replies."""
    services = _services(ctx)
    repository = services.repository
    user = await repository.user(user_id)
    runtime = services.runtime_for(user)
    after = await repository.sync_cursor(user_id)
    try:
        page = await runtime.tools.call("sync", "list_events", {"after": after, "limit": 100})
    except ToolServerUnavailable as error:
        await _record_outage(services.redis, error)
        return 0
    for event in page["items"]:
        proposal_id = event["proposal_id"]
        if event["type"] == "proposal_status_changed":
            status = event["status"]
            await repository.set_card_status(user_id, proposal_id, status)
            if status in _POSITIVE_STATUSES:
                await runtime.memory.record_outcome(user_id, proposal_id, status)
        elif event["type"] == "message_received":
            conversation_id = event["conversation_id"]
            thread_id = conversation_thread(user_id, conversation_id)
            trace_id = uuid.uuid4().hex
            await repository.create_run(
                thread_id, user_id, "conversation", conversation_id, trace_id
            )
            await ctx["redis"].enqueue_job(
                "run_thread",
                "conversation",
                thread_id,
                user_id,
                conversation_id,
                trace_id,
                new_message=True,
                _job_id=f"message:{thread_id}:{event['event_id']}",
            )
        after = event["event_id"]
    await repository.set_sync_cursor(user_id, after)
    return len(page["items"])


async def sync_tick(ctx: dict[str, Any]) -> None:
    for user in await _services(ctx).repository.all_users():
        bucket = int(datetime.now(UTC).timestamp()) // 60
        await ctx["redis"].enqueue_job(
            "sync_user", user["id"], _job_id=f"sync:{user['id']}:{bucket}"
        )


async def ingest_upload(
    ctx: dict[str, Any],
    upload_id: str,
    user_id: str,
    path: str,
    title: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Index one uploaded file. Existing items are left alone (incremental re-indexing)."""
    services = _services(ctx)
    user = await services.repository.user(user_id)
    await services.ensure_portfolio(user)  # first, or the copy would be skipped as already done
    details = UploadMetadata.model_validate(metadata or {})
    document = load_upload(Path(path), upload_id, title, details)
    await services.on_portfolio(ingest_documents, [document], user_id, services.rag())
    await services.repository.set_upload_status(upload_id, "indexed")


async def ingest_github(
    ctx: dict[str, Any],
    upload_id: str,
    user_id: str,
    owner: str,
    repo: str,
) -> None:
    """Index a repository's README and code through the read-only GitHub MCP server."""
    services = _services(ctx)
    user = await services.repository.user(user_id)
    await services.ensure_portfolio(user)
    gateway = services.github()
    if gateway is None:
        await services.repository.set_upload_status(upload_id, "github_not_configured")
        return
    components = services.rag()
    documents = await read_repository(gateway, owner, repo, components.config)
    await services.on_portfolio(ingest_documents, documents, user_id, components)
    await services.repository.set_upload_status(upload_id, "indexed")


async def delete_evidence(ctx: dict[str, Any], user_id: str, item_id: str) -> None:
    """Remove one item from this user's portfolio only. Safe to run twice."""
    services = _services(ctx)
    user = await services.repository.user(user_id)
    await services.ensure_portfolio(user)
    await services.on_portfolio(remove_item, user_id, item_id, services.rag())
