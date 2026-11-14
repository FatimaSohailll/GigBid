"""Run history and live progress."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent.tracing import trace_url
from apps.api.schemas.api import (
    CheckpointOut,
    NodeUsageOut,
    QueuedRun,
    ReplayIn,
    RunOut,
    TraceLinkOut,
)
from apps.api.services import ApiServices, CurrentUser, Services, adopt_trace, read_thread
from apps.api.streaming import event_stream

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("", response_model=list[RunOut])
async def runs(user: CurrentUser, services: Services) -> list[dict[str, Any]]:
    return await services.repository.runs(user["id"])


@router.get("/{thread_id}", response_model=RunOut)
async def run(thread_id: str, user: CurrentUser, services: Services) -> dict[str, Any]:
    row = await services.repository.run(user["id"], thread_id)
    if row is None:
        raise HTTPException(404, "Run not found.")
    return row


@router.get("/{thread_id}/stream", response_class=StreamingResponse)
async def stream(
    thread_id: str,
    user: CurrentUser,
    services: Services,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    """Node-by-node progress over Server-Sent Events, resumable with Last-Event-ID."""
    if await services.repository.run(user["id"], thread_id) is None:
        raise HTTPException(404, "Run not found.")
    return StreamingResponse(
        event_stream(services.redis, thread_id, last_event_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Replaying from a step that sends would act again, so these are refused. Every other node can be
# replayed; a replay that reaches an approval waits for a human, as the first run did.
SENDING_NODES = frozenset({"submit", "send_on_platform", "send"})


async def _history(services: ApiServices, kind: str, thread_id: str) -> list[CheckpointOut]:
    config: Any = {"configurable": {"thread_id": thread_id}}
    history = []
    async for snapshot in services.graph(kind).aget_state_history(config):
        if not snapshot.next:
            continue  # the end of a run; nothing to replay
        nodes = list(snapshot.next)
        history.append(
            CheckpointOut(
                checkpoint_id=snapshot.config["configurable"]["checkpoint_id"],
                step=int((snapshot.metadata or {}).get("step", -1)),
                next_nodes=nodes,
                created_at=snapshot.created_at,
                replayable=not SENDING_NODES & set(nodes),
            )
        )
    return history


@router.get("/{thread_id}/checkpoints", response_model=list[CheckpointOut])
async def checkpoints(thread_id: str, user: CurrentUser, services: Services) -> list[CheckpointOut]:
    """The thread's history, newest first: each point the graph could be replayed from."""
    run = await services.repository.run(user["id"], thread_id)
    if run is None:
        raise HTTPException(404, "Run not found.")
    return await _history(services, run["kind"], thread_id)


@router.post("/{thread_id}/replay", response_model=QueuedRun, status_code=202)
async def replay(
    thread_id: str, body: ReplayIn, user: CurrentUser, services: Services, request: Request
) -> QueuedRun:
    run = await services.repository.run(user["id"], thread_id)
    if run is None:
        raise HTTPException(404, "Run not found.")
    adopt_trace(request, run["trace_id"])
    point = next(
        (
            item
            for item in await _history(services, run["kind"], thread_id)
            if item.checkpoint_id == body.checkpoint_id
        ),
        None,
    )
    if point is None:
        raise HTTPException(404, "That point is not in this run's history.")
    if not point.replayable:
        raise HTTPException(422, "Replaying from a sending step could send again.")
    await services.queue.enqueue_job(
        "run_thread",
        run["kind"],
        thread_id,
        user["id"],
        run["ref_id"],
        run["trace_id"],
        checkpoint_id=body.checkpoint_id,
        _job_id=f"replay:{thread_id}:{uuid.uuid4().hex}",
    )
    return QueuedRun(thread_id=thread_id, status="queued")


@router.get("/{thread_id}/usage", response_model=list[NodeUsageOut])
async def usage(thread_id: str, user: CurrentUser, services: Services) -> list[NodeUsageOut]:
    """Tokens, cost and latency per node, summed over every time the node ran in this thread."""
    run = await services.repository.run(user["id"], thread_id)
    if run is None:
        raise HTTPException(404, "Run not found.")
    values, _ = await read_thread(services, run["kind"], thread_id)
    totals: dict[str, NodeUsageOut] = {}
    for item in values.get("node_usage", []):
        row = totals.setdefault(
            item.node,
            NodeUsageOut(
                node=item.node,
                calls=0,
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                latency_seconds=0.0,
                models=[],
            ),
        )
        row.calls += item.calls
        row.input_tokens += item.input_tokens
        row.output_tokens += item.output_tokens
        row.latency_seconds = round(row.latency_seconds + item.latency_seconds, 3)
        row.cost_usd = (
            None
            if row.cost_usd is None or item.cost_usd is None
            else round(row.cost_usd + item.cost_usd, 6)
        )
        row.models = sorted(set(row.models) | set(item.models))
    return list(totals.values())


@router.get("/{thread_id}/trace", response_model=TraceLinkOut)
async def trace(thread_id: str, user: CurrentUser, services: Services) -> TraceLinkOut:
    """A link to this run's full trace in LangSmith, found by the GigBid trace id."""
    run = await services.repository.run(user["id"], thread_id)
    if run is None:
        raise HTTPException(404, "Run not found.")
    url, reason = await trace_url(services.config, run["trace_id"])
    return TraceLinkOut(url=url, reason=reason)
