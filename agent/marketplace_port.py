"""The single marketplace interface (R1).

Adapters in mcp_servers/marketplace/adapters/ implement this port. The Marketplace MCP server
is the only caller, and agents reach it through MCP tools, so a graph cannot tell which
marketplace is behind it.
"""

from contextvars import ContextVar
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

current_trace_id: ContextVar[str | None] = ContextVar("current_trace_id", default=None)


class _Record(BaseModel):
    """Tolerates extra fields, so that a marketplace can add data without breaking GigBid."""

    model_config = ConfigDict(extra="ignore")


class Budget(_Record):
    min: float
    max: float
    currency: str = "USD"


class MarketJob(_Record):
    job_id: str
    client_id: str
    title: str
    description: str
    category: str
    required_skills: list[str] = Field(default_factory=list)
    budget: Budget
    duration_days: int
    screening_questions: list[str] = Field(default_factory=list)
    connects_cost: int
    posted_at: datetime


class JobPage(_Record):
    items: list[MarketJob]
    next_cursor: str | None = None


class ClientReview(_Record):
    rating: float
    comment: str


class ClientHistory(_Record):
    client_id: str
    display_name: str
    industry: str
    completed_projects: int
    average_rating: float
    payment_verified: bool
    total_spent_usd: float
    reviews: list[ClientReview] = Field(default_factory=list)


class ConnectsBalance(_Record):
    freelancer_id: str
    balance: int
    spent: int


class PriceStats(_Record):
    category: str
    skills: list[str] = Field(default_factory=list)
    sample_size: int
    p25: float
    p50: float
    p75: float
    currency: str = "USD"


class Proposal(_Record):
    proposal_id: str
    job_id: str
    freelancer_id: str
    cover_letter: str
    screening_answers: list[str] = Field(default_factory=list)
    bid_amount: float
    status: str
    connects_spent: int = 0
    created_at: datetime
    submitted_at: datetime | None = None
    requires_platform_confirmation: bool = False


class Conversation(_Record):
    conversation_id: str
    proposal_id: str
    job_id: str
    client_id: str
    freelancer_id: str


class Message(_Record):
    message_id: str
    conversation_id: str
    sender: str
    body: str
    sent_at: datetime


class MarketEvent(_Record):
    event_id: int
    type: str
    freelancer_id: str
    proposal_id: str
    conversation_id: str | None = None
    status: str | None = None
    at: datetime


class ProposalDraftRequest(_Record):
    job_id: str
    cover_letter: str
    screening_answers: list[str] = Field(default_factory=list)
    bid_amount: float


class MarketplaceError(Exception):
    code = "marketplace_error"
    retryable = False

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class MarketplaceNotFound(MarketplaceError):
    code = "not_found"


class MarketplaceConflict(MarketplaceError):
    code = "conflict"


class MarketplaceRejected(MarketplaceError):
    code = "invalid_request"


class InsufficientConnects(MarketplaceError):
    code = "insufficient_connects"


class MarketplaceAuthError(MarketplaceError):
    code = "marketplace_auth_failed"


class MarketplaceRateLimited(MarketplaceError):
    code = "rate_limited"
    retryable = True


class MarketplaceUnavailable(MarketplaceError):
    """The UI's degraded-mode banner keys on this error code."""

    code = "marketplace_unavailable"
    retryable = True


class SubmissionMode(StrEnum):
    """What "submit" means on this marketplace, so the graph need not know which one it uses."""

    DIRECT = "direct"  # GigSim: the marketplace receives the proposal
    PLATFORM_CONFIRMATION = "platform_confirmation"  # Upwork: the user confirms the draft there


class MarketplacePort(Protocol):
    submission_mode: SubmissionMode

    async def search_jobs(
        self,
        query: str | None,
        skills: list[str],
        limit: int,
        cursor: str | None,
    ) -> JobPage: ...

    async def get_job(self, job_id: str) -> MarketJob: ...

    async def get_client_history(self, client_id: str) -> ClientHistory: ...

    async def get_connects_balance(self) -> ConnectsBalance: ...

    async def get_price_stats(self, category: str, skills: list[str]) -> PriceStats: ...

    async def get_proposal(self, proposal_id: str) -> Proposal: ...

    async def list_conversations(self) -> list[Conversation]: ...

    async def list_messages(self, conversation_id: str) -> list[Message]: ...

    async def list_events(self, after: int, limit: int) -> list[MarketEvent]: ...

    async def create_proposal_draft(
        self,
        draft: ProposalDraftRequest,
        idempotency_key: str,
    ) -> Proposal: ...

    async def submit_proposal(self, proposal_id: str, idempotency_key: str) -> Proposal: ...

    async def send_message(
        self,
        conversation_id: str,
        body: str,
        idempotency_key: str,
    ) -> Message: ...

    async def aclose(self) -> None: ...
