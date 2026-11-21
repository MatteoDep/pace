"""cached_call: cache keys and the optional key prefix."""

import asyncio
import sqlite3

import pytest

from pace import config
from pace.cache import Cache
from pace.commands._helpers import cached_call


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")
    made = []

    async def safe_call(method, *args, **kwargs):
        made.append(method)
        return True, {"m": method}, None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    return made


def _keys() -> list[str]:
    conn = sqlite3.connect(str(config.CACHE_DB))
    try:
        return sorted(k for (k,) in conn.execute("SELECT key FROM cache"))
    finally:
        conn.close()


def test_key_prefix_gives_own_keys(_home) -> None:
    async def go():
        cache = Cache()
        try:
            await cached_call("get_activity", 5, cache=cache, ttl=60)
            await cached_call("get_activity", 5, cache=cache, ttl=60, key_prefix="zones")
            await cached_call("get_activity", 5, cache=cache, ttl=60, key_prefix="zones")  # hit
        finally:
            cache.close()

    asyncio.run(go())
    assert _keys() == ["get_activity:5", "zones:get_activity:5"]
    assert len(_home) == 2
