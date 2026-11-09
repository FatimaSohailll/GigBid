"""Seed records and API schemas."""

from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    """Rejects unknown fields, so gold labels cannot slip into API-visible records."""

    model_config = ConfigDict(extra="forbid")


class Budget(_Strict):
    min: float = Field(ge=0)
    max: float = Field(ge=0)
    currency: str = "USD"


class Job(_Strict):
    job_id: str
    client_id: str
    title: str
    description: str
    category: str
    required_skills: list[str] = Field(default_factory=list)
    budget: Budget
    duration_days: int = Field(gt=0)
    screening_questions: list[str] = Field(default_factory=list)
    connects_cost: int = Field(ge=0)
    posted_at: AwareDatetime


class ClientReview(_Strict):
    rating: float = Field(ge=0, le=5)
    comment: str


class ClientHistory(_Strict):
    client_id: str
    display_name: str
    industry: str
    completed_projects: int = Field(ge=0)
    average_rating: float = Field(ge=0, le=5)
    payment_verified: bool
    total_spent_usd: float = Field(ge=0)
    reviews: list[ClientReview] = Field(default_factory=list)


class ClientRecord(ClientHistory):
    """Seed-only client. ``hidden_preferences`` must never reach an API response."""

    hidden_preferences: dict[str, Any] = Field(default_factory=dict)

    def public(self) -> ClientHistory:
        return ClientHistory.model_validate(self.model_dump(exclude={"hidden_preferences"}))


class Freelancer(_Strict):
    freelancer_id: str
    display_name: str
    starting_connects: int = Field(ge=0)


class ProposalStatus(StrEnum):
    DRAFTED = "drafted"
    SUBMITTED = "submitted"
    VIEWED = "viewed"
    REPLIED = "replied"
    INTERVIEW = "interview"
    HIRED = "hired"
    LOST = "lost"


class ProposalDraftIn(_Strict):
    job_id: str = Field(min_length=1)
    cover_letter: str = Field(min_length=1)
    screening_answers: list[str] = Field(default_factory=list)
    bid_amount: float = Field(gt=0)


class Proposal(_Strict):
    proposal_id: str
    job_id: str
    freelancer_id: str
    cover_letter: str
    screening_answers: list[str]
    bid_amount: float
    status: ProposalStatus
    connects_spent: int = 0
    created_at: AwareDatetime
    submitted_at: AwareDatetime | None = None


class ConnectsBalance(_Strict):
    freelancer_id: str
    balance: int
    spent: int


class JobPage(_Strict):
    items: list[Job]
    next_cursor: str | None = None


class Conversation(_Strict):
    conversation_id: str
    proposal_id: str
    job_id: str
    client_id: str
    freelancer_id: str


class MessageSender(StrEnum):
    FREELANCER = "freelancer"
    CLIENT = "client"


class Message(_Strict):
    message_id: str
    conversation_id: str
    sender: MessageSender
    body: str
    sent_at: AwareDatetime


class MessageIn(_Strict):
    body: str = Field(min_length=1)


class EventType(StrEnum):
    PROPOSAL_DRAFTED = "proposal_drafted"
    PROPOSAL_SUBMITTED = "proposal_submitted"
    PROPOSAL_STATUS_CHANGED = "proposal_status_changed"
    MESSAGE_RECEIVED = "message_received"
    MESSAGE_SENT = "message_sent"


class Event(_Strict):
    event_id: int
    type: EventType
    freelancer_id: str
    proposal_id: str
    conversation_id: str | None = None
    status: ProposalStatus | None = None
    at: AwareDatetime


class HistoricalJob(_Strict):
    job_id: str
    category: str
    required_skills: list[str]
    budget: Budget
    final_price_usd: float = Field(gt=0)
    duration_days: int = Field(gt=0)
    proposals_received: int = Field(ge=0)
    posted_at: AwareDatetime


class PriceStats(_Strict):
    category: str
    skills: list[str]
    sample_size: int
    p25: float
    p50: float
    p75: float
    currency: str = "USD"


class Health(_Strict):
    status: str
    simulator: str
    official_gigsim: bool
    version: str
    sim_time: AwareDatetime


class ClockAdvanceIn(_Strict):
    minutes: float = Field(gt=0)


class FaultRatesIn(_Strict):
    error_rate: float = Field(ge=0, le=1)
    post_commit_error_rate: float = Field(ge=0, le=1)


class StatusChangeIn(_Strict):
    status: ProposalStatus
