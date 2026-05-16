"""Wellness subcommands: daily health metrics."""

import typer

from pace.commands._helpers import cache_for, cached_call, run
from pace.dates import parse_date
from pace.models.raw import RawData
from pace.render import Format

wellness_app = typer.Typer(no_args_is_help=True, help="Daily health metrics.")

# Today's data: short TTL so re-fetches stay current.
_TTL = 300  # 5 minutes


def _fetch_for_date(method: str, no_cache: bool):
    async def inner(date_str: str) -> RawData:
        cdate = parse_date(date_str).isoformat()
        cache = cache_for(no_cache)
        try:
            data = await cached_call(method, cdate, cache=cache, ttl=_TTL)
        finally:
            if cache is not None:
                cache.close()
        return RawData.from_response(data)

    return inner


@wellness_app.command("today")
def today(
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Daily summary (steps, calories, distance, floors)."""
    coro = _fetch_for_date("get_user_summary", no_cache)
    run(lambda: coro("today"), fmt)


@wellness_app.command("sleep")
def sleep(
    date: str = typer.Option("today", "--date", "-d"),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Sleep duration and stages."""
    coro = _fetch_for_date("get_sleep_data", no_cache)
    run(lambda: coro(date), fmt)


@wellness_app.command("hr")
def hr(
    date: str = typer.Option("today", "--date", "-d"),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Heart rate by minute."""
    coro = _fetch_for_date("get_heart_rates", no_cache)
    run(lambda: coro(date), fmt)


@wellness_app.command("stress")
def stress(
    date: str = typer.Option("today", "--date", "-d"),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Stress level metrics."""
    coro = _fetch_for_date("get_stress_data", no_cache)
    run(lambda: coro(date), fmt)


@wellness_app.command("body-battery")
def body_battery(
    date: str = typer.Option("today", "--date", "-d"),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Body battery (energy indicator)."""
    coro = _fetch_for_date("get_body_battery", no_cache)
    run(lambda: coro(date), fmt)


@wellness_app.command("hrv")
def hrv(
    date: str = typer.Option("today", "--date", "-d"),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Heart rate variability."""
    coro = _fetch_for_date("get_hrv_data", no_cache)
    run(lambda: coro(date), fmt)


@wellness_app.command("hydration")
def hydration(
    date: str = typer.Option("today", "--date", "-d"),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Water intake for the day."""
    coro = _fetch_for_date("get_hydration_data", no_cache)
    run(lambda: coro(date), fmt)


@wellness_app.command("spo2")
def spo2(
    date: str = typer.Option("today", "--date", "-d"),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Blood oxygen saturation."""
    coro = _fetch_for_date("get_spo2_data", no_cache)
    run(lambda: coro(date), fmt)
