"""Proposal studio and pipeline board."""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agent.guardrails import delimit_untrusted
from agent.handoffs import EvidenceItem, ProposalDraft
from agent.nodes.analyst import job_text
from agent.prompt_loader import load_prompt
from apps.api.schemas.api import PipelineCard, ProposalStudio, RegenerateIn, RegenerateOut
from apps.api.services import CurrentUser, Services, bid_thread, read_thread
from rag.index.corpus import CorpusStore
from rag.verify import LexicalJudge, verify_draft

router = APIRouter(prefix="/api", tags=["proposals"])


class _Rewrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)


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


def current_evidence(
    evidence: list[EvidenceItem],
    corpus: CorpusStore | None,
    user_id: str,
) -> tuple[list[EvidenceItem], list[str]]:
    """Drop evidence deleted since the draft was written, so claims citing it are removed."""
    if corpus is None:
        return evidence, []
    kept = [item for item in evidence if corpus.get(user_id, item.evidence_id) is not None]
    gone = [item.evidence_id for item in evidence if item not in kept]
    return kept, gone


@router.post("/proposals/{job_id}/regenerate", response_model=RegenerateOut)
async def regenerate(
    job_id: str,
    body: RegenerateIn,
    user: CurrentUser,
    services: Services,
) -> RegenerateOut:
    """Suggest a new version of one paragraph. Claims are checked before it is returned.

    Nothing changes until the user saves the edit, which sends it through Compliance.
    """
    if services.llm is None:
        raise HTTPException(503, "Regeneration is not available.")
    thread_id = bid_thread(user["id"], job_id)
    run = await services.repository.run(user["id"], thread_id)
    if run is None:
        raise HTTPException(404, "This job has not been analysed yet.")
    values, pending = await read_thread(services, "bid", thread_id)
    if pending is None:
        raise HTTPException(409, "Only a draft awaiting approval can be regenerated.")
    evidence, missing = current_evidence(values.get("evidence", []), services.corpus, user["id"])
    blocks = "\n".join(f"[{item.evidence_id}] {item.excerpt}" for item in evidence)
    data = (
        f"{delimit_untrusted('job_post', job_text(values['job']))}\n\n"
        f"{delimit_untrusted('evidence', blocks)}\n\n"
        f"Paragraph to rewrite:\n{delimit_untrusted('paragraph', body.paragraph)}"
    )
    result = await services.llm.structured("rewrite", load_prompt("rewrite"), data, _Rewrite)
    checked = await asyncio.to_thread(
        verify_draft,
        ProposalDraft(job_id=job_id, cover_letter=result.value.text),
        evidence,
        LexicalJudge(services.config.verification),
        values["fit_report"].missing_skills,
    )
    return RegenerateOut(
        text=checked.draft.cover_letter,
        removed_claims=[claim.text for claim in checked.removed_claims],
        missing_evidence=missing,
    )


@router.get("/pipeline", response_model=list[PipelineCard])
async def pipeline(user: CurrentUser, services: Services) -> list[dict[str, Any]]:
    return await services.repository.pipeline(user["id"])
