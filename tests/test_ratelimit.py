"""Tests for the async rate limiter."""

import asyncio
import time

import pytest

from pace.ratelimit import Budget, BudgetExhausted, RateLimiter, active_budget, budget, slot


@pytest.mark.asyncio
async def test_first_call_immediate() -> None:
    rl = RateLimiter(min_interval=0.01, per_minute=10, per_hour=100, per_day=500)
    t0 = time.monotonic()
    await rl.acquire()
    assert time.monotonic() - t0 < 0.05


@pytest.mark.asyncio
async def test_min_interval_enforced() -> None:
    rl = RateLimiter(min_interval=0.1, per_minute=100, per_hour=1000, per_day=10000)
    await rl.acquire()
    t0 = time.monotonic()
    await rl.acquire()
    assert time.monotonic() - t0 >= 0.09


@pytest.mark.asyncio
async def test_per_minute_cap() -> None:
    rl = RateLimiter(min_interval=0.0, per_minute=3, per_hour=1000, per_day=10000)
    for _ in range(3):
        await rl.acquire()
    # 4th would block ~60s; assert by racing with timeout
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(rl.acquire(), timeout=0.2)


def test_limiter_and_slot_survive_a_second_event_loop() -> None:
    # min_interval > 0 so acquire waits on the lock (a 0.0 interval never binds the lock to a loop)
    rl = RateLimiter(min_interval=0.01, per_minute=1000, per_hour=1000, per_day=1000)

    async def burst():
        async def one():
            async with slot():
                await rl.acquire()
                await asyncio.sleep(0.01)

        await asyncio.gather(*(one() for _ in range(6)))  # contends: 6 > CONCURRENCY

    asyncio.run(burst())
    asyncio.run(burst())  # raised "bound to a different event loop" before
    assert len(rl._history) == 12  # one history across loops


def test_acquire_raises_instead_of_waiting_past_deadline() -> None:
    rl = RateLimiter(min_interval=5.0, per_minute=1000, per_hour=1000, per_day=1000)

    async def go():
        await rl.acquire()
        with pytest.raises(BudgetExhausted):
            await rl.acquire(budget=Budget(max_calls=10, deadline=time.monotonic() + 1.0))  # would wait ~5 s

    asyncio.run(go())


def test_budget_context_is_cleared_and_not_nested() -> None:
    with budget(2, 60) as b:
        assert active_budget() is b
        with pytest.raises(RuntimeError):
            with budget(1, 1):
                pass
        assert active_budget() is b
    assert active_budget() is None
