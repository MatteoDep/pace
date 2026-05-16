"""Device subcommands."""

import typer

from pace.commands._helpers import cache_for, cached_call, run
from pace.dates import parse_date
from pace.models.list import ListModel
from pace.models.raw import RawData
from pace.render import Format

device_app = typer.Typer(no_args_is_help=True, help="Registered Garmin devices.")

_TTL = 86400  # 1 day

_DEVICE_COLUMNS = ["deviceId", "displayName", "productDisplayName", "serialNumber"]


@device_app.command("list")
def list_(
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """List all registered devices."""
    async def fetch() -> ListModel:
        cache = cache_for(no_cache)
        try:
            data = await cached_call("get_devices", cache=cache, ttl=_TTL)
        finally:
            if cache is not None:
                cache.close()
        return ListModel(items=data or [], columns=_DEVICE_COLUMNS, title="devices")

    run(fetch, fmt)


@device_app.command("last-used")
def last_used(
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Show the device most recently synced."""
    async def fetch() -> RawData:
        cache = cache_for(no_cache)
        try:
            data = await cached_call("get_device_last_used", cache=cache, ttl=_TTL)
        finally:
            if cache is not None:
                cache.close()
        return RawData.from_response(data)

    run(fetch, fmt)


@device_app.command("settings")
def settings(
    device_id: int = typer.Argument(...),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Show settings for a specific device."""
    async def fetch() -> RawData:
        cache = cache_for(no_cache)
        try:
            data = await cached_call("get_device_settings", device_id, cache=cache, ttl=_TTL)
        finally:
            if cache is not None:
                cache.close()
        return RawData.from_response(data)

    run(fetch, fmt)


@device_app.command("alarms")
def alarms(
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Show device alarm settings."""
    async def fetch() -> RawData:
        cache = cache_for(no_cache)
        try:
            data = await cached_call("get_device_alarms", cache=cache, ttl=_TTL)
        finally:
            if cache is not None:
                cache.close()
        return RawData.from_response(data)

    run(fetch, fmt)


@device_app.command("solar")
def solar(
    device_id: int = typer.Argument(...),
    from_: str = typer.Option("-7d", "--from", "-F"),
    to: str = typer.Option("today", "--to", "-T"),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Solar charging data for a device over a date range."""
    async def fetch() -> RawData:
        start = parse_date(from_).isoformat()
        end = parse_date(to).isoformat()
        cache = cache_for(no_cache)
        try:
            data = await cached_call(
                "get_device_solar_data", device_id, start, end, cache=cache, ttl=_TTL
            )
        finally:
            if cache is not None:
                cache.close()
        return RawData.from_response(data)

    run(fetch, fmt)
