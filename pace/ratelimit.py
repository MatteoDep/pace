"""Async rate limiter -- sliding window with per-minute/hour/day caps."""

import asyncio
import time
from collections import deque


class RateLimiter:
    """Async sliding-window rate limiter.

    Enforces a minimum interval between calls plus rolling caps over
    1-minute, 1-hour, and 1-day windows.
    """

    def __init__(
        self,
        min_interval: float = 2.0,
        per_minute: int = 10,
        per_hour: int = 100,
        per_day: int = 500,
    ) -> None:
        self.min_interval = min_interval
        self.per_minute = per_minute
        self.per_hour = per_hour
        self.per_day = per_day
        self._history: deque[float] = deque()
        self._last_call: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until a call is permitted, then register it."""
        async with self._lock:
            while True:
                now = time.monotonic()

                while self._history and now - self._history[0] > 86400.0:
                    self._history.popleft()

                calls_last_minute = sum(1 for t in self._history if now - t <= 60.0)
                calls_last_hour = sum(1 for t in self._history if now - t <= 3600.0)
                calls_last_day = len(self._history)

                wait = 0.0

                elapsed = now - self._last_call
                if elapsed < self.min_interval:
                    wait = max(wait, self.min_interval - elapsed)

                if calls_last_minute >= self.per_minute:
                    oldest = next(t for t in self._history if now - t <= 60.0)
                    wait = max(wait, 60.0 - (now - oldest) + 0.1)

                if calls_last_hour >= self.per_hour:
                    oldest = next(t for t in self._history if now - t <= 3600.0)
                    wait = max(wait, 3600.0 - (now - oldest) + 0.1)

                if calls_last_day >= self.per_day:
                    wait = max(wait, 86400.0 - (now - self._history[0]) + 0.1)

                if wait <= 0:
                    break
                await asyncio.sleep(wait)

            self._last_call = time.monotonic()
            self._history.append(self._last_call)


limiter = RateLimiter()
concurrency = asyncio.Semaphore(3)
