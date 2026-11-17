"""Workout commands: list, show, calendar, push (DSL), schedule, delete."""

from datetime import date
from typing import Any

import typer

from pace.cache import Cache, CacheTracker
from pace.commands._helpers import cached_call, read_json
from pace.commands.plan import plan_range
from pace.dates import parse_date
from pace.envelope import PaceError, Result, fail, run
from pace.factory import BUCKET_OPT, FIELDS_OPT, NO_CACHE_OPT, RAW_OPT, shape_args
from pace.shape import apply
from pace.workoutwrites import delete_spec, schedule_spec, upload_spec
from pace.writes import DRY_RUN_OPT, run_write

app = typer.Typer(no_args_is_help=True, help="Structured workouts: list, show, push, schedule, delete.")

_LIST_TTL = 600
_SHOW_TTL = 86400
_CALENDAR_TTL = 300
_MAX_CALENDAR_MONTHS = 12
WORKOUT_LIST_FIELDS: tuple[str, ...] = (
    "workoutId", "workoutName", "sportType.sportTypeKey", "estimatedDurationInSecs",
    "estimatedDistanceInMeters", "updatedDate",
)


@app.command("list")
def list_(limit: int = typer.Option(50, "--limit", "-l", help="Max workouts (newest first)."),
          fields: str = FIELDS_OPT, bucket: str = BUCKET_OPT, raw: bool = RAW_OPT, no_cache: bool = NO_CACHE_OPT) -> None:
    """Saved workouts. truncated is true when the result length equals --limit; raise --limit to see more."""
    cmd = "workout list"
    try:
        f, b, echo = shape_args(fields, bucket, raw)
    except PaceError as err:
        fail(cmd, err)

    async def work() -> Result:
        cache = Cache()
        tracker = CacheTracker()
        try:
            rows = await cached_call("get_workouts", 0, limit, cache=cache, ttl=_LIST_TTL, tracker=tracker, refresh=no_cache)
        finally:
            cache.close()
        rows = rows or []
        shaped, report = apply(rows, series=(), default_fields=WORKOUT_LIST_FIELDS, fields=f, bucket=b, raw=raw, span_s=0)
        return Result(shaped, tracker.report(), report, truncated=len(rows) >= limit)

    run(cmd, {"limit": limit, **echo}, work)


@app.command("show")
def show(workout_id: int = typer.Argument(..., metavar="WORKOUT_ID"),
         fields: str = FIELDS_OPT, bucket: str = BUCKET_OPT, raw: bool = RAW_OPT, no_cache: bool = NO_CACHE_OPT) -> None:
    """One saved workout, full structure."""
    cmd = "workout show"
    try:
        f, b, echo = shape_args(fields, bucket, raw)
    except PaceError as err:
        fail(cmd, err)

    async def work() -> Result:
        cache = Cache()
        tracker = CacheTracker()
        try:
            data = await cached_call("get_workout_by_id", workout_id, cache=cache, ttl=_SHOW_TTL, tracker=tracker, refresh=no_cache)
        finally:
            cache.close()
        shaped, report = apply(data, series=(), default_fields=(), fields=f, bucket=b, raw=raw, span_s=0)
        return Result(shaped, tracker.report(), report)

    run(cmd, {"workout_id": workout_id, **echo}, work)


def _months(start: date, end: date) -> list[tuple[int, int]]:
    out, (y, m) = [], (start.year, start.month)
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


@app.command("calendar")
def calendar(from_: str | None = typer.Option(None, "--from", help="Range start; default today - --past."),
             to: str | None = typer.Option(None, "--to", help="Range end; default today + --ahead."),
             past: int = typer.Option(0, "--past", help="Days before today when --from is not given."),
             ahead: int = typer.Option(14, "--ahead", help="Days after today when --to is not given."),
             no_cache: bool = NO_CACHE_OPT) -> None:
    """Workouts scheduled on the Garmin calendar in a range (one call per month, at most 12)."""
    cmd = "workout calendar"

    async def work() -> Result:
        start, end = plan_range(from_, to, past, ahead, date.today())
        months = _months(start, end)
        if len(months) > _MAX_CALENDAR_MONTHS:
            raise PaceError("bad_args", f"range spans {len(months)} months; at most {_MAX_CALENDAR_MONTHS}")
        lo, hi = start.isoformat(), end.isoformat()
        items: dict[Any, dict] = {}
        cache = Cache()
        tracker = CacheTracker()
        try:
            for y, m in months:
                # Garmin numbers months from 0, and the month view also lists days of the neighbouring months.
                view = await cached_call("connectapi", f"/calendar-service/year/{y}/month/{m - 1}", cache=cache,
                                         ttl=_CALENDAR_TTL, tracker=tracker, refresh=no_cache)
                for it in (view or {}).get("calendarItems") or []:
                    if (it.get("itemType") == "workout" and it.get("id") is not None
                            and lo <= str(it.get("date")) <= hi):
                        items[it.get("id")] = it
        finally:
            cache.close()
        rows = sorted(({"date": it["date"], "schedule_id": it["id"], "workout_id": it.get("workoutId"),
                        "title": it.get("title")} for it in items.values()),
                      key=lambda r: (r["date"], r["schedule_id"]))
        return Result(rows, tracker.report())

    run(cmd, {"from": from_, "to": to, "past": past, "ahead": ahead}, work)


@app.command("push")
def push(file: str = typer.Argument(..., metavar="FILE", help="Path to a DSL JSON file, or - for stdin."),
         dry_run: bool = DRY_RUN_OPT) -> None:
    """Create a workout on Garmin Connect from the compact DSL (see `pace schema` -> workout_dsl)."""
    cmd = "workout push"
    try:
        spec = upload_spec(read_json(file))
    except PaceError as err:
        fail(cmd, err)
    run(cmd, {"file": file, "dry_run": dry_run}, lambda: run_write(spec, dry_run=dry_run))


@app.command("schedule")
def schedule(workout_id: int = typer.Argument(..., metavar="WORKOUT_ID"),
             date: str = typer.Argument(..., metavar="DATE", help="YYYY-MM-DD | today | yesterday | +Nd | -Nd"),
             dry_run: bool = DRY_RUN_OPT) -> None:
    """Put a saved workout on the Garmin calendar (syncs to the watch)."""
    cmd = "workout schedule"
    try:
        iso = parse_date(date).isoformat()
    except PaceError as err:
        fail(cmd, err)
    spec = schedule_spec(workout_id, iso)
    run(cmd, {"workout_id": workout_id, "date": iso, "dry_run": dry_run}, lambda: run_write(spec, dry_run=dry_run))


@app.command("delete")
def delete(workout_id: int = typer.Argument(..., metavar="WORKOUT_ID"), dry_run: bool = DRY_RUN_OPT) -> None:
    """Delete a saved workout (only workouts; activities are never deleted)."""
    spec = delete_spec(workout_id)
    run("workout delete", {"workout_id": workout_id, "dry_run": dry_run}, lambda: run_write(spec, dry_run=dry_run))
