"""Request and response models. The TypeScript client is generated from these."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.handoffs import (
    BidDecision,
    ComplianceVerdict,
    Critique,
    EvidenceItem,
    FitReport,
    JobSpec,
    PriceQuote,
    ProposalDraft,
    ReplyDraft,
    RiskReport,
)

Persona = Literal["ai_ml_engineer", "fullstack_developer", "data_analyst"]
Autonomy = Literal["manual", "auto_skip_low_fit"]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignupIn(_Input):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    password: str = Field(min_length=10, max_length=256)
    persona: Persona


class LoginIn(_Input):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class UserOut(BaseModel):
    id: str
    email: str
    persona: str
    floor_usd: float
    weekly_connects_budget: int
    autonomy: str
    marketplace: str


class SettingsIn(_Input):
    floor_usd: float = Field(ge=0, le=1_000_000)
    weekly_connects_budget: int = Field(ge=0, le=10_000)
    autonomy: Autonomy


class SavedSearchIn(_Input):
    query: str | None = Field(default=None, max_length=200)
    skills: list[str] = Field(default_factory=list, max_length=10)
    interval_minutes: int = Field(ge=1, le=10_080)


class SavedSearchOut(BaseModel):
    id: str
    query: str | None
    skills: list[str]
    interval_minutes: int
    last_run_at: datetime | None


class JobSummary(BaseModel):
    job_id: str
    title: str
    category: str
    status: str
    fit: float | None
    risk: float | None
    action: str | None
    price: float | None
    connects_cost: int
    risk_flags: list[str]
    budget_min: float
    budget_max: float
    run_status: str | None = None
    thread_id: str | None = None


class JobDetail(BaseModel):
    job: JobSummary
    description: str
    job_spec: JobSpec | None = None
    risk_report: RiskReport | None = None
    fit_report: FitReport | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    decision: BidDecision | None = None
    price_quote: PriceQuote | None = None


class QueuedRun(BaseModel):
    thread_id: str
    status: str


class ProposalStudio(BaseModel):
    thread_id: str
    status: str
    draft: ProposalDraft | None = None
    critiques: list[Critique] = Field(default_factory=list)
    compliance: list[ComplianceVerdict] = Field(default_factory=list)
    verification_reports: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    pending_approval: dict[str, Any] | None = None


class PipelineCard(BaseModel):
    proposal_id: str
    job_id: str
    title: str
    category: str
    status: str
    connects_spent: int
    updated_at: datetime


class ApprovalSummary(BaseModel):
    thread_id: str
    kind: str
    ref_id: str
    started_at: datetime


class ApprovalDetail(ApprovalSummary):
    payload: dict[str, Any]


class ApprovalIn(_Input):
    action: Literal["approve", "edit", "reject"]
    edited_text: str | None = Field(default=None, min_length=1, max_length=8000)
    reason: str | None = Field(default=None, max_length=1000)
    selected_slot: datetime | None = None


class ConversationSummary(BaseModel):
    conversation_id: str
    thread_id: str
    status: str


class ConversationDetail(ConversationSummary):
    messages: list[dict[str, Any]] = Field(default_factory=list)
    intent: dict[str, Any] | None = None
    reply: ReplyDraft | None = None
    proposed_slots: list[dict[str, Any]] = Field(default_factory=list)
    pending_approval: dict[str, Any] | None = None


class RunOut(BaseModel):
    thread_id: str
    kind: str
    ref_id: str
    status: str
    trace_id: str
    tokens: int
    started_at: datetime
    finished_at: datetime | None
    attention_reason: str | None
    rejection_reason: str | None


class CategoryStats(BaseModel):
    category: str
    submitted: int
    replied: int
    hired: int
    connects_spent: int
    reply_rate: float
    hire_rate: float


class RejectionStat(BaseModel):
    reason: str
    count: int


class Analytics(BaseModel):
    by_category: list[CategoryStats]
    runs: int
    mean_tokens_per_run: float
    p95_latency_seconds: float
    top_rejection_reasons: list[RejectionStat]


class ServiceStatus(BaseModel):
    marketplace: Literal["ok", "unavailable"]
    message: str | None = None


class MemoryItem(BaseModel):
    key: str
    text: str
    detail: dict[str, Any]


class MemoryOut(BaseModel):
    style: list[MemoryItem]
    outcome: list[MemoryItem]


class UploadOut(BaseModel):
    id: str
    filename: str
    status: str
