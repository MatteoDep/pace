"""`pace load`: weekly and daily training load (actual, planned, projected, rolling 7 days)."""

from datetime import date

import typer

from pace import load as load_mod
from pace import plan as plan_store
from pace.cache import Cache, CacheTracker
from pace.envelope import PaceError, Result, run
from pace.factory import NO_CACHE_OPT

SPORTS = ("running", "all")


def load(weeks: int = typer.Option(16, "--weeks", help="Whole weeks back, this week included (>= 1)."),
         weeks_ahead: int | None = typer.Option(None, "--weeks-ahead",
                                                help="Weeks after this one; default: through the plan's last session."),
         sport: str = typer.Option("running", "--sport", help="running or all."),
         no_cache: bool = NO_CACHE_OPT) -> None:
    """Weekly and daily load: km, elevation and hours; actual, planned, projected and a rolling 7-day sum."""
    async def work() -> Result:
        if sport not in SPORTS:
            raise PaceError("bad_args", f"--sport must be one of {', '.join(SPORTS)}")
        if weeks < 1:
            raise PaceError("bad_args", "--weeks must be >= 1")
        if weeks_ahead is not None and weeks_ahead < 0:
            raise PaceError("bad_args", "--weeks-ahead must be >= 0")
        today = date.today()
        start, end = load_mod.default_range(plan_store.load(), today=today, weeks=weeks, weeks_ahead=weeks_ahead)
        cache = Cache()
        tracker = CacheTracker()
        try:
            acts, sessions = await load_mod.fetch_inputs(start, end, today=today, cache=cache, tracker=tracker,
                                                         refresh=no_cache)
        finally:
            cache.close()
        data = load_mod.aggregate(acts, sessions, start=start, end=end, today=today, sport=sport)
        return Result(data, tracker.report())

    run("load", {"weeks": weeks, "weeks_ahead": weeks_ahead, "sport": sport}, work)
