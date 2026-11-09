"""DevSim REST API."""

import hmac
import math
import time
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from devsim import SIMULATOR_NAME, __version__
from devsim.config import Settings
from devsim.models import (
    ClientHistory,
    ClockAdvanceIn,
    ConnectsBalance,
    Conversation,
    Event,
    FaultRatesIn,
    Health,
    Job,
    JobPage,
    Message,
    MessageIn,
    PriceStats,
    Proposal,
    ProposalDraftIn,
    StatusChangeIn,
)
from devsim.runtime import FaultInjector, Monotonic, SimClock, SlidingWindowRateLimiter
from devsim.seed_loader import load_seed
from devsim.store import ApiError, MarketplaceStore

IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=128),
]


def _error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


def _bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def create_app(settings: Settings, monotonic: Monotonic = time.monotonic) -> FastAPI:
    config = settings.config
    seed = load_seed(config.seed_dir)
    clock = SimClock(config.start_time, monotonic)
    store = MarketplaceStore(seed, clock, config.limits)
    limiter = SlidingWindowRateLimiter(config.rate_limit.requests_per_minute, monotonic)
    faults = FaultInjector(config.faults)

    unknown = sorted(
        set(settings.api_tokens.values()) - {f.freelancer_id for f in seed.freelancers}
    )
    if unknown:
        raise ValueError(f"API tokens reference unknown freelancers: {unknown}")

    app = FastAPI(
        title="GigBid DevSim (not the official GigSim)",
        version=__version__,
        description="Development stand-in for the instructor-provided GigSim marketplace.",
    )

    @app.middleware("http")
    async def mark_simulator(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers["X-Simulator"] = SIMULATOR_NAME
        return response

    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {key: value for key, value in error.items() if key in {"type", "loc", "msg"}}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_error_body(
                "validation_error",
                "The request is not valid.",
                jsonable_encoder(details),
            ),
        )

    def service_unavailable() -> ApiError:
        return ApiError(
            503,
            "service_unavailable",
            "The marketplace is temporarily unavailable.",
            {"Retry-After": str(faults.retry_after_seconds)},
        )

    async def current_freelancer(
        authorization: Annotated[str | None, Header()] = None,
    ) -> str:
        """Authenticate, apply the rate limit, then possibly inject a pre-commit failure."""
        token = _bearer_token(authorization)
        freelancer_id = None
        if token is not None:
            for known_token, owner in settings.api_tokens.items():
                if hmac.compare_digest(token.encode(), known_token.encode()):
                    freelancer_id = owner
        if freelancer_id is None:
            raise ApiError(
                401,
                "unauthorized",
                "A valid bearer token is required.",
                {"WWW-Authenticate": "Bearer"},
            )
        wait = limiter.check(freelancer_id)
        if wait is not None:
            raise ApiError(
                429,
                "rate_limited",
                "Too many requests. Slow down and retry later.",
                {"Retry-After": str(math.ceil(wait))},
            )
        if faults.should_fail_before():
            raise service_unavailable()
        return freelancer_id

    async def require_admin(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        token = _bearer_token(authorization)
        allowed = (
            settings.admin_token is not None
            and token is not None
            and hmac.compare_digest(token.encode(), settings.admin_token.encode())
        )
        if not allowed:
            raise ApiError(403, "forbidden", "A valid admin token is required.")

    Freelancer = Annotated[str, Depends(current_freelancer)]
    Admin = Depends(require_admin)

    def finish_write(response: Response, result: dict[str, Any], replayed: bool) -> dict[str, Any]:
        """Mark replays. May fail after commit, so clients must retry with the same key."""
        response.headers["Idempotent-Replay"] = "true" if replayed else "false"
        if not replayed and faults.should_fail_after_commit():
            raise service_unavailable()
        return result

    @app.get("/health", response_model=Health, tags=["system"])
    async def health() -> Health:
        return Health(
            status="ok",
            simulator=SIMULATOR_NAME,
            official_gigsim=False,
            version=__version__,
            sim_time=clock.now(),
        )

    # ---- Reads -------------------------------------------------------------------------

    @app.get("/v1/jobs", response_model=JobPage, tags=["jobs"])
    async def search_jobs(
        _: Freelancer,
        query: str | None = None,
        skills: Annotated[list[str] | None, Query()] = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> JobPage:
        return store.search_jobs(query, skills or [], limit, cursor)

    @app.get("/v1/jobs/{job_id}", response_model=Job, tags=["jobs"])
    async def get_job(job_id: str, _: Freelancer) -> Job:
        return store.get_job(job_id)

    @app.get("/v1/clients/{client_id}/history", response_model=ClientHistory, tags=["clients"])
    async def get_client_history(client_id: str, _: Freelancer) -> ClientHistory:
        return store.get_client_history(client_id)

    @app.get("/v1/connects/balance", response_model=ConnectsBalance, tags=["connects"])
    async def get_connects_balance(freelancer: Freelancer) -> ConnectsBalance:
        return store.get_balance(freelancer)

    @app.get("/v1/proposals", response_model=list[Proposal], tags=["proposals"])
    async def list_proposals(freelancer: Freelancer) -> list[Proposal]:
        return store.list_proposals(freelancer)

    @app.get("/v1/proposals/{proposal_id}", response_model=Proposal, tags=["proposals"])
    async def get_proposal(proposal_id: str, freelancer: Freelancer) -> Proposal:
        return store.get_proposal(freelancer, proposal_id)

    @app.get("/v1/conversations", response_model=list[Conversation], tags=["messages"])
    async def list_conversations(freelancer: Freelancer) -> list[Conversation]:
        return store.list_conversations(freelancer)

    @app.get(
        "/v1/conversations/{conversation_id}/messages",
        response_model=list[Message],
        tags=["messages"],
    )
    async def list_messages(conversation_id: str, freelancer: Freelancer) -> list[Message]:
        return store.list_messages(freelancer, conversation_id)

    @app.get("/v1/events", response_model=list[Event], tags=["events"])
    async def list_events(freelancer: Freelancer, after: int = 0, limit: int = 50) -> list[Event]:
        return store.list_events(freelancer, after, limit)

    @app.get("/v1/market/price-stats", response_model=PriceStats, tags=["market"])
    async def get_price_stats(
        _: Freelancer,
        category: str,
        skills: Annotated[list[str] | None, Query()] = None,
    ) -> PriceStats:
        return store.price_stats(category, skills or [])

    # ---- Writes ------------------------------------------------------------------------

    @app.post(
        "/v1/proposals/drafts",
        response_model=Proposal,
        status_code=201,
        tags=["proposals"],
    )
    async def create_proposal_draft(
        draft: ProposalDraftIn,
        response: Response,
        freelancer: Freelancer,
        key: IdempotencyKey,
    ) -> dict[str, Any]:
        result, replayed = store.once(
            freelancer,
            "create_draft",
            key,
            draft.model_dump(),
            lambda: store.create_draft(freelancer, draft),
        )
        return finish_write(response, result, replayed)

    @app.post("/v1/proposals/{proposal_id}/submit", response_model=Proposal, tags=["proposals"])
    async def submit_proposal(
        proposal_id: str,
        response: Response,
        freelancer: Freelancer,
        key: IdempotencyKey,
    ) -> dict[str, Any]:
        result, replayed = store.once(
            freelancer,
            "submit_proposal",
            key,
            {"proposal_id": proposal_id},
            lambda: store.submit_proposal(freelancer, proposal_id),
        )
        return finish_write(response, result, replayed)

    @app.post(
        "/v1/conversations/{conversation_id}/messages",
        response_model=Message,
        status_code=201,
        tags=["messages"],
    )
    async def send_message(
        conversation_id: str,
        message: MessageIn,
        response: Response,
        freelancer: Freelancer,
        key: IdempotencyKey,
    ) -> dict[str, Any]:
        result, replayed = store.once(
            freelancer,
            "send_message",
            key,
            {"conversation_id": conversation_id, "body": message.body},
            lambda: store.send_message(freelancer, conversation_id, message.body),
        )
        return finish_write(response, result, replayed)

    # ---- Simulator controls ------------------------------------------------------------

    @app.post("/admin/reset", status_code=204, dependencies=[Admin], tags=["admin"])
    async def reset() -> Response:
        store.reset()
        clock.reset()
        limiter.reset()
        faults.reset()
        return Response(status_code=204)

    @app.post("/admin/clock/advance", response_model=Health, dependencies=[Admin], tags=["admin"])
    async def advance_clock(body: ClockAdvanceIn) -> Health:
        clock.advance(body.minutes)
        return await health()

    @app.put("/admin/faults", status_code=204, dependencies=[Admin], tags=["admin"])
    async def set_faults(body: FaultRatesIn) -> Response:
        faults.error_rate = body.error_rate
        faults.post_commit_error_rate = body.post_commit_error_rate
        return Response(status_code=204)

    @app.post(
        "/admin/proposals/{proposal_id}/status",
        response_model=Proposal,
        dependencies=[Admin],
        tags=["admin"],
    )
    async def change_status(proposal_id: str, body: StatusChangeIn) -> Proposal:
        return store.change_status(proposal_id, body.status)

    @app.post(
        "/admin/proposals/{proposal_id}/client-message",
        response_model=Message,
        status_code=201,
        dependencies=[Admin],
        tags=["admin"],
    )
    async def client_message(proposal_id: str, body: MessageIn) -> Message:
        return store.receive_client_message(proposal_id, body.body)

    return app
