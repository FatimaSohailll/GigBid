"""User settings, saved searches, the memory viewer, portfolio upload and service status."""

import uuid
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

from apps.api.schemas.api import (
    EvidenceOut,
    GithubIngestIn,
    MemoryItem,
    MemoryOut,
    SavedSearchIn,
    SavedSearchOut,
    ServiceStatus,
    SettingsIn,
    UploadOut,
    UserOut,
)
from apps.api.services import DEGRADED_KEY, ApiServices, CurrentUser, Services
from rag.ingest.loaders import PortfolioDocument, UploadMetadata

router = APIRouter(prefix="/api", tags=["settings"])
_ALLOWED_UPLOADS = {".pdf", ".md", ".txt"}
_MAX_UPLOAD_BYTES = 5_000_000
_UNAVAILABLE = "The marketplace is unavailable. Your work is queued and will resume automatically."


@router.put("/settings", response_model=UserOut)
async def update_settings(
    body: SettingsIn,
    user: CurrentUser,
    services: Services,
) -> dict[str, Any]:
    await services.repository.update_settings(
        user["id"], body.floor_usd, body.weekly_connects_budget, body.autonomy
    )
    return await services.repository.user(user["id"])


@router.get("/searches", response_model=list[SavedSearchOut])
async def searches(user: CurrentUser, services: Services) -> list[dict[str, Any]]:
    return await services.repository.searches(user["id"])


@router.post("/searches", response_model=SavedSearchOut, status_code=201)
async def add_search(
    body: SavedSearchIn,
    user: CurrentUser,
    services: Services,
) -> dict[str, Any]:
    minimum = services.config.worker.min_search_interval_minutes[user["marketplace"]]
    if body.interval_minutes < minimum:
        raise HTTPException(422, f"Searches can run at most every {minimum} minutes.")
    return await services.repository.add_search(
        user["id"], body.query, body.skills, body.interval_minutes
    )


@router.delete("/searches/{search_id}", status_code=204)
async def delete_search(search_id: str, user: CurrentUser, services: Services) -> None:
    await services.repository.delete_search(user["id"], search_id)


@router.get("/memory", response_model=MemoryOut)
async def memory(user: CurrentUser, services: Services) -> MemoryOut:
    style = await services.memory.style_items(user["id"])
    outcome = await services.memory.outcome_items(user["id"])
    return MemoryOut(
        style=[MemoryItem(key=i["pattern"], text=i["rule"], detail=i) for i in style],
        outcome=[
            MemoryItem(key=f"{i['category']}|{i['project']}", text=i["insight"], detail=i)
            for i in outcome
        ],
    )


@router.delete("/memory/style/{key}", status_code=204)
async def delete_style(key: str, user: CurrentUser, services: Services) -> None:
    await services.memory.delete_style_rule(user["id"], key)


@router.delete("/memory/outcome/{key}", status_code=204)
async def delete_outcome(key: str, user: CurrentUser, services: Services) -> None:
    await services.memory.delete_outcome_insight(user["id"], key)


@router.post("/portfolio/uploads", response_model=UploadOut, status_code=202)
async def upload(
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(min_length=1, max_length=200)],
    user: CurrentUser,
    services: Services,
    item_type: Annotated[str, Form()] = "case_study",
    project: Annotated[str | None, Form(max_length=128)] = None,
    skills: Annotated[str, Form(max_length=500)] = "",
    date: Annotated[str | None, Form(max_length=32)] = None,
    client_industry: Annotated[str | None, Form(max_length=64)] = None,
    outcome: Annotated[str | None, Form()] = None,
) -> UploadOut:
    """Save a portfolio file with its metadata and index it on the worker."""
    try:
        metadata = UploadMetadata.model_validate(
            {
                "item_type": item_type,
                "project": project,
                "skills": [skill.strip() for skill in skills.split(",") if skill.strip()],
                "date": date,
                "client_industry": client_industry,
                "outcome": outcome,
            }
        )
    except ValidationError as error:
        raise HTTPException(422, "Check the portfolio item details.") from error
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_UPLOADS:
        raise HTTPException(415, "Upload a PDF, Markdown or text file.")
    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, "This file is too large.")
    upload_id = uuid.uuid4().hex
    folder = services.upload_dir / user["id"]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{upload_id}{suffix}"
    path.write_bytes(content)
    filename = Path(file.filename or "upload").name
    await services.repository.add_upload(user["id"], upload_id, filename)
    await services.queue.enqueue_job(
        "ingest_upload", upload_id, user["id"], str(path), title, metadata.model_dump()
    )
    return UploadOut(id=upload_id, filename=filename, status="queued")


@router.get("/portfolio/uploads", response_model=list[UploadOut])
async def uploads(user: CurrentUser, services: Services) -> list[dict[str, Any]]:
    return await services.repository.uploads(user["id"])


@router.post("/portfolio/github", response_model=UploadOut, status_code=202)
async def ingest_github(body: GithubIngestIn, user: CurrentUser, services: Services) -> UploadOut:
    """Index a repository's README and code on the worker, through the GitHub MCP server."""
    upload_id = uuid.uuid4().hex
    owner, repo = body.repository.split("/", 1)
    await services.repository.add_upload(user["id"], upload_id, body.repository)
    await services.queue.enqueue_job("ingest_github", upload_id, user["id"], owner, repo)
    return UploadOut(id=upload_id, filename=body.repository, status="queued")


def _evidence_rows(services: ApiServices, user: dict[str, Any]) -> list[PortfolioDocument]:
    if services.corpus is None:
        raise HTTPException(503, "The portfolio store is not available.")
    # Until the first run copies the persona's portfolio, show what will be copied.
    return services.corpus.documents(user["id"]) or services.corpus.documents(user["persona"])


@router.get("/portfolio/evidence", response_model=list[EvidenceOut])
async def evidence(user: CurrentUser, services: Services) -> list[EvidenceOut]:
    return [
        EvidenceOut(id=item.item_id, **item.model_dump(exclude={"item_id", "text"}))
        for item in _evidence_rows(services, user)
    ]


@router.delete("/portfolio/evidence/{item_id:path}", status_code=202)
async def delete_evidence(item_id: str, user: CurrentUser, services: Services) -> dict[str, str]:
    """Remove one item from this user's portfolio. Other users are not affected."""
    if item_id not in {item.item_id for item in _evidence_rows(services, user)}:
        raise HTTPException(404, "That item is not in your portfolio.")
    await services.queue.enqueue_job("delete_evidence", user["id"], item_id)
    return {"status": "queued"}


@router.get("/status", response_model=ServiceStatus)
async def status(services: Services) -> ServiceStatus:
    """What the UI's degraded-mode banner reads, and which marketplace this server uses."""
    mode = services.config.marketplace.mode
    if await services.redis.get(DEGRADED_KEY):
        return ServiceStatus(marketplace="unavailable", message=_UNAVAILABLE, marketplace_mode=mode)
    if services.marketplace_health_url:
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                (await client.get(services.marketplace_health_url)).raise_for_status()
        except httpx.HTTPError:
            return ServiceStatus(
                marketplace="unavailable", message=_UNAVAILABLE, marketplace_mode=mode
            )
    return ServiceStatus(marketplace="ok", marketplace_mode=mode)
