"""Proposal studio and pipeline board."""

from typing import Any

from fastapi import APIRouter, HTTPException

from apps.api.schemas.api import PipelineCard, ProposalStudio
from apps.api.services import CurrentUser, Services, bid_thread, read_thread

router = APIRouter(prefix="/api", tags=["proposals"])


@router.get("/proposals/{job_id}", response_model=ProposalStudio)
async def studio(job_id: str, user: CurrentUser, services: Services) -> ProposalStudio:
    thread_id = bid_thread(user["id"], job_id)
    run = await services.repository.run(user["id"], thread_id)
    if run is None:
        raise HTTPException(404, "This job has not been analysed yet.")
    values, pending = await read_thread(services, "bid", thread_id)
    drafts = values.get("drafts", [])
    return ProposalStudio(
        thread_id=thread_id,
        status=run["status"],
        draft=drafts[-1] if drafts else None,
        critiques=values.get("critiques", []),
        compliance=values.get("compliance_verdicts", []),
        verification_reports=values.get("verification_reports", []),
        evidence=values.get("evidence", []),
        pending_approval=pending,
    )


@router.get("/pipeline", response_model=list[PipelineCard])
async def pipeline(user: CurrentUser, services: Services) -> list[dict[str, Any]]:
    return await services.repository.pipeline(user["id"])
