"""Profile subcommands."""

import asyncio

import typer

from pace.commands._helpers import cache_for, cached_call, run
from pace.models.profile import Profile
from pace.models.raw import RawData
from pace.render import Format

profile_app = typer.Typer(no_args_is_help=True, help="User profile information.")

_DEFAULT_TTL = 604800  # 7 days


@profile_app.command("show")
def show(
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    ttl: int = typer.Option(_DEFAULT_TTL, "--cache-ttl"),
) -> None:
    """Show user profile (name, profile number, unit system)."""
    async def fetch() -> Profile:
        cache = cache_for(no_cache)
        try:
            name, dev, unit = await asyncio.gather(
                cached_call("get_full_name", cache=cache, ttl=ttl),
                cached_call("get_device_last_used", cache=cache, ttl=ttl),
                cached_call("get_unit_system", cache=cache, ttl=ttl),
            )
        finally:
            if cache is not None:
                cache.close()
        return Profile.from_api(name, dev, unit)

    run(fetch, fmt)


@profile_app.command("settings")
def settings(
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    ttl: int = typer.Option(_DEFAULT_TTL, "--cache-ttl"),
) -> None:
    """Show user profile settings."""
    async def fetch() -> RawData:
        cache = cache_for(no_cache)
        try:
            data = await cached_call("get_userprofile_settings", cache=cache, ttl=ttl)
        finally:
            if cache is not None:
                cache.close()
        return RawData.from_response(data)

    run(fetch, fmt)


@profile_app.command("unit-system")
def unit_system(
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    ttl: int = typer.Option(_DEFAULT_TTL, "--cache-ttl"),
) -> None:
    """Show the user's measurement unit system (metric/imperial)."""
    async def fetch() -> RawData:
        cache = cache_for(no_cache)
        try:
            data = await cached_call("get_unit_system", cache=cache, ttl=ttl)
        finally:
            if cache is not None:
                cache.close()
        return RawData.from_response(data)

    run(fetch, fmt)
