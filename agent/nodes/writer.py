"""Proposal Writer: draft a personalised proposal that cites evidence."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.guardrails import delimit_untrusted
from agent.handoffs import ProposalDraft, ScreeningAnswer
from agent.nodes.analyst import job_text
from agent.runtime import AgentRuntime
from agent.state import BidState


class _ModelDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cover_letter: str = Field(min_length=1)
    screening_answers: list[ScreeningAnswer] = Field(default_factory=list)


async def _phrasing_examples(state: BidState, runtime: AgentRuntime) -> str:
    """V4: how winning proposals were worded. Kept apart from evidence, so it can't be cited."""
    if not runtime.config.outcome_aware:
        return ""
    spec = state["job_spec"]
    found = await runtime.tools.call(
        "writer",
        "search_evidence",
        {
            "requirement": " ".join([spec.title, *spec.required_skills]),
            "k": runtime.config.writing.phrasing_examples,
            "filters": {"types": ["proposal"], "outcomes": ["won", "replied"]},
        },
    )
    excerpts = "\n---\n".join(item["excerpt"] for item in found["items"])
    return f"\n\n{delimit_untrusted('phrasing_examples', excerpts)}" if excerpts else ""


async def run(state: BidState, runtime: AgentRuntime) -> dict[str, Any]:
    job = state["job"]
    spec = state["job_spec"]
    evidence = "\n".join(
        f"[{item.evidence_id}] {item.excerpt}" for item in state.get("evidence", [])
    )
    critiques = state.get("critiques", [])
    feedback = ""
    if critiques and critiques[-1].reasons:
        reasons = "; ".join(critiques[-1].reasons)
        feedback = f"\n\nReviewer feedback:\n{delimit_untrusted('revision_feedback', reasons)}"
    style = await runtime.memory.style_rules(state["user_id"])
    what_worked = await runtime.memory.outcome_insights(state["user_id"], job["category"])
    data = (
        f"{delimit_untrusted('job_post', job_text(job))}\n\n"
        f"Hidden needs: {spec.hidden_needs}\nDeliverables: {spec.deliverables}\n"
        f"Bid: {state['price_quote'].amount}\n"
        f"Style rules: {style}\nWhat has worked before: {what_worked}\n\n"
        f"{delimit_untrusted('evidence', evidence)}{feedback}"
        f"{await _phrasing_examples(state, runtime)}"
    )
    result = await runtime.llm.structured("writer", runtime.prompt("writer"), data, _ModelDraft)
    drafts = state.get("drafts", [])
    draft = ProposalDraft(
        job_id=job["job_id"],
        version=(drafts[-1].version + 1) if drafts else 1,
        cover_letter=result.value.cover_letter,
        screening_answers=result.value.screening_answers,
        bid_amount=state["price_quote"].amount,
    )
    return {"drafts": [draft], "tokens_used": result.tokens}
