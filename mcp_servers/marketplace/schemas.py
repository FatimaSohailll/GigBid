"""MCP tool inputs and outputs."""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from agent.marketplace_port import Conversation, MarketEvent, Message, Proposal

IdempotencyKey = Annotated[
    str,
    Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
        description="Unique per intended action. Reuse the same value when retrying it.",
    ),
]
DryRun = Annotated[
    bool,
    Field(description="If true, return what would be sent and what it costs, and send nothing."),
]
ResourceId = Annotated[str, Field(min_length=1, max_length=128)]


class _ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProposalDraftInput(_ToolInput):
    job_id: ResourceId
    cover_letter: str = Field(min_length=1, max_length=5000)
    screening_answers: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(
        default_factory=list,
        max_length=10,
    )
    bid_amount: float = Field(gt=0, le=1_000_000)
    idempotency_key: IdempotencyKey
    dry_run: DryRun = False


class SubmitProposalInput(_ToolInput):
    proposal_id: ResourceId
    idempotency_key: IdempotencyKey
    dry_run: DryRun = False


class SendMessageInput(_ToolInput):
    conversation_id: ResourceId
    body: str = Field(min_length=1, max_length=4000)
    idempotency_key: IdempotencyKey
    dry_run: DryRun = False


class DryRunPreview(BaseModel):
    tool: str
    payload: dict[str, Any]
    connects_cost: int
    connects_balance: int
    connects_balance_after: int
    can_execute: bool
    blockers: list[str] = Field(default_factory=list)
    requires_platform_confirmation: bool = False


class ProposalWriteResult(BaseModel):
    dry_run: bool
    replayed: bool = False
    proposal: Proposal | None = None
    preview: DryRunPreview | None = None


class MessageWriteResult(BaseModel):
    dry_run: bool
    replayed: bool = False
    message: Message | None = None
    preview: DryRunPreview | None = None


class ConversationList(BaseModel):
    items: list[Conversation]


class MessageList(BaseModel):
    items: list[Message]


class EventList(BaseModel):
    items: list[MarketEvent]
