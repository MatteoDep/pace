"""Shared helpers for command modules."""

import asyncio
import sys
from collections.abc import Callable, Coroutine
from typing import Any

import typer
from pydantic import BaseModel

from pace.api import safe_call
from pace.cache import Cache
from pace.render import Format, default_format, render


def run(coro_factory: Callable[[], Coroutine[Any, Any, BaseModel]], fmt: Format | None) -> None:
    """Run an async fetch+model coroutine and render the result.

    Centralised error handling for command modules.
    """
    try:
        model = asyncio.run(coro_factory())
    except RuntimeError as err:
        typer.secho(f"Error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    render(model, fmt or default_format(), sys.stdout)


async def cached_call(
    method: str,
    *args: Any,
    cache: Cache | None,
    ttl: int,
    **kwargs: Any,
) -> Any:
    """Call a Garmin method through `safe_call`, optionally via the cache.

    Raises RuntimeError with the API error message on failure.
    """
    async def fetch() -> Any:
        ok, data, err = await safe_call(method, *args, **kwargs)
        if not ok:
            raise RuntimeError(err.message)
        return data

    if cache is None:
        return await fetch()

    key_parts = [method, *(str(a) for a in args)]
    if kwargs:
        key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
    key = ":".join(key_parts)
    return await cache.get_or_fetch(key, fetch, ttl=ttl)


def cache_for(no_cache: bool) -> Cache | None:
    return None if no_cache else Cache()
