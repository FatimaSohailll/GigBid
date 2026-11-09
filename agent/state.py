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


class _ThreadState(TypedDict, total=False):
    user_id: str
    trace_id: str
    status: ThreadStatus
    errors: Annotated[list[NodeError], operator.add]
    approvals: Annotated[list[ApprovalRecord], operator.add]
    steps_used: Annotated[int, operator.add]
    tokens_used: Annotated[int, operator.add]


class BidState(_ThreadState, total=False):
    """One thread per job."""

    job_id: str
    job: dict[str, Any]
    job_spec: JobSpec
    risk_report: RiskReport
    fit_report: FitReport
    evidence: Annotated[list[EvidenceItem], merge_evidence]
    decision: BidDecision
    price_quote: PriceQuote
    drafts: Annotated[list[ProposalDraft], operator.add]
    critiques: Annotated[list[Critique], operator.add]
    compliance_verdicts: Annotated[list[ComplianceVerdict], operator.add]
    dry_run_preview: dict[str, Any]
    outcomes: Annotated[list[OutcomeRecord], operator.add]


class ConversationState(_ThreadState, total=False):
    """One thread per client conversation."""

    conversation_id: str
    client_messages: list[dict[str, Any]]
    intent: IntentClassification
    reply_drafts: Annotated[list[ReplyDraft], operator.add]
    compliance_verdicts: Annotated[list[ComplianceVerdict], operator.add]
    dry_run_preview: dict[str, Any]
    outcomes: Annotated[list[OutcomeRecord], operator.add]
