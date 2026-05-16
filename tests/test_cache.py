"""Tests for the SQLite cache."""

import pytest

from pace.cache import Cache


@pytest.mark.asyncio
async def test_miss_then_hit(tmp_path) -> None:
    cache = Cache(tmp_path / "c.db")
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        return {"x": 1}

    assert await cache.get_or_fetch("k", fetch, ttl=60) == {"x": 1}
    assert await cache.get_or_fetch("k", fetch, ttl=60) == {"x": 1}
    assert calls == 1
    cache.close()


@pytest.mark.asyncio
async def test_ttl_expiry(tmp_path) -> None:
    cache = Cache(tmp_path / "c.db")
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        return calls

    await cache.get_or_fetch("k", fetch, ttl=0)
    await cache.get_or_fetch("k", fetch, ttl=0)
    assert calls == 2
    cache.close()


@pytest.mark.asyncio
async def test_clear(tmp_path) -> None:
    cache = Cache(tmp_path / "c.db")
    await cache.get_or_fetch("a", lambda: _ret(1), ttl=60)
    await cache.get_or_fetch("b", lambda: _ret(2), ttl=60)
    assert cache.stats()["entries"] == 2
    assert cache.clear() == 2
    assert cache.stats()["entries"] == 0
    cache.close()


async def _ret(v):
    return v
