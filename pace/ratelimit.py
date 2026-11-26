"""Async rate limiter -- sliding window with per-minute/hour/day caps."""

import asyncio
import time
import weakref
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass


class BudgetExhausted(Exception):
    """The active budget forbids another Garmin call."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Budget:
    """Call and time cap for a batch of commands; `deadline` is on the `time.monotonic()` clock."""

    max_calls: int
    deadline: float
    calls: int = 0
    stopped: str | None = None

    def check(self, wait: float = 0.0) -> None:
        """Raise BudgetExhausted if a call that starts after `wait` seconds is not allowed."""
        if self.stopped:
            raise BudgetExhausted(f"stopped after {self.stopped}")
        if self.calls >= self.max_calls:
            raise BudgetExhausted(f"call budget of {self.max_calls} spent")
        if time.monotonic() + wait > self.deadline:
            raise BudgetExhausted("time budget spent")

    def stop(self, code: str) -> None:
        self.stopped = code


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
        # asyncio primitives bind to the loop that first waits on them; `pace export` runs one
        # asyncio.run per command, so keep one lock per running loop and one shared history.
        self._locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
            weakref.WeakKeyDictionary()
        )

    def _lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(loop)
        if lock is None:
            lock = self._locks[loop] = asyncio.Lock()
        return lock

    async def acquire(self, budget: Budget | None = None) -> None:
        """Block until a call is permitted, then register it.

        With a budget, raise BudgetExhausted instead of sleeping past its limits, and count the
        call when it is registered."""
        async with self._lock():
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

                if budget is not None:
                    budget.check(wait)
                if wait <= 0:
                    break
                await asyncio.sleep(wait)

            self._last_call = time.monotonic()
            self._history.append(self._last_call)
            if budget is not None:
                budget.calls += 1


#: Maximum Garmin calls in flight at once (a day fan-out is gathered, not serialized).
CONCURRENCY = 3

limiter = RateLimiter()

#: Call outcomes after which the rest of a budget is not worth spending.
STOP_CODES = ("rate_limited", "network")

_slots: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = weakref.WeakKeyDictionary()


def slot() -> asyncio.Semaphore:
    """The CONCURRENCY-slot semaphore of the running event loop (one per loop)."""
    loop = asyncio.get_running_loop()
    sem = _slots.get(loop)
    if sem is None:
        sem = _slots[loop] = asyncio.Semaphore(CONCURRENCY)
    return sem


_active: Budget | None = None


def active_budget() -> Budget | None:
    return _active


@contextmanager
def budget(max_calls: int, max_seconds: float) -> Iterator[Budget]:
    """Make a new Budget the active one for the block. Budgets do not nest."""
    global _active
    if _active is not None:
        raise RuntimeError("a budget is already active")
    b = Budget(max_calls=max_calls, deadline=time.monotonic() + max_seconds)
    _active = b
    try:
        yield b
    finally:
        _active = None
