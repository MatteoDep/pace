"""`pace chart KIND`: render a PNG chart and return its path plus a summary of what it shows."""

import importlib
from datetime import date
from pathlib import Path
from typing import Any

import typer

from pace import charts
from pace import load as load_mod
from pace import plan as plan_store
from pace.cache import Cache, CacheTracker
from pace.commands.plan import plan_range
from pace.envelope import PaceError, Result, run
from pace.factory import NO_CACHE_OPT

METRICS = ("km", "elev", "hours")
SPORTS = ("running", "all")
WEEK_FLAGS = ("--weeks", "--weeks-ahead", "--metric")
DAY_FLAGS = ("--from", "--to", "--past", "--ahead")
#: Flags each kind accepts besides --out and --no-cache; a flag given to another kind is bad_args.
KIND_FLAGS: dict[str, tuple[str, ...]] = {"volume": (*WEEK_FLAGS, "--sport"), "plan": DAY_FLAGS, "recovery": DAY_FLAGS,
                                    "sleep": DAY_FLAGS}
#: Day-range defaults (past, ahead) for kinds keyed by days.
DAY_DEFAULTS: dict[str, tuple[int, int]] = {"plan": (14, 14), "recovery": (28, 0), "sleep": (28, 0)}


def _check(kind: str, item_id: str | None, given: dict[str, Any]) -> int | None:
    if kind not in charts.KINDS:
        raise PaceError("bad_args", f"unknown chart kind {kind!r}; one of {', '.join(charts.KINDS)}")
    activity_id = None
    if kind == "activity":
        if item_id is None or not item_id.isdigit() or int(item_id) < 1:
            raise PaceError("bad_args", "chart activity needs a positive activity ID")
        activity_id = int(item_id)
    elif item_id is not None:
        raise PaceError("bad_args", f"chart {kind} takes no ID")
    extra = [flag for flag, value in given.items() if value is not None and flag not in KIND_FLAGS[kind]]
    if extra:
        raise PaceError("bad_args", f"chart {kind} does not take {', '.join(extra)}")
    return activity_id


def chart(kind: str = typer.Argument(..., metavar="KIND", help="volume, plan, recovery or sleep."),
          item_id: str | None = typer.Argument(None, metavar="[ID]", help="Activity id (activity charts only)."),
          weeks: int | None = typer.Option(None, "--weeks", help="volume: whole weeks back (default 16)."),
          weeks_ahead: int | None = typer.Option(None, "--weeks-ahead",
                                                 help="volume: weeks after this one (default: through the plan)."),
          from_: str | None = typer.Option(None, "--from", help="Day-ranged kinds: range start."),
          to: str | None = typer.Option(None, "--to", help="Day-ranged kinds: range end."),
          past: int | None = typer.Option(None, "--past", help="Day-ranged kinds: days before today."),
          ahead: int | None = typer.Option(None, "--ahead", help="Day-ranged kinds: days after today."),
          metric: str | None = typer.Option(None, "--metric", help="volume: km, elev or hours (default km)."),
          sport: str | None = typer.Option(None, "--sport", help="volume: running (default) or all."),
          out: str | None = typer.Option(None, "--out", help="PNG path; default $PACE_HOME/charts/<name>.png."),
          no_cache: bool = NO_CACHE_OPT) -> None:
    """Render a PNG chart; data.path is the file, data.summary the numbers it shows."""
    async def work() -> Result:
        given = {"--weeks": weeks, "--weeks-ahead": weeks_ahead, "--from": from_, "--to": to, "--past": past,
                 "--ahead": ahead, "--metric": metric, "--sport": sport}
        activity_id = _check(kind, item_id, given)
        if out is not None and not out.lower().endswith(".png"):
            raise PaceError("bad_args", "--out must end in .png")
        today = date.today()
        opts: dict[str, Any] = {}
        if kind == "volume":
            opts = {"metric": metric or "km", "sport": sport or "running"}
            if opts["metric"] not in METRICS:
                raise PaceError("bad_args", f"--metric must be one of {', '.join(METRICS)}")
            if opts["sport"] not in SPORTS:
                raise PaceError("bad_args", f"--sport must be one of {', '.join(SPORTS)}")
            if weeks is not None and weeks < 1:
                raise PaceError("bad_args", "--weeks must be >= 1")
            if weeks_ahead is not None and weeks_ahead < 0:
                raise PaceError("bad_args", "--weeks-ahead must be >= 0")
            start, end = load_mod.default_range(plan_store.load(), today=today, weeks=weeks or 16,
                                                weeks_ahead=weeks_ahead)
        else:
            dpast, dahead = DAY_DEFAULTS[kind]
            start, end = plan_range(from_, to, dpast if past is None else past, dahead if ahead is None else ahead,
                                    today)
        path = Path(out).expanduser().absolute() if out is not None else charts.default_out(
            kind, start=start, end=end, metric=opts.get("metric"), sport=opts.get("sport"), activity_id=activity_id)
        module = importlib.import_module(f"pace.charts.{kind}")
        cache = Cache()
        tracker = CacheTracker()
        try:
            data = await module.fetch(start, end, today=today, cache=cache, tracker=tracker, refresh=no_cache, **opts)
        finally:
            cache.close()
        summary = module.render(data, path, **opts)
        return Result({"path": str(path), "kind": kind,
                       "range": {"from": start.isoformat(), "to": end.isoformat()}, "summary": summary},
                      tracker.report())

    run("chart", {"kind": kind, "id": item_id, "weeks": weeks, "weeks_ahead": weeks_ahead, "from": from_, "to": to,
                  "past": past, "ahead": ahead, "metric": metric, "sport": sport, "out": out}, work)
