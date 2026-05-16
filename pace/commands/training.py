"""Training subcommands: load, readiness, predictions."""

import typer

from pace.commands._helpers import cache_for, cached_call, run
from pace.dates import parse_date
from pace.models.raw import RawData
from pace.render import Format

training_app = typer.Typer(no_args_is_help=True, help="Training load and performance metrics.")

_TTL = 3600  # 1 hour


def _date_fetch(method: str, no_cache: bool, date_str: str):
    async def inner() -> RawData:
        cdate = parse_date(date_str).isoformat()
        cache = cache_for(no_cache)
        try:
            data = await cached_call(method, cdate, cache=cache, ttl=_TTL)
        finally:
            if cache is not None:
                cache.close()
        return RawData.from_response(data)

    return inner


def _no_arg_fetch(method: str, no_cache: bool):
    async def inner() -> RawData:
        cache = cache_for(no_cache)
        try:
            data = await cached_call(method, cache=cache, ttl=_TTL)
        finally:
            if cache is not None:
                cache.close()
        return RawData.from_response(data)

    return inner


@training_app.command("status")
def status(
    date: str = typer.Option("today", "--date", "-d"),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Training status (load, balance, focus)."""
    run(_date_fetch("get_training_status", no_cache, date), fmt)


@training_app.command("readiness")
def readiness(
    date: str = typer.Option("today", "--date", "-d"),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Daily training readiness score."""
    run(_date_fetch("get_training_readiness", no_cache, date), fmt)


@training_app.command("race-predictions")
def race_predictions(
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Predicted race times across distances."""
    run(_no_arg_fetch("get_race_predictions", no_cache), fmt)


@training_app.command("endurance")
def endurance(
    date: str = typer.Option("today", "--date", "-d"),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Endurance score (aerobic capacity)."""
    run(_date_fetch("get_endurance_score", no_cache, date), fmt)


@training_app.command("fitness-age")
def fitness_age(
    date: str = typer.Option("today", "--date", "-d"),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Estimated fitness age."""
    run(_date_fetch("get_fitnessage_data", no_cache, date), fmt)


@training_app.command("hill-score")
def hill_score(
    date: str = typer.Option("today", "--date", "-d"),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Climbing ability score."""
    run(_date_fetch("get_hill_score", no_cache, date), fmt)
