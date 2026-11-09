"""Durable server state: the append-only audit log and the idempotency ledger."""

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_PHONE = re.compile(r"(?<![\w(])(?!\d{4}-\d{2}-\d{2})\+?\(?\d[\d ()-]{7,}\d(?!\w)")

_SCHEMA = """
create table if not exists audit_log (
    id integer primary key autoincrement,
    at text not null,
    user_id text not null,
    tool text not null,
    arguments text not null,
    outcome text not null,
    result text not null,
    trace_id text not null
);
create trigger if not exists audit_no_update before update on audit_log
begin select raise(abort, 'audit_log is append-only'); end;
create trigger if not exists audit_no_delete before delete on audit_log
begin select raise(abort, 'audit_log is append-only'); end;
create table if not exists idempotency (
    user_id text not null,
    tool text not null,
    key text not null,
    fingerprint text not null,
    response text,
    primary key (user_id, tool, key)
);
"""


class IdempotencyKeyReused(Exception):
    pass


def mask_pii(value: Any) -> Any:
    """Mask emails, card numbers and phone numbers in any JSON-like value."""
    if isinstance(value, str):
        masked = _EMAIL.sub("[email]", value)
        masked = _CARD.sub("[payment]", masked)
        return _PHONE.sub("[phone]", masked)
    if isinstance(value, list):
        return [mask_pii(item) for item in value]
    if isinstance(value, dict):
        return {key: mask_pii(item) for key, item in value.items()}
    return value


def fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ServerStore:
    """SQLite calls are short and made from one event loop, so one connection is enough."""

    def __init__(self, path: Path | str) -> None:
        # The server's event loop may run in a different thread from the one that built it.
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def audit(
        self,
        user_id: str,
        tool: str,
        arguments: dict[str, Any],
        outcome: str,
        result: Any,
        trace_id: str,
    ) -> None:
        self._db.execute(
            "insert into audit_log (at, user_id, tool, arguments, outcome, result, trace_id) "
            "values (?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(UTC).isoformat(),
                user_id,
                tool,
                json.dumps(mask_pii(arguments), sort_keys=True, default=str),
                outcome,
                json.dumps(mask_pii(result), sort_keys=True, default=str),
                trace_id,
            ),
        )

    def audit_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._db.execute("select * from audit_log order by id")]

    def begin_write(
        self,
        user_id: str,
        tool: str,
        key: str,
        payload_fingerprint: str,
    ) -> dict[str, Any] | None:
        """Claim a key. Returns the stored response if this write already completed.

        A claim left without a response means an earlier attempt failed or crashed, so the
        write is forwarded again with the same key and the marketplace deduplicates it.
        """
        self._db.execute(
            "insert or ignore into idempotency (user_id, tool, key, fingerprint) "
            "values (?, ?, ?, ?)",
            (user_id, tool, key, payload_fingerprint),
        )
        row = self._db.execute(
            "select fingerprint, response from idempotency where user_id=? and tool=? and key=?",
            (user_id, tool, key),
        ).fetchone()
        if row["fingerprint"] != payload_fingerprint:
            raise IdempotencyKeyReused
        stored: dict[str, Any] | None = json.loads(row["response"]) if row["response"] else None
        return stored

    def finish_write(self, user_id: str, tool: str, key: str, response: dict[str, Any]) -> None:
        self._db.execute(
            "update idempotency set response=? where user_id=? and tool=? and key=?",
            (json.dumps(response, sort_keys=True, default=str), user_id, tool, key),
        )
