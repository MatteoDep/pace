"""Shared helpers for command modules."""

import json
import sys
from pathlib import Path
from typing import Any

from pace.api import safe_call
from pace.cache import Cache, CacheTracker
from pace.envelope import PaceError, log


async def cached_call(
    method: str,
    *args: Any,
    cache: Cache | None,
    ttl: int,
    tracker: CacheTracker | None = None,
    refresh: bool = False,
    key_prefix: str | None = None,
    **kwargs: Any,
) -> Any:
    """Call a Garmin method through `safe_call`, optionally via the cache. Raises PaceError.

    `refresh` bypasses the cache read but still writes the fresh result (used by --no-cache,
    which refreshes rather than fully disabling the cache); it is ignored when `cache is None`.
    `key_prefix` gives a caller its own cache keys (`zones:get_activity:ID`), so it can keep its own TTL.
    """
    async def fetch() -> Any:
        ok, data, err = await safe_call(method, *args, **kwargs)
        if not ok:
            assert err is not None
            raise PaceError.from_api(err)
        return data

    if cache is None:
        value = await fetch()
        if tracker is not None:
            tracker.add(False, 0)
        return value

    key_parts = [*([key_prefix] if key_prefix else []), method, *(str(a) for a in args)]
    if kwargs:
        key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
    value, hit, age = await cache.get_or_fetch(":".join(key_parts), fetch, ttl=ttl, refresh=refresh)
    log(f"{'hit ' if hit else 'miss'} {':'.join(key_parts)} age={age}s")
    if tracker is not None:
        tracker.add(hit, age)
    return value


def read_json(source: str) -> Any:
    """Parse JSON from a file path, or from stdin when `source` is `-`. Raises PaceError(bad_args)."""
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    except OSError as exc:
        raise PaceError("bad_args", f"cannot read {source}: {exc}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise PaceError("bad_args", f"{source}: invalid JSON ({exc.msg} at line {exc.lineno})") from None
