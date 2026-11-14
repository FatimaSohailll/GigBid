"""Workspace MCP server: calendar slots, tentative interview holds and email drafts.

This is the local backend the assignment permits (A3). Nothing leaves the machine, and email is
only ever stored as a draft. Agents see MCP tools, so Google APIs could replace it later.
"""

import os
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, AwareDatetime, BaseModel, ConfigDict, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from agent.config import WorkspaceConfig, get_config
from agent.logging_setup import install_log_masking
from mcp_servers.tenant_auth import TenantBearerVerifier, bearer_tenant, parse_tenants
from mcp_servers.tool_descriptions import TOOL_DESCRIPTIONS

TenantResolver = Callable[[Context[Any, Any, Any]], str]
READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
LOCAL_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
IdempotencyKey = Annotated[str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
_SCHEMA = """
create table if not exists holds (
    tenant text not null, key text not null, start text not null, end text not null,
    title text not null, primary key (tenant, key)
);
create table if not exists email_drafts (
    tenant text not null, key text not null, subject text not null, body text not null,
    recipient text, primary key (tenant, key)
);
"""


class Slot(BaseModel):
    start: datetime
    end: datetime


class SlotList(BaseModel):
    items: list[Slot]


class CreateHoldInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: AwareDatetime
    duration_minutes: int = Field(ge=15, le=240)
    title: str = Field(min_length=1, max_length=200)
    idempotency_key: IdempotencyKey


class Hold(BaseModel):
    start: datetime
    end: datetime
    title: str
    status: str = "tentative"
    replayed: bool = False


class CreateEmailDraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=8000)
    recipient: str | None = Field(default=None, max_length=254)
    idempotency_key: IdempotencyKey


class EmailDraft(BaseModel):
    subject: str
    status: str = "draft"
    replayed: bool = False


@dataclass
class WorkspaceStore:
    path: Path | str

    def __post_init__(self) -> None:
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.db.executescript(_SCHEMA)

    def holds(self, tenant: str) -> list[tuple[datetime, datetime]]:
        rows = self.db.execute("select start, end from holds where tenant=?", (tenant,))
        return [(datetime.fromisoformat(a), datetime.fromisoformat(b)) for a, b in rows]


def available_slots(
    now: datetime,
    busy: list[tuple[datetime, datetime]],
    config: WorkspaceConfig,
    duration: timedelta,
    count: int,
    days_ahead: int,
) -> list[Slot]:
    """Weekday slots inside working hours that do not overlap an existing hold."""
    slots: list[Slot] = []
    for offset in range(days_ahead + 1):
        day = (now + timedelta(days=offset)).replace(minute=0, second=0, microsecond=0)
        if day.weekday() >= 5:
            continue
        start = day.replace(hour=config.work_start_hour)
        closing = day.replace(hour=config.work_end_hour)
        while start + duration <= closing and len(slots) < count:
            end = start + duration
            if start > now and not any(start < until and end > since for since, until in busy):
                slots.append(Slot(start=start, end=end))
            start = end
    return slots


def build_server(
    store: WorkspaceStore,
    tenant_of: TenantResolver = bearer_tenant,
    config: WorkspaceConfig | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    **fastmcp_options: Any,
) -> FastMCP:
    settings = config or get_config().workspace
    mcp = FastMCP(
        "gigbid-workspace",
        instructions="Calendar availability and drafts. Nothing here is sent to anyone.",
        stateless_http=True,
        json_response=True,
        **fastmcp_options,
    )

    @mcp.tool(annotations=READ_ONLY, description=TOOL_DESCRIPTIONS["free_slots"])
    async def free_slots(
        ctx: Context[Any, Any, Any],
        count: Annotated[int, Field(ge=1, le=10)] = settings.slots_offered,
        duration_minutes: Annotated[int, Field(ge=15, le=240)] = settings.interview_minutes,
        days_ahead: Annotated[int, Field(ge=1, le=30)] = settings.days_ahead,
    ) -> SlotList:
        """List free calendar slots within working hours, soonest first."""
        items = available_slots(
            clock(),
            store.holds(tenant_of(ctx)),
            settings,
            timedelta(minutes=duration_minutes),
            count,
            days_ahead,
        )
        return SlotList(items=items)

    @mcp.tool(annotations=LOCAL_WRITE, description=TOOL_DESCRIPTIONS["create_interview_hold"])
    async def create_interview_hold(
        request: CreateHoldInput,
        ctx: Context[Any, Any, Any],
    ) -> Hold:
        """Place a tentative interview hold on the calendar. Only after human approval."""
        tenant = tenant_of(ctx)
        end = request.start + timedelta(minutes=request.duration_minutes)
        previous = store.db.execute(
            "select start, end, title from holds where tenant=? and key=?",
            (tenant, request.idempotency_key),
        ).fetchone()
        if previous:
            return Hold(
                start=datetime.fromisoformat(previous[0]),
                end=datetime.fromisoformat(previous[1]),
                title=previous[2],
                replayed=True,
            )
        if any(request.start < until and end > since for since, until in store.holds(tenant)):
            raise ToolError("conflict: this time overlaps an existing hold")
        store.db.execute(
            "insert into holds values (?, ?, ?, ?, ?)",
            (
                tenant,
                request.idempotency_key,
                request.start.isoformat(),
                end.isoformat(),
                request.title,
            ),
        )
        return Hold(start=request.start, end=end, title=request.title)

    @mcp.tool(annotations=LOCAL_WRITE, description=TOOL_DESCRIPTIONS["create_email_draft"])
    async def create_email_draft(
        request: CreateEmailDraftInput,
        ctx: Context[Any, Any, Any],
    ) -> EmailDraft:
        """Save an email as a draft. It is never sent automatically."""
        cursor = store.db.execute(
            "insert or ignore into email_drafts values (?, ?, ?, ?, ?)",
            (
                tenant_of(ctx),
                request.idempotency_key,
                request.subject,
                request.body,
                request.recipient,
            ),
        )
        return EmailDraft(subject=request.subject, replayed=cursor.rowcount == 0)

    @mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "gigbid-workspace-mcp"})

    return mcp


def create_http_server(environ: Mapping[str, str] | None = None) -> FastMCP:
    install_log_masking()
    env = os.environ if environ is None else environ
    tenants = parse_tenants(env.get("MCP_WORKSPACE_USERS", ""))
    host = env.get("MCP_WORKSPACE_HOST", "127.0.0.1")
    port = int(env.get("MCP_WORKSPACE_PORT", "8092"))
    url = AnyHttpUrl(env.get("MCP_WORKSPACE_PUBLIC_URL", f"http://{host}:{port}"))
    return build_server(
        WorkspaceStore(env.get("MCP_WORKSPACE_DB", "data/processed/workspace_mcp.sqlite3")),
        token_verifier=TenantBearerVerifier(tenants, env.get("MCP_TENANT_SIGNING_KEY")),
        auth=AuthSettings(issuer_url=url, resource_server_url=url, validate_token_resource=False),
        host=host,
        port=port,
    )


if __name__ == "__main__":
    create_http_server().run(transport="streamable-http")
