"""SQLite-backed read-through cache with per-key TTL."""

import asyncio
import json
import sqlite3
import time
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pace import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value BLOB NOT NULL,
    fetched_at INTEGER NOT NULL,
    ttl INTEGER NOT NULL
);
"""


class Cache:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.CACHE_DB
        config.ensure_home()
        self._conn = sqlite3.connect(str(self.path))
        self._conn.execute(_SCHEMA)
        self._conn.commit()
        self._lock = asyncio.Lock()

    def close(self) -> None:
        self._conn.close()

    def _get(self, key: str) -> tuple[Any, int] | None:
        cur = self._conn.execute(
            "SELECT value, fetched_at, ttl FROM cache WHERE key = ?", (key,)
        )
        row = cur.fetchone()
        if not row:
            return None
        value, fetched_at, ttl = row
        age = int(time.time() - fetched_at)
        if age >= ttl:
            return None
        return json.loads(value), age

    def _put(self, key: str, value: Any, ttl: int) -> None:
        payload = json.dumps(value, default=str)
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (key, value, fetched_at, ttl) "
            "VALUES (?, ?, ?, ?)",
            (key, payload, int(time.time()), ttl),
        )
        self._conn.commit()

    async def get_or_fetch(
        self,
        key: str,
        fetch: Callable[[], Awaitable[Any]],
        ttl: int,
        refresh: bool = False,
    ) -> tuple[Any, bool, int]:
        """Return (value, hit, age_s). `refresh` skips the read and always fetches + stores."""
        if not refresh:
            async with self._lock:
                cached = self._get(key)
                if cached is not None:
                    return cached[0], True, cached[1]
        value = await fetch()
        async with self._lock:
            self._put(key, value, ttl)
        return value, False, 0

    def clear(self) -> int:
        cur = self._conn.execute("DELETE FROM cache")
        self._conn.commit()
        return cur.rowcount

    def stats(self) -> dict[str, int]:
        cur = self._conn.execute("SELECT COUNT(*) FROM cache")
        entries = cur.fetchone()[0]
        size = self.path.stat().st_size if self.path.exists() else 0
        return {"entries": entries, "size_bytes": size}


class CacheTracker:
    """Accumulates per-call hit/age so a fan-out can report one cache block."""

    def __init__(self) -> None:
        self._hits: list[bool] = []
        self._oldest = 0

    def add(self, hit: bool, age_s: int) -> None:
        self._hits.append(hit)
        self._oldest = max(self._oldest, age_s)

    def report(self) -> dict[str, Any] | None:
        if not self._hits:
            return None
        hit: bool | str = True if all(self._hits) else False if not any(self._hits) else "partial"
        return {"hit": hit, "oldest_age_s": self._oldest}


def ttl_for_day(day: date, today_ttl: int, today: date | None = None) -> int:
    today = today or date.today()
    if day >= today:
        return today_ttl
    if day == today - timedelta(days=1):
        return 86400
    return 604800
