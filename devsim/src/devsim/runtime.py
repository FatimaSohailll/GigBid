"""Simulated time, rate limiting and fault injection."""

import random
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime, timedelta

from devsim.config import FaultConfig

Monotonic = Callable[[], float]


class SimClock:
    """Marketplace time: real elapsed time plus any manual advances."""

    def __init__(self, start: datetime, monotonic: Monotonic) -> None:
        self._start = start
        self._monotonic = monotonic
        self.reset()

    def reset(self) -> None:
        self._origin = self._monotonic()
        self._advanced = timedelta()

    def now(self) -> datetime:
        elapsed = timedelta(seconds=self._monotonic() - self._origin)
        return self._start + elapsed + self._advanced

    def advance(self, minutes: float) -> None:
        self._advanced += timedelta(minutes=minutes)


class SlidingWindowRateLimiter:
    """Per-user limit over a rolling 60-second window. Uses real time, not simulated time."""

    WINDOW_SECONDS = 60.0

    def __init__(self, requests_per_minute: int, monotonic: Monotonic) -> None:
        self._limit = requests_per_minute
        self._monotonic = monotonic
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)

    def check(self, user: str) -> float | None:
        """Record a request. Returns None if allowed, else the seconds to wait."""
        now = self._monotonic()
        hits = self._hits[user]
        while hits and now - hits[0] >= self.WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= self._limit:
            return self.WINDOW_SECONDS - (now - hits[0])
        hits.append(now)
        return None

    def reset(self) -> None:
        self._hits.clear()


class FaultInjector:
    """Seeded failures, so a run with the same request order fails the same way."""

    def __init__(self, config: FaultConfig) -> None:
        self._config = config
        self.retry_after_seconds = config.retry_after_seconds
        self.reset()

    def reset(self) -> None:
        self.error_rate = self._config.error_rate
        self.post_commit_error_rate = self._config.post_commit_error_rate
        self._random = random.Random(self._config.seed)

    def should_fail_before(self) -> bool:
        return self._random.random() < self.error_rate

    def should_fail_after_commit(self) -> bool:
        return self._random.random() < self.post_commit_error_rate
