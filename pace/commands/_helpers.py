"""Shared helpers for command modules."""

import asyncio
import sys
from collections.abc import Callable, Coroutine
from typing import Any

import typer
from pydantic import BaseModel

from pace.api import safe_call
from pace.cache import Cache, CacheTracker
from pace.envelope import PaceError, log
from pace.render import Format, default_format, render


def run(coro_factory: Callable[[], Coroutine[Any, Any, BaseModel]], fmt: Format | None) -> None:
    """Run an async fetch+model coroutine and render the result.

    Centralised error handling for command modules.
    """
    try:
        model = asyncio.run(coro_factory())
    except PaceError as err:
        typer.secho(f"Error: {err.message}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    except RuntimeError as err:
        typer.secho(f"Error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    render(model, fmt or default_format(), sys.stdout)


async def cached_call(
    method: str,
    *args: Any,
    cache: Cache | None,
    ttl: int,
    tracker: CacheTracker | None = None,
    **kwargs: Any,
) -> Any:
    """Call a Garmin method through `safe_call`, optionally via the cache. Raises PaceError."""
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
    value, hit, age = await cache.get_or_fetch(":".join(key_parts), fetch, ttl=ttl)
    log(f"{'hit ' if hit else 'miss'} {':'.join(key_parts)} age={age}s")
    if tracker is not None:
        tracker.add(hit, age)
    return value


def cache_for(no_cache: bool) -> Cache | None:
    return None if no_cache else Cache()
