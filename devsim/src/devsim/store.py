"""In-memory marketplace state and rules.

The app runs in one event loop and these methods never await, so each operation is atomic.
State is deliberately lost on restart: seed data is the reproducible starting point.
"""

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from devsim.config import LimitsConfig
from devsim.models import (
    ClientHistory,
    ConnectsBalance,
    Conversation,
    Event,
    EventType,
    Job,
    JobPage,
    Message,
    MessageSender,
    Proposal,
    ProposalDraftIn,
    ProposalStatus,
)
from devsim.runtime import SimClock
from devsim.seed_loader import SeedData

_PIPELINE = [
    ProposalStatus.SUBMITTED,
    ProposalStatus.VIEWED,
    ProposalStatus.REPLIED,
    ProposalStatus.INTERVIEW,
    ProposalStatus.HIRED,
]
_TERMINAL = {ProposalStatus.HIRED, ProposalStatus.LOST}


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


@dataclass(frozen=True)
class _IdempotentResult:
    fingerprint: str
    response: dict[str, Any]


def _fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class MarketplaceStore:
    def __init__(self, seed: SeedData, clock: SimClock, limits: LimitsConfig) -> None:
        self._seed = seed
        self._clock = clock
        self._limits = limits
        self.reset()

    def reset(self) -> None:
        self._jobs = {job.job_id: job for job in self._seed.jobs}
        self._clients = {client.client_id: client for client in self._seed.clients}
        self._balances = {f.freelancer_id: f.starting_connects for f in self._seed.freelancers}
        self._spent: defaultdict[str, int] = defaultdict(int)
        self._proposals: dict[str, Proposal] = {}
        self._conversations: dict[str, Conversation] = {}
        self._messages: defaultdict[str, list[Message]] = defaultdict(list)
        self._events: list[Event] = []
        self._idempotent: dict[tuple[str, str, str], _IdempotentResult] = {}
        self._next_id = 1

    def is_known_freelancer(self, freelancer_id: str) -> bool:
        return freelancer_id in self._balances

    def _new_id(self, prefix: str) -> str:
        value = f"{prefix}-{self._next_id:06d}"
        self._next_id += 1
        return value

    def _emit(
        self,
        event_type: EventType,
        proposal: Proposal,
        conversation_id: str | None = None,
    ) -> None:
        self._events.append(
            Event(
                event_id=len(self._events) + 1,
                type=event_type,
                freelancer_id=proposal.freelancer_id,
                proposal_id=proposal.proposal_id,
                conversation_id=conversation_id,
                status=proposal.status,
                at=self._clock.now(),
            )
        )

    # ---- Idempotency -------------------------------------------------------------------

    def once(
        self,
        freelancer_id: str,
        operation: str,
        key: str,
        payload: dict[str, Any],
        action: Callable[[], BaseModel],
    ) -> tuple[dict[str, Any], bool]:
        """Run a write at most once per key. Returns (response, was_replayed).

        Only successful writes are remembered, so a request rejected for insufficient
        Connects can be retried with the same key once the balance allows it.
        """
        scope = (freelancer_id, operation, key)
        fingerprint = _fingerprint(payload)
        existing = self._idempotent.get(scope)
        if existing is not None:
            if existing.fingerprint != fingerprint:
                raise ApiError(
                    422,
                    "idempotency_key_reused",
                    "This Idempotency-Key was already used with a different request.",
                )
            return existing.response, True
        response = action().model_dump(mode="json")
        self._idempotent[scope] = _IdempotentResult(fingerprint, response)
        return response, False

    # ---- Reads -------------------------------------------------------------------------

    def _visible_job(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None or job.posted_at > self._clock.now():
            raise ApiError(404, "job_not_found", f"Job '{job_id}' does not exist.")
        return job

    def search_jobs(
        self,
        query: str | None,
        skills: list[str],
        limit: int,
        cursor: str | None,
    ) -> JobPage:
        if limit < 1 or limit > self._limits.max_page_size:
            raise ApiError(
                400,
                "invalid_limit",
                f"limit must be between 1 and {self._limits.max_page_size}.",
            )
        try:
            offset = int(cursor) if cursor else 0
        except ValueError:
            raise ApiError(400, "invalid_cursor", "cursor is not valid.") from None
        if offset < 0:
            raise ApiError(400, "invalid_cursor", "cursor is not valid.")

        now = self._clock.now()
        terms = (query or "").lower().split()
        wanted_skills = {skill.lower() for skill in skills}

        def matches(job: Job) -> bool:
            if job.posted_at > now:
                return False
            job_skills = {skill.lower() for skill in job.required_skills}
            if not wanted_skills <= job_skills:
                return False
            haystack = " ".join([job.title, job.description, *job.required_skills]).lower()
            return all(term in haystack for term in terms)

        by_id = sorted(filter(matches, self._jobs.values()), key=lambda job: job.job_id)
        ordered = sorted(by_id, key=lambda job: job.posted_at, reverse=True)
        items = ordered[offset : offset + limit]
        end = offset + limit
        return JobPage(items=items, next_cursor=str(end) if end < len(ordered) else None)

    def get_job(self, job_id: str) -> Job:
        return self._visible_job(job_id)

    def get_client_history(self, client_id: str) -> ClientHistory:
        client = self._clients.get(client_id)
        if client is None:
            raise ApiError(404, "client_not_found", f"Client '{client_id}' does not exist.")
        return client.public()

    def get_balance(self, freelancer_id: str) -> ConnectsBalance:
        return ConnectsBalance(
            freelancer_id=freelancer_id,
            balance=self._balances[freelancer_id],
            spent=self._spent[freelancer_id],
        )

    def _owned_proposal(self, freelancer_id: str, proposal_id: str) -> Proposal:
        proposal = self._proposals.get(proposal_id)
        if proposal is None or proposal.freelancer_id != freelancer_id:
            raise ApiError(404, "proposal_not_found", f"Proposal '{proposal_id}' does not exist.")
        return proposal

    def get_proposal(self, freelancer_id: str, proposal_id: str) -> Proposal:
        return self._owned_proposal(freelancer_id, proposal_id)

    def list_proposals(self, freelancer_id: str) -> list[Proposal]:
        return [p for p in self._proposals.values() if p.freelancer_id == freelancer_id]

    def _owned_conversation(self, freelancer_id: str, conversation_id: str) -> Conversation:
        conversation = self._conversations.get(conversation_id)
        if conversation is None or conversation.freelancer_id != freelancer_id:
            raise ApiError(
                404,
                "conversation_not_found",
                f"Conversation '{conversation_id}' does not exist.",
            )
        return conversation

    def list_conversations(self, freelancer_id: str) -> list[Conversation]:
        return [c for c in self._conversations.values() if c.freelancer_id == freelancer_id]

    def list_messages(self, freelancer_id: str, conversation_id: str) -> list[Message]:
        conversation = self._owned_conversation(freelancer_id, conversation_id)
        return list(self._messages[conversation.conversation_id])

    def list_events(self, freelancer_id: str, after: int, limit: int) -> list[Event]:
        if limit < 1 or limit > self._limits.max_page_size:
            raise ApiError(
                400,
                "invalid_limit",
                f"limit must be between 1 and {self._limits.max_page_size}.",
            )
        selected = [
            event
            for event in self._events
            if event.freelancer_id == freelancer_id and event.event_id > after
        ]
        return selected[:limit]

    # ---- Writes ------------------------------------------------------------------------

    def create_draft(self, freelancer_id: str, draft: ProposalDraftIn) -> Proposal:
        self._visible_job(draft.job_id)
        if len(draft.cover_letter) > self._limits.max_cover_letter_chars:
            raise ApiError(
                422,
                "cover_letter_too_long",
                f"cover_letter exceeds {self._limits.max_cover_letter_chars} characters.",
            )
        proposal = Proposal(
            proposal_id=self._new_id("prop"),
            job_id=draft.job_id,
            freelancer_id=freelancer_id,
            cover_letter=draft.cover_letter,
            screening_answers=list(draft.screening_answers),
            bid_amount=draft.bid_amount,
            status=ProposalStatus.DRAFTED,
            created_at=self._clock.now(),
        )
        self._proposals[proposal.proposal_id] = proposal
        self._emit(EventType.PROPOSAL_DRAFTED, proposal)
        return proposal

    def submit_proposal(self, freelancer_id: str, proposal_id: str) -> Proposal:
        proposal = self._owned_proposal(freelancer_id, proposal_id)
        if proposal.status is not ProposalStatus.DRAFTED:
            raise ApiError(409, "already_submitted", "This proposal has already been submitted.")
        job = self._visible_job(proposal.job_id)
        if len(proposal.screening_answers) != len(job.screening_questions):
            raise ApiError(
                422,
                "screening_answers_mismatch",
                f"Expected {len(job.screening_questions)} screening answers, "
                f"got {len(proposal.screening_answers)}.",
            )
        already_bid = any(
            other.job_id == job.job_id
            and other.freelancer_id == freelancer_id
            and other.status is not ProposalStatus.DRAFTED
            for other in self._proposals.values()
        )
        if already_bid:
            raise ApiError(409, "duplicate_bid", "A proposal for this job was already submitted.")
        balance = self._balances[freelancer_id]
        if balance < job.connects_cost:
            raise ApiError(
                402,
                "insufficient_connects",
                f"This job costs {job.connects_cost} Connects; the balance is {balance}.",
            )
        self._balances[freelancer_id] = balance - job.connects_cost
        self._spent[freelancer_id] += job.connects_cost
        updated = proposal.model_copy(
            update={
                "status": ProposalStatus.SUBMITTED,
                "connects_spent": job.connects_cost,
                "submitted_at": self._clock.now(),
            }
        )
        self._proposals[proposal_id] = updated
        self._emit(EventType.PROPOSAL_SUBMITTED, updated)
        return updated

    def send_message(self, freelancer_id: str, conversation_id: str, body: str) -> Message:
        conversation = self._owned_conversation(freelancer_id, conversation_id)
        self._check_message_length(body)
        message = self._append_message(conversation, MessageSender.FREELANCER, body)
        proposal = self._proposals[conversation.proposal_id]
        self._emit(EventType.MESSAGE_SENT, proposal, conversation.conversation_id)
        return message

    def _check_message_length(self, body: str) -> None:
        if len(body) > self._limits.max_message_chars:
            raise ApiError(
                422,
                "message_too_long",
                f"body exceeds {self._limits.max_message_chars} characters.",
            )

    def _append_message(
        self,
        conversation: Conversation,
        sender: MessageSender,
        body: str,
    ) -> Message:
        message = Message(
            message_id=self._new_id("msg"),
            conversation_id=conversation.conversation_id,
            sender=sender,
            body=body,
            sent_at=self._clock.now(),
        )
        self._messages[conversation.conversation_id].append(message)
        return message

    # ---- Simulator controls ------------------------------------------------------------

    def _any_proposal(self, proposal_id: str) -> Proposal:
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ApiError(404, "proposal_not_found", f"Proposal '{proposal_id}' does not exist.")
        return proposal

    def _set_status(self, proposal: Proposal, status: ProposalStatus) -> Proposal:
        updated = proposal.model_copy(update={"status": status})
        self._proposals[proposal.proposal_id] = updated
        self._emit(EventType.PROPOSAL_STATUS_CHANGED, updated)
        return updated

    def change_status(self, proposal_id: str, status: ProposalStatus) -> Proposal:
        proposal = self._any_proposal(proposal_id)
        current = proposal.status
        invalid = (
            current is ProposalStatus.DRAFTED
            or current in _TERMINAL
            or status is ProposalStatus.DRAFTED
            or (
                status is not ProposalStatus.LOST
                and _PIPELINE.index(status) <= _PIPELINE.index(current)
            )
        )
        if invalid:
            raise ApiError(
                409,
                "invalid_status_change",
                f"Cannot move a proposal from '{current.value}' to '{status.value}'.",
            )
        return self._set_status(proposal, status)

    def receive_client_message(self, proposal_id: str, body: str) -> Message:
        proposal = self._any_proposal(proposal_id)
        if proposal.status is ProposalStatus.DRAFTED or proposal.status in _TERMINAL:
            raise ApiError(
                409,
                "proposal_not_open",
                f"A client cannot reply to a '{proposal.status.value}' proposal.",
            )
        self._check_message_length(body)
        conversation = next(
            (c for c in self._conversations.values() if c.proposal_id == proposal_id),
            None,
        )
        if conversation is None:
            conversation = Conversation(
                conversation_id=self._new_id("conv"),
                proposal_id=proposal_id,
                job_id=proposal.job_id,
                client_id=self._jobs[proposal.job_id].client_id,
                freelancer_id=proposal.freelancer_id,
            )
            self._conversations[conversation.conversation_id] = conversation
        message = self._append_message(conversation, MessageSender.CLIENT, body)
        if proposal.status in {ProposalStatus.SUBMITTED, ProposalStatus.VIEWED}:
            proposal = self._set_status(proposal, ProposalStatus.REPLIED)
        self._emit(EventType.MESSAGE_RECEIVED, proposal, conversation.conversation_id)
        return message
