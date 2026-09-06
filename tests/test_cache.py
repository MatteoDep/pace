"""Tests for the SQLite cache."""

from datetime import date, timedelta

import pytest

from pace.cache import Cache, CacheTracker, ttl_for_day


@pytest.mark.asyncio
async def test_miss_then_hit(tmp_path) -> None:
    cache = Cache(tmp_path / "c.db")
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        return {"x": 1}

    assert (await cache.get_or_fetch("k", fetch, ttl=60))[0] == {"x": 1}
    assert (await cache.get_or_fetch("k", fetch, ttl=60))[0] == {"x": 1}
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


@pytest.mark.asyncio
async def test_get_or_fetch_reports_hit_and_age(tmp_path) -> None:
    cache = Cache(tmp_path / "c.db")
    v, hit, age = await cache.get_or_fetch("k", lambda: _ret(1), ttl=60)
    assert (v, hit, age) == (1, False, 0)
    v, hit, age = await cache.get_or_fetch("k", lambda: _ret(2), ttl=60)
    assert v == 1 and hit is True and 0 <= age < 5
    cache.close()


def test_tracker_report() -> None:
    t = CacheTracker()
    assert t.report() is None
    t.add(True, 30)
    assert t.report() == {"hit": True, "oldest_age_s": 30}
    t.add(False, 0)
    assert t.report() == {"hit": "partial", "oldest_age_s": 30}
    t2 = CacheTracker()
    t2.add(False, 0)
    assert t2.report() == {"hit": False, "oldest_age_s": 0}


@pytest.mark.asyncio
async def test_refresh_skips_read_and_overwrites(tmp_path) -> None:
    cache = Cache(tmp_path / "c.db")
    await cache.get_or_fetch("k", lambda: _ret(1), ttl=60)

    v, hit, age = await cache.get_or_fetch("k", lambda: _ret(2), ttl=60, refresh=True)
    assert (v, hit, age) == (2, False, 0)

    v, hit, age = await cache.get_or_fetch("k", lambda: _ret(3), ttl=60)
    assert v == 2 and hit is True
    cache.close()


def test_ttl_for_day() -> None:
    today = date(2026, 9, 4)
    assert ttl_for_day(today, 300, today) == 300
    assert ttl_for_day(today - timedelta(days=1), 300, today) == 86400
    assert ttl_for_day(today - timedelta(days=2), 300, today) == 604800


@pytest.mark.asyncio
async def test_invalidate_prefix_only(tmp_path) -> None:
    cache = Cache(tmp_path / "c.db")
    await cache.get_or_fetch("get_hydration_data:2026-09-06", lambda: _ret(1), ttl=600)
    await cache.get_or_fetch("get_hydration_data:2026-09-05", lambda: _ret(2), ttl=600)
    await cache.get_or_fetch("get_weigh_ins:2026-09-01:2026-09-06", lambda: _ret(3), ttl=600)
    assert cache.invalidate("get_hydration_data:2026-09-06") == 1
    assert cache.invalidate("get_weigh_ins:") == 1
    assert cache.stats()["entries"] == 1
    v, hit, _ = await cache.get_or_fetch("get_hydration_data:2026-09-05", lambda: _ret(9), ttl=600)
    assert v == 2 and hit is True
    cache.close()


@pytest.mark.asyncio
async def test_invalidate_escapes_like_wildcards(tmp_path) -> None:
    cache = Cache(tmp_path / "c.db")
    await cache.get_or_fetch("a_b:1", lambda: _ret(1), ttl=600)
    await cache.get_or_fetch("axb:1", lambda: _ret(2), ttl=600)
    assert cache.invalidate("a_b") == 1          # `_` must not act as a wildcard
    assert cache.stats()["entries"] == 1
    cache.close()
