"""Shared helpers for command modules."""

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
    **kwargs: Any,
) -> Any:
    """Call a Garmin method through `safe_call`, optionally via the cache. Raises PaceError.

    `refresh` bypasses the cache read but still writes the fresh result (used by --no-cache,
    which refreshes rather than fully disabling the cache); it is ignored when `cache is None`.
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

    key_parts = [method, *(str(a) for a in args)]
    if kwargs:
        key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
    value, hit, age = await cache.get_or_fetch(":".join(key_parts), fetch, ttl=ttl, refresh=refresh)
    log(f"{'hit ' if hit else 'miss'} {':'.join(key_parts)} age={age}s")
    if tracker is not None:
        tracker.add(hit, age)
    return value
