"""Run history and live progress."""

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse

from apps.api.schemas.api import RunOut
from apps.api.services import CurrentUser, Services
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
