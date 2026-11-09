"""Job inbox, job detail and starting an analysis."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request

from apps.api.schemas.api import JobDetail, JobSummary, QueuedRun
from apps.api.services import (
    ApiServices,
    CurrentUser,
    Services,
    bid_thread,
    read_thread,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def summary(row: dict[str, Any], run: dict[str, Any] | None = None) -> JobSummary:
    payload = row["payload"]
    return JobSummary(
        job_id=row["job_id"],
        title=payload["title"],
        category=payload["category"],
        status=row["status"],
        fit=row["fit"],
        risk=row["risk"],
        action=row["action"],
        price=row["price"],
        connects_cost=payload["connects_cost"],
        risk_flags=row["risk_flags"],
        budget_min=payload["budget"]["min"],
        budget_max=payload["budget"]["max"],
        run_status=run["status"] if run else None,
        thread_id=run["thread_id"] if run else None,
    )


async def start_bid_run(
    services: ApiServices,
    user: dict[str, Any],
    job_id: str,
    trace_id: str,
) -> QueuedRun:
    thread_id = bid_thread(user["id"], job_id)
    await services.repository.create_run(thread_id, user["id"], "bid", job_id, trace_id)
    await services.queue.enqueue_job(
        "run_thread", "bid", thread_id, user["id"], job_id, trace_id, _job_id=f"run:{thread_id}"
    )
    return QueuedRun(thread_id=thread_id, status="queued")


@router.get("", response_model=list[JobSummary])
async def inbox(
    user: CurrentUser,
    services: Services,
    action: Annotated[str | None, Query(pattern="^(bid|watch|skip)$")] = None,
) -> list[JobSummary]:
    runs = {
        r["ref_id"]: r for r in await services.repository.runs(user["id"]) if r["kind"] == "bid"
    }
    rows = await services.repository.inbox(user["id"], action)
    return [summary(row, runs.get(row["job_id"])) for row in rows]


@router.post("/refresh", response_model=list[str], status_code=202)
async def refresh(user: CurrentUser, services: Services) -> list[str]:
    """Run the user's saved searches now, on the worker."""
    searches = await services.repository.searches(user["id"])
    for search in searches:
        await services.queue.enqueue_job("scout_search", search["id"], user["id"])
    return [search["id"] for search in searches]


@router.get("/{job_id}", response_model=JobDetail)
async def detail(job_id: str, user: CurrentUser, services: Services) -> JobDetail:
    row = await services.repository.job(user["id"], job_id)
    if row is None:
        raise HTTPException(404, "Job not found.")
    thread_id = bid_thread(user["id"], job_id)
    run = await services.repository.run(user["id"], thread_id)
    values: dict[str, Any] = {}
    if run:
        values, _ = await read_thread(services, "bid", thread_id)
    return JobDetail(
        job=summary(row, run),
        description=row["payload"]["description"],
        job_spec=values.get("job_spec"),
        risk_report=values.get("risk_report"),
        fit_report=values.get("fit_report"),
        evidence=values.get("evidence", []),
        decision=values.get("decision"),
        price_quote=values.get("price_quote"),
    )


@router.post("/{job_id}/analyze", response_model=QueuedRun, status_code=202)
async def analyze(
    job_id: str,
    request: Request,
    user: CurrentUser,
    services: Services,
) -> QueuedRun:
    if await services.repository.job(user["id"], job_id) is None:
        raise HTTPException(404, "Job not found.")
    trace_id: str = getattr(request.state, "trace_id", uuid.uuid4().hex)
    return await start_bid_run(services, user, job_id, trace_id)


@router.post("/{job_id}/skip", status_code=204)
async def skip(job_id: str, user: CurrentUser, services: Services) -> None:
    if await services.repository.job(user["id"], job_id) is None:
        raise HTTPException(404, "Job not found.")
    await services.repository.set_job_status(user["id"], job_id, "dismissed")
