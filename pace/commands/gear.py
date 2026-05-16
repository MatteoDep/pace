"""Gear subcommands: shoes, bikes, equipment."""

import typer

from pace.commands._helpers import cache_for, cached_call, run
from pace.models.list import ListModel
from pace.models.raw import RawData
from pace.render import Format

gear_app = typer.Typer(no_args_is_help=True, help="Gear inventory and usage.")

_TTL = 86400  # 1 day

_GEAR_COLUMNS = [
    "uuid",
    "gearTypeName",
    "customMakeModel",
    "gearMakeName",
    "gearStatusName",
    "dateBegin",
    "maximumMeters",
]


async def _user_profile_number(cache) -> int:
    data = await cached_call("get_device_last_used", cache=cache, ttl=_TTL)
    if not data or "userProfileNumber" not in data:
        raise RuntimeError("Cannot resolve userProfileNumber from last-used device.")
    return data["userProfileNumber"]


@gear_app.command("list")
def list_(
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """List all gear items."""
    async def fetch() -> ListModel:
        cache = cache_for(no_cache)
        try:
            upn = await _user_profile_number(cache)
            data = await cached_call("get_gear", upn, cache=cache, ttl=_TTL)
        finally:
            if cache is not None:
                cache.close()
        return ListModel(items=data or [], columns=_GEAR_COLUMNS, title="gear")

    run(fetch, fmt)


@gear_app.command("stats")
def stats(
    gear_uuid: str = typer.Argument(...),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Wear stats for a gear item."""
    async def fetch() -> RawData:
        cache = cache_for(no_cache)
        try:
            data = await cached_call("get_gear_stats", gear_uuid, cache=cache, ttl=_TTL)
        finally:
            if cache is not None:
                cache.close()
        return RawData.from_response(data)

    run(fetch, fmt)


@gear_app.command("defaults")
def defaults(
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Default gear assignments per activity type."""
    async def fetch() -> RawData:
        cache = cache_for(no_cache)
        try:
            upn = await _user_profile_number(cache)
            data = await cached_call("get_gear_defaults", upn, cache=cache, ttl=_TTL)
        finally:
            if cache is not None:
                cache.close()
        return RawData.from_response(data)

    run(fetch, fmt)
