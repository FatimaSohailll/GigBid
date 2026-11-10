"""Graph state.

Three things are kept apart (assignment C2):
- State (this module): what this job or conversation needs right now.
- Execution trace: tool names, arguments and results, held in tracing and the audit log.
- Long-term memory: promoted knowledge only, written to a LangGraph store by an explicit rule.

Any field that parallel branches can write needs a reducer, or LangGraph rejects the update.
"""

import operator
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from agent.handoffs import (
    BidDecision,
    ComplianceVerdict,
    Critique,
    EvidenceItem,
    FitReport,
    IntentClassification,
    JobSpec,
    PriceQuote,
    ProposalDraft,
    ReplyDraft,
    RiskReport,
)


class ThreadStatus(StrEnum):
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SKIPPED = "skipped"
    WATCHING = "watching"
    SUBMITTED = "submitted"
    SENT = "sent"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    NEEDS_ATTENTION = "needs_attention"
    DEAD_LETTER = "dead_letter"


class ApprovalAction(StrEnum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ApprovalAction
    user_id: str
    decided_at: datetime
    reason: str | None = None
    edit_diff: str | None = None


class NodeError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: str
    message: str
    retryable: bool = False


class NodeUsage(BaseModel):
    """What one node execution spent. Written by the shared node wrapper, never by a node."""

    model_config = ConfigDict(extra="forbid")

    node: str
    models: list[str] = Field(default_factory=list)
    calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    latency_seconds: float = Field(default=0.0, ge=0)


class OutcomeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    status: str
    recorded_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)


def merge_evidence(
    current: list[EvidenceItem] | None,
    update: list[EvidenceItem] | None,
) -> list[EvidenceItem]:
    """Combine evidence from parallel branches. Later copies of an id replace earlier ones."""
    merged = {item.evidence_id: item for item in current or []}
    merged.update({item.evidence_id: item for item in update or []})
    return list(merged.values())


_STICKY_STATUSES = {ThreadStatus.NEEDS_ATTENTION, ThreadStatus.DEAD_LETTER}


def merge_status(current: ThreadStatus | None, update: ThreadStatus) -> ThreadStatus:
    """Keep a failure visible even if a parallel branch reports normal progress."""
    if current in _STICKY_STATUSES and update not in _STICKY_STATUSES:
        return current
    return update


def keep_first(current: str | None, update: str | None) -> str | None:
    return current or update


class UserSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    floor_usd: float = Field(ge=0)
    weekly_connects_budget: int = Field(ge=0)
    connects_spent_this_week: int = Field(default=0, ge=0)


class _ThreadState(TypedDict, total=False):
    user_id: str
    trace_id: str
    settings: UserSettings
    status: Annotated[ThreadStatus, merge_status]
    attention_reason: Annotated[str | None, keep_first]
    errors: Annotated[list[NodeError], operator.add]
    approvals: Annotated[list[ApprovalRecord], operator.add]
    steps_used: Annotated[int, operator.add]
    tokens_used: Annotated[int, operator.add]
    node_usage: Annotated[list[NodeUsage], operator.add]


class BidState(_ThreadState, total=False):
    """One thread per job."""

    job_id: str
    job: dict[str, Any]
    recent_jobs: list[dict[str, Any]]
    duplicate_of: str | None
    job_spec: JobSpec
    risk_report: RiskReport
    fit_report: FitReport
    evidence: Annotated[list[EvidenceItem], merge_evidence]
    decision: BidDecision
    price_quote: PriceQuote
    drafts: Annotated[list[ProposalDraft], operator.add]
    verification_reports: Annotated[list[dict[str, Any]], operator.add]
    needs_rewrite: bool
    critiques: Annotated[list[Critique], operator.add]
    compliance_verdicts: Annotated[list[ComplianceVerdict], operator.add]
    dry_run_preview: dict[str, Any]
    style_edits: Annotated[list[dict[str, Any]], operator.add]
    proposal_id: str
    outcomes: Annotated[list[OutcomeRecord], operator.add]


class ConversationState(_ThreadState, total=False):
    """One thread per client conversation."""

    conversation_id: str
    conversation: dict[str, Any]
    job: dict[str, Any]
    client_messages: list[dict[str, Any]]
    latest_message: dict[str, Any]
    injected_text: list[str]
    proposed_slots: list[dict[str, Any]]
    selected_slot: datetime | None
    style_edits: Annotated[list[dict[str, Any]], operator.add]
    intent: IntentClassification
    reply_drafts: Annotated[list[ReplyDraft], operator.add]
    compliance_verdicts: Annotated[list[ComplianceVerdict], operator.add]
    dry_run_preview: dict[str, Any]
    outcomes: Annotated[list[OutcomeRecord], operator.add]
