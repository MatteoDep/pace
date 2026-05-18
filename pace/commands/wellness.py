"""Wellness subcommands: daily health metrics."""

import asyncio
import sys

import typer

from pace.commands._helpers import cache_for, cached_call, run
from pace.commands._trend_metrics import METRICS
from pace.dates import date_range, parse_date
from pace.models.list import ListModel
from pace.models.raw import RawData
from pace.models.trend import TrendPoint, TrendSeries
from pace.progress import named_node, progress_ui
from pace.render import Format, default_format, render
from pace.render.trend import render_trend

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


@wellness_app.command("trend")
def trend(
    metric: str = typer.Argument(..., help=f"Metric: one of {', '.join(sorted(METRICS))}."),
    days: int = typer.Option(30, "--days", "-n", help="Days back from --to."),
    to: str = typer.Option("today", "--to", "-T"),
    from_: str | None = typer.Option(None, "--from", "-F", help="Overrides --days when set."),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Plot a metric over a date range (sparkline + min/avg/max)."""
    if metric not in METRICS:
        typer.secho(
            f"Unknown metric {metric!r}. Choices: {', '.join(sorted(METRICS))}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    spec = METRICS[metric]

    end = parse_date(to)
    start = parse_date(from_) if from_ else parse_date(f"-{days - 1}d")
    if start > end:
        start, end = end, start
    dates = list(date_range(start, end))

    async def fetch_series() -> TrendSeries:
        cache = cache_for(no_cache)
        try:
            async def one(d):
                data = await cached_call(spec.method, d.isoformat(), cache=cache, ttl=86400)
                return TrendPoint(date=d, value=spec.extract(data))

            async with progress_ui():
                tasks = [
                    asyncio.create_task(named_node(f"{metric}-{d.isoformat()}", one)(d))
                    for d in dates
                ]
                points = await asyncio.gather(*tasks)
        finally:
            if cache is not None:
                cache.close()
        return TrendSeries(metric=metric, points=points)

    try:
        series = asyncio.run(fetch_series())
    except RuntimeError as err:
        typer.secho(f"Error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None

    effective = fmt or default_format()
    if effective is Format.TABLE:
        render_trend(series, sys.stdout)
    elif effective is Format.JSON:
        render(series, Format.JSON, sys.stdout)
    else:
        # CSV / MD render through the list path.
        rows = ListModel(
            items=[p.model_dump() for p in series.points],
            columns=["date", "value"],
            title=f"trend: {metric}",
        )
        render(rows, effective, sys.stdout)


@wellness_app.command("spo2")
def spo2(
    date: str = typer.Option("today", "--date", "-d"),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Blood oxygen saturation."""
    coro = _fetch_for_date("get_spo2_data", no_cache)
    run(lambda: coro(date), fmt)
