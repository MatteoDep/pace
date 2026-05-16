"""Profile subcommands."""

import asyncio
import sys

import typer

from pace.api import safe_call
from pace.cache import Cache
from pace.models.profile import Profile
from pace.render import Format, default_format, render

profile_app = typer.Typer(no_args_is_help=True, help="User profile information.")


async def _fetch_profile(use_cache: bool, ttl: int) -> Profile:
    cache = Cache() if use_cache else None

    async def call(method: str):
        async def fetch():
            ok, data, err = await safe_call(method)
            if not ok:
                raise RuntimeError(err)
            return data

        if cache is None:
            return await fetch()
        return await cache.get_or_fetch(f"profile:{method}", fetch, ttl=ttl)

    name_t = asyncio.create_task(call("get_full_name"))
    dev_t = asyncio.create_task(call("get_device_last_used"))
    unit_t = asyncio.create_task(call("get_unit_system"))

    full_name, device_info, unit_system = await asyncio.gather(name_t, dev_t, unit_t)

    if cache is not None:
        cache.close()

    return Profile.from_api(full_name, device_info, unit_system)


@profile_app.command("show")
def show(
    fmt: Format = typer.Option(
        None,
        "--format",
        "-f",
        help="Output format. Default: table on TTY, json otherwise.",
    ),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass cache."),
    ttl: int = typer.Option(
        604800, "--cache-ttl", help="Cache TTL in seconds (default 7 days)."
    ),
) -> None:
    """Show user profile (name, profile number, unit system)."""
    try:
        profile = asyncio.run(_fetch_profile(use_cache=not no_cache, ttl=ttl))
    except RuntimeError as err:
        typer.secho(f"Error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None

    render(profile, fmt or default_format(), sys.stdout)
