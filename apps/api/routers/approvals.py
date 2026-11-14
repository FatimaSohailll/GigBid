"""Human approvals. Always read from the checkpoint, so a refresh cannot lose one."""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from agent.nodes.approval import PLATFORM_STAGE, ApprovalDecision
from agent.nodes.client_liaison import ReplyDecision
from apps.api.schemas.api import ApprovalDetail, ApprovalIn, ApprovalSummary, QueuedRun
from apps.api.services import CurrentUser, Services, adopt_trace, read_thread

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


def _decision(kind: str, body: ApprovalIn) -> dict[str, Any]:
    """Validate here, so that a bad request is refused instead of failing inside the graph."""
    if kind == "bid":
        return ApprovalDecision(
            action=body.action,
            edited_cover_letter=body.edited_text,
            reason=body.reason,
        ).model_dump(mode="json")
    return ReplyDecision(
        action=body.action,
        edited_body=body.edited_text,
        reason=body.reason,
        selected_slot=body.selected_slot,
    ).model_dump(mode="json")


@router.get("", response_model=list[ApprovalSummary])
async def pending(user: CurrentUser, services: Services) -> list[dict[str, Any]]:
    return await services.repository.runs(user["id"], "awaiting_approval")


@router.get("/{thread_id}", response_model=ApprovalDetail)
async def detail(thread_id: str, user: CurrentUser, services: Services) -> ApprovalDetail:
    run = await services.repository.run(user["id"], thread_id)
    if run is None:
        raise HTTPException(404, "Approval not found.")
    _, payload = await read_thread(services, run["kind"], thread_id)
    if payload is None:
        raise HTTPException(409, "This thread is not waiting for approval.")
    return ApprovalDetail(
        thread_id=thread_id,
        kind=run["kind"],
        ref_id=run["ref_id"],
        started_at=run["started_at"],
        payload=payload,
    )


@router.post("/{thread_id}", response_model=QueuedRun, status_code=202)
async def decide(
    thread_id: str,
    body: ApprovalIn,
    user: CurrentUser,
    services: Services,
    request: Request,
) -> QueuedRun:
    run = await services.repository.run(user["id"], thread_id)
    if run is None:
        raise HTTPException(404, "Approval not found.")
    adopt_trace(request, run["trace_id"])
    try:
        decision = _decision(run["kind"], body)
    except ValidationError as error:
        raise HTTPException(422, error.errors()[0]["msg"]) from None
    if body.action == "edit":
        _, payload = await read_thread(services, run["kind"], thread_id)
        if payload and payload.get("stage") == PLATFORM_STAGE:
            raise HTTPException(
                422, "The marketplace has prepared this proposal; approve or reject it."
            )
    if not await services.repository.claim_approval(user["id"], thread_id):
        raise HTTPException(409, "This approval has already been decided.")
    await services.queue.enqueue_job(
        "run_thread",
        run["kind"],
        thread_id,
        user["id"],
        run["ref_id"],
        run["trace_id"],
        decision=decision,
        _job_id=f"resume:{thread_id}:{uuid.uuid4().hex}",
    )
    return QueuedRun(thread_id=thread_id, status="resuming")
