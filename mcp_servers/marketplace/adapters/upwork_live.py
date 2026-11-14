"""Live Upwork adapter (A4): the marketplace port over Upwork's official MCP server.

GigBid acts as an MCP client of https://mcp.upwork.com/mcp with each user's own OAuth token.
Nothing here scrapes a page or drives a browser. Upwork's tools return JSON as text, with no
output schemas, so every response is parsed defensively and anything missing is reported, never
invented. Response shapes were checked against the live server on 2026-11-14.

Sending is two steps, matching both Upwork's tools and the user's choice:
  submit_proposal     upwork__manage_proposals action=create. Upwork returns a preview with the
                      Connects cost and balance. Nothing reaches the client.
  confirm_submission  upwork__confirm_preview with that preview's id, only after a second,
                      separate human approval in GigBid.

Mapped with documented approximations: search results lack the Connects cost, so each result is
fetched with action=get; Upwork has no price statistics (get_price_stats is not_found, which the
pricing node already handles) and no event feed (list_events is empty; replies are read from
conversations).
"""

import hashlib
import json
import re
import sqlite3
import threading
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from agent.config import MarketplaceConfig
from agent.marketplace_port import (
    Budget,
    ClientHistory,
    ConnectsBalance,
    Conversation,
    JobPage,
    MarketEvent,
    MarketJob,
    MarketplaceAuthError,
    MarketplaceConflict,
    MarketplaceError,
    MarketplaceNotFound,
    MarketplaceRateLimited,
    MarketplaceRejected,
    MarketplaceUnavailable,
    Message,
    PriceStats,
    Proposal,
    ProposalDraftRequest,
    SubmissionMode,
)
from mcp_servers.marketplace.adapters.gigsim import PerUserRateLimiter

TOOL_PREFIX = "upwork__"
_SEARCH_PAGE_LIMIT = 10  # Upwork's own maximum
_DURATION_DAYS = {
    "less than 1 month": 21,
    "1 to 3 months": 60,
    "3 to 6 months": 135,
    "more than 6 months": 240,
}
_DEFAULT_DURATION_DAYS = 30
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*(?:\.\d+)?")


# ---- Talking to Upwork's server -----------------------------------------------------------


class UpworkTools(Protocol):
    """One call to an Upwork MCP tool, returning its parsed JSON. Tests pass a fake."""

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


TokenSource = Callable[[], Awaitable[str]]


def _error_for(text: str) -> MarketplaceError:
    lowered = text.lower()
    if any(word in lowered for word in ("invalid_token", "unauthor", "401", "expired token")):
        return MarketplaceAuthError("Upwork refused the sign-in. Connect Upwork again in Settings.")
    if ("rate" in lowered and "limit" in lowered) or "429" in lowered:
        return MarketplaceRateLimited(f"Upwork rate limit: {text[:200]}")
    if "not found" in lowered or "not_found" in lowered:
        return MarketplaceNotFound(text[:300])
    if "vj-ja-10" in lowered or "already" in lowered:
        return MarketplaceConflict(text[:300])
    return MarketplaceRejected(text[:300])


def parse_tool_text(content: list[Any]) -> dict[str, Any]:
    text = "".join(getattr(item, "text", "") for item in content)
    try:
        value = json.loads(text) if text else {}
    except json.JSONDecodeError:
        raise MarketplaceRejected(f"Upwork returned text that is not JSON: {text[:200]}") from None
    if not isinstance(value, dict):
        raise MarketplaceRejected("Upwork returned an unexpected response.")
    return value


class McpUpworkTools:
    """Calls Upwork's MCP server over Streamable HTTP as one user."""

    def __init__(self, url: str, token: TokenSource, timeout_seconds: float) -> None:
        self._url = url
        self._token = token
        self._timeout = timeout_seconds

    @asynccontextmanager
    async def _session(self) -> Any:
        headers = {"Authorization": f"Bearer {await self._token()}"}
        http = create_mcp_http_client(headers=headers, timeout=httpx.Timeout(self._timeout))
        async with (
            http,
            streamable_http_client(self._url, http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._session() as session:
                result = await session.call_tool(f"{TOOL_PREFIX}{tool}", arguments)
        except MarketplaceError:
            raise
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (401, 403):
                raise MarketplaceAuthError(
                    "Upwork refused the sign-in. Connect Upwork again in Settings."
                ) from error
            raise MarketplaceUnavailable(
                f"Upwork returned {error.response.status_code}."
            ) from error
        except (httpx.HTTPError, OSError, ExceptionGroup) as error:
            raise MarketplaceUnavailable(f"Upwork is unreachable: {error}") from error
        if result.isError:
            raise _error_for("".join(getattr(item, "text", "") for item in result.content))
        return parse_tool_text(result.content)


# ---- Parsing Upwork's responses -------------------------------------------------------------


def _find(value: Any, key: str) -> Any:
    """The first value stored under key anywhere in a nested response."""
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for item in value.values():
            found = _find(item, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find(item, key)
            if found is not None:
                return found
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        match = _NUMBER.search(value.replace(",", ""))
        return float(match.group(0)) if match else None
    if isinstance(value, dict):
        for key in ("rawValue", "amount", "value"):
            if key in value:
                return _number(value[key])
    return None


def parse_budget_text(text: str | None) -> tuple[float, float]:
    """Upwork's search budget, e.g. '60.00–120.00/hr', '$500', 'up to 40/hr', '30+/hr'."""
    if not text:
        return 0.0, 0.0
    numbers = [float(item) for item in _NUMBER.findall(text.replace(",", ""))]
    if not numbers:
        return 0.0, 0.0
    if len(numbers) >= 2:
        return min(numbers[:2]), max(numbers[:2])
    if text.strip().lower().startswith("up to"):
        return 0.0, numbers[0]
    return numbers[0], numbers[0]


def _budget(terms: Mapping[str, Any], search_budget: str | None) -> Budget:
    hourly = terms.get("hourlyContractTerms") or {}
    if hourly:
        low = _number(hourly.get("hourlyBudgetMin")) or 0.0
        high = _number(hourly.get("hourlyBudgetMax")) or low
        return Budget(min=low, max=high)
    fixed = terms.get("fixedPriceContractTerms") or {}
    amount = _number(fixed.get("amount")) if fixed else None
    if amount is not None:
        top = _number(fixed.get("maxAmount")) or amount
        return Budget(min=amount, max=top)
    low, high = parse_budget_text(search_budget)
    return Budget(min=low, max=high)


def _when(*values: Any) -> datetime:
    for value in values:
        if isinstance(value, str) and value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
    return datetime.now(UTC)


def job_from_upwork(detail: Mapping[str, Any], row: Mapping[str, Any] | None = None) -> MarketJob:
    """A job from find_jobs action=get, filled in from its search row where get is silent."""
    row = row or {}
    posting = _find(detail, "marketplaceJobPosting") or {}
    content = posting.get("content") or {}
    classification = posting.get("classification") or {}
    terms = posting.get("contractTerms") or {}
    client = posting.get("clientCompanyPublic") or {}
    job_id = str(posting.get("id") or row.get("id") or "")
    if not job_id:
        raise MarketplaceRejected("Upwork returned a job without an id.")
    duration = str(row.get("duration") or "").strip().lower()
    questions = _find(detail, "screening_questions") or _find(detail, "questions") or []
    return MarketJob(
        job_id=job_id,
        client_id=str(client.get("id") or f"client-of-{job_id}"),
        title=str(content.get("title") or row.get("title") or ""),
        description=str(content.get("description") or row.get("description_snippet") or ""),
        category=str((classification.get("category") or {}).get("preferredLabel") or "Other"),
        required_skills=[str(skill) for skill in row.get("skills", []) if skill],
        budget=_budget(terms, row.get("budget")),
        duration_days=_DURATION_DAYS.get(duration, _DEFAULT_DURATION_DAYS),
        screening_questions=[
            str(item.get("question") if isinstance(item, dict) else item) for item in questions
        ],
        connects_cost=int(_number(detail.get("connects_cost")) or 0),
        posted_at=_when(row.get("published_date"), row.get("created_date")),
    )


def client_from_upwork(
    detail: Mapping[str, Any], row: Mapping[str, Any] | None, client_id: str
) -> ClientHistory:
    record = detail.get("client_record") or {}
    search_client = (row or {}).get("client") or {}
    posting = _find(detail, "marketplaceJobPosting") or {}
    country = ((posting.get("clientCompanyPublic") or {}).get("country") or {}).get("name")
    return ClientHistory(
        client_id=client_id,
        display_name=f"Upwork client{f' in {country}' if country else ''}",
        industry=str(search_client.get("industry") or "Not stated"),
        completed_projects=int(_number(record.get("contracts_total")) or 0),
        average_rating=float(_number(record.get("feedback_score")) or 0.0),
        payment_verified=str(search_client.get("verification_status", "")).upper() == "VERIFIED",
        total_spent_usd=float(_number(search_client.get("total_spent")) or 0.0),
    )


# ---- Proposals GigBid has prepared ---------------------------------------------------------


class UpworkDraftStore:
    """Upwork has no unsent drafts, so GigBid keeps its own until a preview is confirmed."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._transaction() as db:
            db.execute(
                "create table if not exists upwork_proposals ("
                "proposal_id text primary key, user_id text not null, body text not null)"
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._db:
            yield self._db

    def save(self, user_id: str, proposal: Proposal, extra: Mapping[str, Any]) -> None:
        body = json.dumps({"proposal": proposal.model_dump(mode="json"), **extra})
        with self._transaction() as db:
            db.execute(
                "insert into upwork_proposals values (?, ?, ?) on conflict(proposal_id) "
                "do update set body=excluded.body",
                (proposal.proposal_id, user_id, body),
            )

    def load(self, user_id: str, proposal_id: str) -> tuple[Proposal, dict[str, Any]]:
        with self._transaction() as db:
            row = db.execute(
                "select body from upwork_proposals where proposal_id=? and user_id=?",
                (proposal_id, user_id),
            ).fetchone()
        if row is None:
            raise MarketplaceNotFound(f"No proposal '{proposal_id}'.")
        body: dict[str, Any] = json.loads(row[0])
        return Proposal.model_validate(body.pop("proposal")), body

    def close(self) -> None:
        self._db.close()


# ---- The adapter ---------------------------------------------------------------------------


class UpworkLiveAdapter:
    submission_mode = SubmissionMode.PLATFORM_CONFIRMATION

    def __init__(
        self,
        tools: UpworkTools,
        user_id: str,
        drafts: UpworkDraftStore,
        limiter: PerUserRateLimiter,
    ) -> None:
        self._tools = tools
        self._user = user_id
        self._drafts = drafts
        self._limiter = limiter
        self._org_uid: str | None = None
        self._rows: dict[str, dict[str, Any]] = {}  # search rows, which get does not repeat
        self._details: dict[str, dict[str, Any]] = {}  # latest get per job
        self._client_jobs: dict[str, str] = {}
        self._balance: int | None = None

    async def aclose(self) -> None:
        return None

    async def _call(self, tool: str, action: str | None, params: dict[str, Any]) -> dict[str, Any]:
        await self._limiter.acquire(self._user)
        arguments: dict[str, Any] = {"org_uid": await self._org(), "params": params}
        if action:
            arguments["action"] = action
        return await self._tools.call(tool, arguments)

    async def _org(self) -> str:
        if self._org_uid is None:
            await self._limiter.acquire(self._user)
            accounts = (await self._tools.call("list_accounts", {})).get("accounts", [])
            freelancer = next(
                (item for item in accounts if str(item.get("role", "")).upper() == "TALENT"),
                None,
            )
            if freelancer is None or not freelancer.get("org_uid"):
                raise MarketplaceRejected("This Upwork sign-in has no freelancer account.")
            self._org_uid = str(freelancer["org_uid"])
        return self._org_uid

    # Reads --------------------------------------------------------------------------------

    async def search_jobs(
        self,
        query: str | None,
        skills: list[str],
        limit: int,
        cursor: str | None,
    ) -> JobPage:
        params: dict[str, Any] = {"limit": max(1, min(limit, _SEARCH_PAGE_LIMIT))}
        text = query or " ".join(skills)
        if text:
            params["query"] = text
        if cursor:
            params["cursor"] = cursor
        page = await self._call("find_jobs", "search", params)
        jobs = []
        for row in page.get("jobs", []):
            if row.get("applied"):
                continue  # never bid twice on one job
            self._rows[str(row.get("id"))] = row
            jobs.append(await self.get_job(str(row.get("id"))))
        info = page.get("pageInfo") or {}
        return JobPage(
            items=jobs,
            next_cursor=info.get("endCursor") if info.get("hasNextPage") else None,
        )

    async def get_job(self, job_id: str) -> MarketJob:
        detail = await self._call("find_jobs", "get", {"id": job_id})
        job = job_from_upwork(detail, self._rows.get(job_id))
        self._details[job.job_id] = detail
        self._client_jobs[job.client_id] = job.job_id
        balance = _number(detail.get("connects_balance"))
        if balance is not None:
            self._balance = int(balance)
        return job

    async def get_client_history(self, client_id: str) -> ClientHistory:
        job_id = self._client_jobs.get(client_id)
        if job_id is None:
            raise MarketplaceNotFound(f"No Upwork job seen yet for client '{client_id}'.")
        return client_from_upwork(self._details[job_id], self._rows.get(job_id), client_id)

    async def get_connects_balance(self) -> ConnectsBalance:
        if self._balance is None:
            dashboard = await self._call("get_freelancer_dashboard", None, {})
            found = _number(_find(dashboard, "connects_balance") or _find(dashboard, "connects"))
            if found is None:
                raise MarketplaceUnavailable("Upwork did not report a Connects balance.")
            self._balance = int(found)
        return ConnectsBalance(freelancer_id=self._user, balance=self._balance, spent=0)

    async def get_price_stats(self, category: str, skills: list[str]) -> PriceStats:
        raise MarketplaceNotFound("Upwork publishes no price statistics.")

    async def get_proposal(self, proposal_id: str) -> Proposal:
        return self._drafts.load(self._user, proposal_id)[0]

    async def list_conversations(self) -> list[Conversation]:
        listed = await self._call("get_messages", "list_rooms", {"limit": 20})
        rooms = listed.get("rooms") or listed.get("items") or []
        return [
            Conversation(
                conversation_id=str(room.get("id") or room.get("room_id")),
                proposal_id=str(room.get("proposal_id") or ""),
                job_id=str(room.get("job_id") or ""),
                client_id=str(room.get("client_id") or ""),
                freelancer_id=self._user,
            )
            for room in rooms
            if room.get("id") or room.get("room_id")
        ]

    async def list_messages(self, conversation_id: str) -> list[Message]:
        listed = await self._call(
            "get_messages", "list_messages", {"room_id": conversation_id, "limit": 50}
        )
        stories = listed.get("messages") or listed.get("stories") or listed.get("items") or []
        messages = [
            Message(
                message_id=str(item.get("story_id") or item.get("id")),
                conversation_id=conversation_id,
                sender="freelancer" if item.get("from_self") else "client",
                body=str(item.get("message") or item.get("text") or ""),
                sent_at=_when(item.get("created"), item.get("sent_at"), item.get("created_at")),
            )
            for item in stories
        ]
        return sorted(messages, key=lambda message: message.sent_at)  # Upwork lists newest first

    async def list_events(self, after: int, limit: int) -> list[MarketEvent]:
        return []

    # Writes -------------------------------------------------------------------------------

    async def create_proposal_draft(
        self,
        draft: ProposalDraftRequest,
        idempotency_key: str,
    ) -> Proposal:
        """Kept by GigBid only. Upwork hears nothing until submit_proposal prepares a preview."""
        digest = hashlib.sha256(f"{self._user}.{idempotency_key}".encode()).hexdigest()[:20]
        proposal = Proposal(
            proposal_id=f"upw-{digest}",
            job_id=draft.job_id,
            freelancer_id=self._user,
            cover_letter=draft.cover_letter,
            screening_answers=draft.screening_answers,
            bid_amount=draft.bid_amount,
            status="drafted",
            created_at=datetime.now(UTC),
            requires_platform_confirmation=True,
        )
        self._drafts.save(self._user, proposal, {})
        return proposal

    async def submit_proposal(self, proposal_id: str, idempotency_key: str) -> Proposal:
        """Ask Upwork for a preview. Nothing is sent; confirm_submission sends it."""
        proposal, extra = self._drafts.load(self._user, proposal_id)
        if extra.get("preview_id"):
            return proposal  # already prepared; a retry must not ask for a second preview
        if proposal.status != "drafted":
            raise MarketplaceConflict(f"Proposal is already '{proposal.status}'.")
        params: dict[str, Any] = {
            "job_reference": proposal.job_id,
            "cover_letter": proposal.cover_letter,
            "charged_amount": proposal.bid_amount,
        }
        detail = self._details.get(proposal.job_id)
        questions = job_from_upwork(detail).screening_questions if detail else []
        if questions and len(questions) == len(proposal.screening_answers):
            params["answers"] = [
                {"question": question, "answer": answer}
                for question, answer in zip(questions, proposal.screening_answers, strict=True)
            ]
        preview = await self._call("manage_proposals", "create", params)
        preview_id = _find(preview, "preview_id")
        if not preview_id:
            raise MarketplaceRejected("Upwork returned no preview to confirm.")
        notes = []
        if _find(preview, "can_apply") is False:
            notes.append("Upwork says this account cannot apply to the job.")
        unmet = _find(preview, "unmet_preferred_qualifications")
        if unmet:
            notes.append(f"Client preferences you do not meet: {json.dumps(unmet)[:300]}")
        if _find(preview, "screening_questions") and "answers" not in params:
            notes.append("Upwork lists screening questions that this draft has not answered.")
        prepared = proposal.model_copy(
            update={
                "status": "awaiting_platform_confirmation",
                "connects_cost": _int_or_none(_find(preview, "connects_cost")),
                "connects_balance": _int_or_none(_find(preview, "connects_balance")),
                "platform_notes": notes,
            }
        )
        self._drafts.save(self._user, prepared, {"preview_id": str(preview_id)})
        return prepared

    async def confirm_submission(self, proposal_id: str, idempotency_key: str) -> Proposal:
        """Send the prepared proposal. Only ever called after a second human approval."""
        proposal, extra = self._drafts.load(self._user, proposal_id)
        if proposal.status == "submitted":
            return proposal
        preview_id = extra.get("preview_id")
        if proposal.status != "awaiting_platform_confirmation" or not preview_id:
            raise MarketplaceConflict("Prepare the proposal on Upwork before confirming it.")
        await self._call(
            "confirm_preview", "confirm", {"type": "proposal", "preview_id": preview_id}
        )
        sent = proposal.model_copy(
            update={
                "status": "submitted",
                "submitted_at": datetime.now(UTC),
                "connects_spent": proposal.connects_cost or 0,
            }
        )
        self._drafts.save(self._user, sent, extra)
        if self._balance is not None and proposal.connects_cost:
            self._balance -= proposal.connects_cost
        return sent

    async def send_message(
        self,
        conversation_id: str,
        body: str,
        idempotency_key: str,
    ) -> Message:
        """Upwork sends this immediately: GigBid's message approval is the only gate."""
        sent = await self._call(
            "send_message", "send", {"room_id": conversation_id, "message": body}
        )
        return Message(
            message_id=str(_find(sent, "story_id") or _find(sent, "id") or idempotency_key),
            conversation_id=conversation_id,
            sender="freelancer",
            body=body,
            sent_at=datetime.now(UTC),
        )


def _int_or_none(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def upwork_port_factory(
    tools_for: Callable[[str], UpworkTools],
    drafts: UpworkDraftStore,
    config: MarketplaceConfig,
) -> Callable[[str], UpworkLiveAdapter]:
    """One adapter per GigBid user, each with that user's own Upwork token."""
    limiter = PerUserRateLimiter(config.max_requests_per_minute, config.max_rate_limit_wait_seconds)
    ports: dict[str, UpworkLiveAdapter] = {}

    def port_for(user_id: str) -> UpworkLiveAdapter:
        if user_id not in ports:
            ports[user_id] = UpworkLiveAdapter(tools_for(user_id), user_id, drafts, limiter)
        return ports[user_id]

    return port_for
