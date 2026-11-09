"""Application data in Postgres. Every query about a user's data is scoped by user_id."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

_SCHEMA = """
create table if not exists users (
    id text primary key,
    email text unique not null,
    password_hash text not null,
    persona text not null,
    floor_usd double precision not null default 200,
    weekly_connects_budget integer not null default 60,
    autonomy text not null default 'manual',
    marketplace text not null default 'gigsim',
    created_at timestamptz not null default now()
);
create table if not exists sessions (
    token_hash text primary key,
    user_id text not null references users(id) on delete cascade,
    expires_at timestamptz not null
);
create table if not exists oauth_tokens (
    user_id text not null references users(id) on delete cascade,
    provider text not null,
    ciphertext bytea not null,
    primary key (user_id, provider)
);
create table if not exists saved_searches (
    id text primary key,
    user_id text not null references users(id) on delete cascade,
    query text,
    skills jsonb not null default '[]',
    interval_minutes integer not null,
    last_run_at timestamptz
);
create table if not exists jobs (
    user_id text not null references users(id) on delete cascade,
    job_id text not null,
    payload jsonb not null,
    status text not null default 'new',
    fit double precision,
    risk double precision,
    action text,
    price double precision,
    risk_flags jsonb not null default '[]',
    created_at timestamptz not null default now(),
    primary key (user_id, job_id)
);
create table if not exists runs (
    thread_id text primary key,
    user_id text not null references users(id) on delete cascade,
    kind text not null,
    ref_id text not null,
    status text not null,
    trace_id text not null,
    tokens integer not null default 0,
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    rejection_reason text,
    attention_reason text
);
create table if not exists pipeline (
    user_id text not null references users(id) on delete cascade,
    proposal_id text not null,
    job_id text not null,
    category text not null,
    title text not null,
    status text not null,
    connects_spent integer not null default 0,
    updated_at timestamptz not null default now(),
    primary key (user_id, proposal_id)
);
create table if not exists sync_cursors (
    user_id text primary key references users(id) on delete cascade,
    last_event_id integer not null default 0
);
create table if not exists uploads (
    id text primary key,
    user_id text not null references users(id) on delete cascade,
    filename text not null,
    status text not null default 'queued',
    created_at timestamptz not null default now()
);
"""


def open_pool(url: str) -> AsyncConnectionPool:
    """A pool the application and LangGraph's Postgres classes can share."""
    return AsyncConnectionPool(
        url.replace("postgresql+psycopg://", "postgresql://", 1),
        open=False,
        max_size=10,
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
    )


class Repository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def setup(self) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(_SCHEMA)

    async def ping(self) -> None:
        await self._all("select 1 as ok")

    async def _all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(sql, params)
            return cast(list[dict[str, Any]], await cursor.fetchall())

    async def _one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = await self._all(sql, params)
        return rows[0] if rows else None

    async def _run(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(sql, params)

    # ---- Users and sessions --------------------------------------------------------------

    async def create_user(self, email: str, password_hash: str, persona: str) -> dict[str, Any]:
        row = await self._one(
            "insert into users (id, email, password_hash, persona) values (%s, %s, %s, %s) "
            "on conflict (email) do nothing returning *",
            (uuid.uuid4().hex, email.lower(), password_hash, persona),
        )
        if row is None:
            raise ValueError("email_taken")
        return row

    async def user_by_email(self, email: str) -> dict[str, Any] | None:
        return await self._one("select * from users where email=%s", (email.lower(),))

    async def user(self, user_id: str) -> dict[str, Any]:
        row = await self._one("select * from users where id=%s", (user_id,))
        if row is None:
            raise KeyError(user_id)
        return row

    async def all_users(self) -> list[dict[str, Any]]:
        return await self._all("select * from users")

    async def update_settings(
        self,
        user_id: str,
        floor_usd: float,
        weekly_connects_budget: int,
        autonomy: str,
    ) -> None:
        await self._run(
            "update users set floor_usd=%s, weekly_connects_budget=%s, autonomy=%s where id=%s",
            (floor_usd, weekly_connects_budget, autonomy, user_id),
        )

    async def create_session(self, user_id: str, token_hash: str, days: int) -> None:
        await self._run(
            "insert into sessions values (%s, %s, %s)",
            (token_hash, user_id, datetime.now(UTC) + timedelta(days=days)),
        )

    async def session_user(self, token_hash: str) -> dict[str, Any] | None:
        return await self._one(
            "select users.* from sessions join users on users.id = sessions.user_id "
            "where token_hash=%s and expires_at > now()",
            (token_hash,),
        )

    async def delete_session(self, token_hash: str) -> None:
        await self._run("delete from sessions where token_hash=%s", (token_hash,))

    async def store_oauth(self, user_id: str, provider: str, ciphertext: bytes) -> None:
        await self._run(
            "insert into oauth_tokens values (%s, %s, %s) on conflict (user_id, provider) "
            "do update set ciphertext=excluded.ciphertext",
            (user_id, provider, ciphertext),
        )

    async def oauth_ciphertext(self, user_id: str, provider: str) -> bytes | None:
        row = await self._one(
            "select ciphertext from oauth_tokens where user_id=%s and provider=%s",
            (user_id, provider),
        )
        return bytes(row["ciphertext"]) if row else None

    # ---- Saved searches ------------------------------------------------------------------

    async def add_search(
        self, user_id: str, query: str | None, skills: list[str], interval_minutes: int
    ) -> dict[str, Any]:
        row = await self._one(
            "insert into saved_searches (id, user_id, query, skills, interval_minutes) "
            "values (%s, %s, %s, %s, %s) returning *",
            (uuid.uuid4().hex, user_id, query, Jsonb(skills), interval_minutes),
        )
        assert row is not None
        return row

    async def searches(self, user_id: str) -> list[dict[str, Any]]:
        return await self._all("select * from saved_searches where user_id=%s", (user_id,))

    async def delete_search(self, user_id: str, search_id: str) -> None:
        await self._run(
            "delete from saved_searches where user_id=%s and id=%s", (user_id, search_id)
        )

    async def due_searches(self) -> list[dict[str, Any]]:
        return await self._all(
            "select * from saved_searches where last_run_at is null or "
            "last_run_at + make_interval(mins => interval_minutes) <= now()"
        )

    async def mark_search_run(self, search_id: str) -> None:
        await self._run("update saved_searches set last_run_at=now() where id=%s", (search_id,))

    # ---- Jobs ----------------------------------------------------------------------------

    async def add_job(self, user_id: str, job: dict[str, Any]) -> bool:
        row = await self._one(
            "insert into jobs (user_id, job_id, payload) values (%s, %s, %s) "
            "on conflict do nothing returning job_id",
            (user_id, job["job_id"], Jsonb(job)),
        )
        return row is not None

    async def job(self, user_id: str, job_id: str) -> dict[str, Any] | None:
        return await self._one(
            "select * from jobs where user_id=%s and job_id=%s", (user_id, job_id)
        )

    async def recent_jobs(self, user_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = await self._all(
            "select payload from jobs where user_id=%s order by created_at desc limit %s",
            (user_id, limit),
        )
        return [row["payload"] for row in rows]

    async def inbox(self, user_id: str, action: str | None) -> list[dict[str, Any]]:
        return await self._all(
            "select * from jobs where user_id=%s and status <> 'dismissed' "
            "and (%s::text is null or action=%s) order by fit desc nulls last, created_at desc",
            (user_id, action, action),
        )

    async def set_job_status(self, user_id: str, job_id: str, status: str) -> None:
        await self._run(
            "update jobs set status=%s where user_id=%s and job_id=%s", (status, user_id, job_id)
        )

    async def record_analysis(
        self,
        user_id: str,
        job_id: str,
        status: str,
        fit: float | None,
        risk: float | None,
        action: str | None,
        price: float | None,
        flags: list[str],
    ) -> None:
        await self._run(
            "update jobs set status=%s, fit=%s, risk=%s, action=%s, price=%s, risk_flags=%s "
            "where user_id=%s and job_id=%s",
            (status, fit, risk, action, price, Jsonb(flags), user_id, job_id),
        )

    # ---- Runs ----------------------------------------------------------------------------

    async def create_run(
        self, thread_id: str, user_id: str, kind: str, ref_id: str, trace_id: str
    ) -> None:
        await self._run(
            "insert into runs (thread_id, user_id, kind, ref_id, status, trace_id) "
            "values (%s, %s, %s, %s, 'queued', %s) on conflict (thread_id) "
            "do update set status='queued', trace_id=excluded.trace_id, finished_at=null "
            "where runs.status not in ('awaiting_approval', 'running', 'resuming')",
            (thread_id, user_id, kind, ref_id, trace_id),
        )

    async def run(self, user_id: str, thread_id: str) -> dict[str, Any] | None:
        return await self._one(
            "select * from runs where user_id=%s and thread_id=%s", (user_id, thread_id)
        )

    async def runs(self, user_id: str, status: str | None = None) -> list[dict[str, Any]]:
        return await self._all(
            "select * from runs where user_id=%s and (%s::text is null or status=%s) "
            "order by started_at desc limit 200",
            (user_id, status, status),
        )

    async def claim_approval(self, user_id: str, thread_id: str) -> bool:
        """Move a waiting thread to resuming. Only the first of two clicks can succeed."""
        row = await self._one(
            "update runs set status='resuming' where user_id=%s and thread_id=%s "
            "and status='awaiting_approval' returning thread_id",
            (user_id, thread_id),
        )
        return row is not None

    async def set_run_status(self, thread_id: str, status: str) -> None:
        await self._run("update runs set status=%s where thread_id=%s", (status, thread_id))

    async def finish_run(
        self,
        thread_id: str,
        status: str,
        tokens: int,
        rejection_reason: str | None,
        attention_reason: str | None,
    ) -> None:
        finished = status != "awaiting_approval"
        await self._run(
            "update runs set status=%s, tokens=%s, rejection_reason=%s, attention_reason=%s, "
            "finished_at=case when %s then now() else null end where thread_id=%s",
            (status, tokens, rejection_reason, attention_reason, finished, thread_id),
        )

    # ---- Pipeline and sync ---------------------------------------------------------------

    async def upsert_card(
        self,
        user_id: str,
        proposal_id: str,
        job_id: str,
        category: str,
        title: str,
        status: str,
        connects_spent: int,
    ) -> None:
        await self._run(
            "insert into pipeline (user_id, proposal_id, job_id, category, title, status, "
            "connects_spent) values (%s, %s, %s, %s, %s, %s, %s) "
            "on conflict (user_id, proposal_id) do update set status=excluded.status, "
            "connects_spent=greatest(pipeline.connects_spent, excluded.connects_spent), "
            "updated_at=now()",
            (user_id, proposal_id, job_id, category, title, status, connects_spent),
        )

    async def set_card_status(self, user_id: str, proposal_id: str, status: str) -> None:
        await self._run(
            "update pipeline set status=%s, updated_at=now() where user_id=%s and proposal_id=%s",
            (status, user_id, proposal_id),
        )

    async def pipeline(self, user_id: str) -> list[dict[str, Any]]:
        return await self._all(
            "select * from pipeline where user_id=%s order by updated_at desc", (user_id,)
        )

    async def connects_spent_since(self, user_id: str, days: int) -> int:
        row = await self._one(
            "select coalesce(sum(connects_spent), 0)::int as spent from pipeline "
            "where user_id=%s and updated_at > now() - make_interval(days => %s)",
            (user_id, days),
        )
        assert row is not None
        return int(row["spent"])

    async def sync_cursor(self, user_id: str) -> int:
        row = await self._one("select last_event_id from sync_cursors where user_id=%s", (user_id,))
        return int(row["last_event_id"]) if row else 0

    async def set_sync_cursor(self, user_id: str, event_id: int) -> None:
        await self._run(
            "insert into sync_cursors values (%s, %s) on conflict (user_id) "
            "do update set last_event_id=excluded.last_event_id",
            (user_id, event_id),
        )

    # ---- Uploads and analytics -----------------------------------------------------------

    async def add_upload(self, user_id: str, upload_id: str, filename: str) -> None:
        await self._run(
            "insert into uploads (id, user_id, filename) values (%s, %s, %s)",
            (upload_id, user_id, filename),
        )

    async def set_upload_status(self, upload_id: str, status: str) -> None:
        await self._run("update uploads set status=%s where id=%s", (status, upload_id))

    async def uploads(self, user_id: str) -> list[dict[str, Any]]:
        return await self._all(
            "select * from uploads where user_id=%s order by created_at desc", (user_id,)
        )

    async def analytics_by_category(self, user_id: str) -> list[dict[str, Any]]:
        return await self._all(
            "select category, count(*)::int as submitted, "
            "count(*) filter (where status in ('replied','interview','hired'))::int as replied, "
            "count(*) filter (where status='hired')::int as hired, "
            "coalesce(sum(connects_spent),0)::int as connects_spent "
            "from pipeline where user_id=%s group by category order by category",
            (user_id,),
        )

    async def analytics_runs(self, user_id: str) -> dict[str, Any]:
        row = await self._one(
            "select count(*)::int as runs, coalesce(avg(tokens),0)::float as mean_tokens, "
            "coalesce(percentile_cont(0.95) within group (order by "
            "extract(epoch from finished_at - started_at)),0)::float as p95_seconds "
            "from runs where user_id=%s and kind='bid' and finished_at is not null",
            (user_id,),
        )
        assert row is not None
        return row

    async def top_rejections(self, user_id: str) -> list[dict[str, Any]]:
        return await self._all(
            "select rejection_reason as reason, count(*)::int as count from runs "
            "where user_id=%s and rejection_reason is not null "
            "group by rejection_reason order by count desc limit 10",
            (user_id,),
        )
