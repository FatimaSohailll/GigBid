"""Structured hand-offs: agents exchange these objects, not free text, for later decisions."""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Handoff(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobSpec(_Handoff):
    job_id: str
    title: str
    deliverables: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    budget_min: float | None = Field(default=None, ge=0)
    budget_max: float | None = Field(default=None, ge=0)
    currency: str = "USD"
    timeline_days: int | None = Field(default=None, gt=0)
    screening_questions: list[str] = Field(default_factory=list)
    hidden_needs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _budget_range(self) -> Self:
        if (
            self.budget_min is not None
            and self.budget_max is not None
            and self.budget_max < self.budget_min
        ):
            raise ValueError("budget_max must not be below budget_min")
        return self


class RiskFlag(StrEnum):
    SCAM = "scam"
    FREE_WORK = "free_work"
    OFF_PLATFORM = "off_platform"
    INJECTION = "injection"
    CREDENTIAL_REQUEST = "credential_request"
    UPFRONT_PAYMENT = "upfront_payment"
    LOW_CLIENT_QUALITY = "low_client_quality"
    PAYMENT_UNVERIFIED = "payment_unverified"


class RiskReport(_Handoff):
    score: float = Field(ge=0, le=1)
    client_quality: float | None = Field(default=None, ge=0, le=1)
    flags: list[RiskFlag] = Field(default_factory=list)
    injected_text: list[str] = Field(default_factory=list)
    explanation: str


class EvidenceItem(_Handoff):
    evidence_id: str
    type: str
    source: str
    excerpt: str
    date: str | None = None
    outcome: str | None = None
    score: float | None = None


class CoverageStatus(StrEnum):
    COVERED = "covered"
    PARTIAL = "partial"
    MISSING = "missing"


class RequirementMatch(_Handoff):
    """One row of the requirement-to-evidence matrix."""

    requirement: str
    status: CoverageStatus
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str = ""

    @model_validator(mode="after")
    def _covered_needs_evidence(self) -> Self:
        if self.status is not CoverageStatus.MISSING and not self.evidence_ids:
            raise ValueError("a covered or partial requirement must cite evidence")
        return self


class FitReport(_Handoff):
    score: float = Field(ge=0, le=1)
    matches: list[RequirementMatch] = Field(default_factory=list)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class BidAction(StrEnum):
    SKIP = "skip"
    WATCH = "watch"
    BID = "bid"


class BidDecision(_Handoff):
    job_id: str
    action: BidAction
    fit_score: float = Field(ge=0, le=1)
    risk_score: float = Field(ge=0, le=1)
    reasons: list[str] = Field(min_length=1)


class PriceQuote(_Handoff):
    job_id: str
    amount: float = Field(gt=0)
    currency: str = "USD"
    floor: float = Field(ge=0)
    market_p25: float | None = Field(default=None, ge=0)
    market_p50: float | None = Field(default=None, ge=0)
    market_p75: float | None = Field(default=None, ge=0)
    connects_cost: int = Field(ge=0)
    connects_worth_it: bool
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def _respects_floor(self) -> Self:
        if self.amount < self.floor:
            raise ValueError("amount must not be below the user's floor")
        return self


class ClaimSupport(StrEnum):
    SUPPORTED = "supported"
    PARTLY_SUPPORTED = "partly_supported"
    UNSUPPORTED = "unsupported"


class ProposalClaim(_Handoff):
    claim_id: str
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    support: ClaimSupport | None = None
    explanation: str = ""

    @model_validator(mode="after")
    def _support_needs_evidence(self) -> Self:
        grounded = {ClaimSupport.SUPPORTED, ClaimSupport.PARTLY_SUPPORTED}
        if self.support in grounded and not self.evidence_ids:
            raise ValueError("a supported claim must cite evidence")
        return self


class ScreeningAnswer(_Handoff):
    question: str
    answer: str


class DraftStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProposalDraft(_Handoff):
    job_id: str
    version: int = Field(default=1, ge=1)
    cover_letter: str
    screening_answers: list[ScreeningAnswer] = Field(default_factory=list)
    bid_amount: float | None = Field(default=None, gt=0)
    claims: list[ProposalClaim] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    status: DraftStatus = DraftStatus.PENDING_REVIEW


class CritiqueVerdict(StrEnum):
    PASS = "pass"
    REVISE = "revise"


class Critique(_Handoff):
    job_id: str
    draft_version: int = Field(ge=1)
    verdict: CritiqueVerdict
    grounded: bool
    personalised: bool
    length_ok: bool
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _revise_needs_reasons(self) -> Self:
        if self.verdict is CritiqueVerdict.REVISE and not self.reasons:
            raise ValueError("a draft sent back must say why")
        return self


class ComplianceAction(StrEnum):
    ALLOW = "allow"
    REDACTED = "redacted"
    BLOCK = "block"


class PolicyViolation(_Handoff):
    rule_id: str
    excerpt: str
    explanation: str


class ComplianceVerdict(_Handoff):
    job_id: str
    draft_version: int = Field(ge=1)
    action: ComplianceAction
    violations: list[PolicyViolation] = Field(default_factory=list)
    redacted_text: str | None = None
    reason: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.action is ComplianceAction.BLOCK and not (self.violations and self.reason):
            raise ValueError("a block must cite violations and give a reason shown to the user")
        if self.action is ComplianceAction.REDACTED and self.redacted_text is None:
            raise ValueError("a redaction must include the redacted text")
        return self


class ClientIntent(StrEnum):
    QUESTION = "question"
    NEGOTIATION = "negotiation"
    INTERVIEW_REQUEST = "interview_request"
    SCAM_SIGNAL = "scam_signal"


class IntentClassification(_Handoff):
    conversation_id: str
    intent: ClientIntent
    confidence: float = Field(ge=0, le=1)
    explanation: str


class ReplyDraft(_Handoff):
    conversation_id: str
    body: str = Field(min_length=1)
    proposed_slots: list[str] = Field(default_factory=list)
