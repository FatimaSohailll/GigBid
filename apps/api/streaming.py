"""Server-Sent Events bridge from graph runs to the browser.

The worker writes node updates to a Redis stream. Each SSE message carries that stream's id, so a
browser that reconnects with Last-Event-ID continues where it stopped and misses nothing.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

from redis.asyncio import Redis

_MAX_EVENTS = 500
_RETENTION_SECONDS = 86_400
_BLOCK_MILLISECONDS = 5_000


def stream_key(thread_id: str) -> str:
    return f"gigbid:run-events:{thread_id}"


async def publish_event(redis: Redis, thread_id: str, event: dict[str, Any]) -> None:
    key = stream_key(thread_id)
    await redis.xadd(key, {"data": json.dumps(event, default=str)}, maxlen=_MAX_EVENTS)
    await redis.expire(key, _RETENTION_SECONDS)


async def event_stream(
    redis: Redis,
    thread_id: str,
    last_event_id: str | None,
) -> AsyncIterator[str]:
    """Yield SSE text until the run ends. A comment keeps idle connections alive."""
    key = stream_key(thread_id)
    position = last_event_id or "0"
    while True:
        batches = await redis.xread({key: position}, block=_BLOCK_MILLISECONDS, count=50)
        if not batches:
            yield ": keep-alive\n\n"
            continue
        for _, entries in batches:
            for entry_id, fields in entries:
                position = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                raw = fields.get(b"data") or fields.get("data")
                data = raw.decode() if isinstance(raw, bytes) else raw
                yield f"id: {position}\ndata: {data}\n\n"
                if json.loads(data).get("type") == "run_end":
                    return
