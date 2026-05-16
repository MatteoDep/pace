"""Tests for the async rate limiter."""

import asyncio
import time

import pytest

from pace.ratelimit import RateLimiter


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
