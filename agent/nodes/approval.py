"""Human approval, built on LangGraph interrupts, and the submission that follows it.

A node containing interrupt() runs again from its start on resume, so this one only reads state.
The dry run happens in the node before it, and the writes happen in the node after it.
"""

import difflib
import re
from datetime import UTC, datetime
from typing import Any, Literal

from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.handoffs import ProposalDraft
from agent.runtime import AgentRuntime
from agent.state import ApprovalAction, ApprovalRecord, BidState, OutcomeRecord, ThreadStatus


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "edit", "reject"]
    edited_cover_letter: str | None = Field(default=None, min_length=1)
    reason: str | None = None

    @model_validator(mode="after")
    def _complete(self) -> "ApprovalDecision":
        if self.action == "edit" and self.edited_cover_letter is None:
            raise ValueError("an edit must include the edited text")
        if self.action == "reject" and not self.reason:
            raise ValueError("a rejection must include a reason")
        return self


def idempotency_key(state: BidState, step: str, version: int) -> str:
    """The same approved action always produces the same key, including after a crash."""
    raw = f"{state['user_id']}.{state['job']['job_id']}.v{version}.{step}"
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", raw)[:128]


def _draft_request(state: BidState, draft: ProposalDraft, step: str) -> dict[str, Any]:
    return {
        "job_id": draft.job_id,
        "cover_letter": draft.cover_letter,
        "screening_answers": [item.answer for item in draft.screening_answers],
        "bid_amount": draft.bid_amount,
        "idempotency_key": idempotency_key(state, step, draft.version),
    }


async def prepare(state: BidState, runtime: AgentRuntime) -> dict[str, Any]:
    """Build the preview the approval screen shows. Nothing is sent."""
    draft = state["drafts"][-1]
    result = await runtime.tools.call(
        "submission",
        "create_proposal_draft",
        {"request": {**_draft_request(state, draft, "draft"), "dry_run": True}},
    )
    preview = result["preview"]
    preview["connects_cost"] = state["job"]["connects_cost"]
    preview["connects_balance_after"] = preview["connects_balance"] - preview["connects_cost"]
    questions = state["job"].get("screening_questions", [])
    if len(draft.screening_answers) != len(questions):
        preview["can_execute"] = False
        preview["blockers"].append("Every screening question needs an answer before sending.")
    if preview["connects_balance_after"] < 0:
        preview["can_execute"] = False
        preview["blockers"].append("Not enough Connects.")
    return {"dry_run_preview": preview, "status": ThreadStatus.AWAITING_APPROVAL}


def approval_payload(state: BidState) -> dict[str, Any]:
    draft = state["drafts"][-1]
    evidence = {item.evidence_id: item for item in state.get("evidence", [])}
    critiques = state.get("critiques", [])
    verdicts = state.get("compliance_verdicts", [])
    return {
        "job_id": draft.job_id,
        "draft": draft.model_dump(mode="json"),
        "claim_evidence": {
            claim.claim_id: [
                evidence[item].model_dump(mode="json")
                for item in claim.evidence_ids
                if item in evidence
            ]
            for claim in draft.claims
        },
        "risk_report": state["risk_report"].model_dump(mode="json"),
        "price_rationale": state["price_quote"].rationale,
        "price_quote": state["price_quote"].model_dump(mode="json"),
        "critique": critiques[-1].model_dump(mode="json") if critiques else None,
        "compliance": verdicts[-1].model_dump(mode="json") if verdicts else None,
        "dry_run_preview": state["dry_run_preview"],
    }


async def approval(state: BidState) -> dict[str, Any]:
    decision = ApprovalDecision.model_validate(interrupt(approval_payload(state)))
    draft = state["drafts"][-1]
    record = ApprovalRecord(
        action=ApprovalAction(decision.action),
        user_id=state["user_id"],
        decided_at=datetime.now(UTC),
        reason=decision.reason,
    )
    if decision.action == "reject":
        return {"approvals": [record], "status": ThreadStatus.REJECTED}
    if decision.action == "approve":
        return {"approvals": [record], "status": ThreadStatus.RUNNING}

    edited = str(decision.edited_cover_letter)
    diff = "\n".join(
        difflib.unified_diff(
            draft.cover_letter.splitlines(),
            edited.splitlines(),
            "agent",
            "user",
            lineterm="",
        )
    )
    return {
        "approvals": [record.model_copy(update={"edit_diff": diff})],
        "drafts": [draft.model_copy(update={"cover_letter": edited, "version": draft.version + 1})],
        "style_edits": [{"before": draft.cover_letter, "after": edited, "diff": diff}],
        "status": ThreadStatus.RUNNING,
    }


async def learn(state: BidState, runtime: AgentRuntime) -> dict[str, Any]:
    """Pass this thread's signals to the promotion rules. Safe to repeat on a replay."""
    job = state["job"]
    await runtime.memory.record_edits(state["user_id"], job["job_id"], state.get("style_edits", []))
    proposal_id = state.get("proposal_id")
    if proposal_id:
        await runtime.memory.record_submission(
            state["user_id"], proposal_id, job["category"], state["drafts"][-1].evidence_ids
        )
    return {}


async def submit(state: BidState, runtime: AgentRuntime) -> dict[str, Any]:
    """Send the approved draft. Each key is stable, so a re-run after a crash cannot repeat it."""
    latest = state.get("approvals", [])[-1:]
    if not latest or latest[0].action is not ApprovalAction.APPROVE:
        raise PermissionError("Refusing to submit without an approval")
    draft = state["drafts"][-1]
    created = await runtime.tools.call(
        "submission",
        "create_proposal_draft",
        {"request": _draft_request(state, draft, "draft")},
    )
    proposal_id = created["proposal"]["proposal_id"]
    submitted = await runtime.tools.call(
        "submission",
        "submit_proposal",
        {
            "request": {
                "proposal_id": proposal_id,
                "idempotency_key": idempotency_key(state, "submit", draft.version),
            }
        },
    )
    return {
        "proposal_id": proposal_id,
        "status": (
            ThreadStatus.AWAITING_PLATFORM_CONFIRMATION
            if submitted["proposal"].get("requires_platform_confirmation")
            else ThreadStatus.SUBMITTED
        ),
        "outcomes": [
            OutcomeRecord(
                proposal_id=proposal_id,
                status=submitted["proposal"]["status"],
                recorded_at=datetime.now(UTC),
                details={"connects_spent": submitted["proposal"]["connects_spent"]},
            )
        ],
    }
