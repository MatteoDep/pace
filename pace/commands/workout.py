"""Workout commands: list, show, push (DSL), schedule, delete."""

import json
import sys
from pathlib import Path
from typing import Any

import typer

from pace.cache import Cache, CacheTracker
from pace.commands._helpers import cached_call
from pace.dates import parse_date
from pace.envelope import PaceError, Result, fail, run
from pace.factory import BUCKET_OPT, FIELDS_OPT, NO_CACHE_OPT, RAW_OPT, shape_args
from pace.shape import apply
from pace.workout import build_workout
from pace.writes import WriteSpec, run_write

app = typer.Typer(no_args_is_help=True, help="Structured workouts: list, show, push, schedule, delete.")

DRY_RUN_OPT = typer.Option(False, "--dry-run", help="Show the exact Garmin payload; make no network call.")
_LIST_TTL = 600
_SHOW_TTL = 86400
WORKOUT_LIST_FIELDS: tuple[str, ...] = (
    "workoutId", "workoutName", "sportType.sportTypeKey", "estimatedDurationInSecs",
    "estimatedDistanceInMeters", "updatedDate",
)


def _read_json(source: str) -> Any:
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    except OSError as exc:
        raise PaceError("bad_args", f"cannot read {source}: {exc}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise PaceError("bad_args", f"{source}: invalid JSON ({exc.msg} at line {exc.lineno})") from None


@app.command("list")
def list_(limit: int = typer.Option(50, "--limit", "-l", help="Max workouts (newest first)."),
          fields: str = FIELDS_OPT, bucket: str = BUCKET_OPT, raw: bool = RAW_OPT, no_cache: bool = NO_CACHE_OPT) -> None:
    """Saved workouts."""
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


@app.command("push")
def push(file: str = typer.Argument(..., metavar="FILE", help="Path to a DSL JSON file, or - for stdin."),
         dry_run: bool = DRY_RUN_OPT) -> None:
    """Create a workout on Garmin Connect from the compact DSL (see `pace schema` -> workout_dsl)."""
    cmd = "workout push"
    try:
        dsl = _read_json(file)
        payload = build_workout(dsl)
    except PaceError as err:
        fail(cmd, err)
    spec = WriteSpec(resource="workout", action="create", method="upload_workout", args=(payload,),
                     payload=payload, echo=dsl, invalidate=("get_workouts:",),
                     id_of=lambda r: r.get("workoutId") if isinstance(r, dict) else None)
    run(cmd, {"file": file, "dry_run": dry_run}, lambda: run_write(spec, dry_run=dry_run))


@app.command("schedule")
def schedule(workout_id: int = typer.Argument(..., metavar="WORKOUT_ID"),
             date: str = typer.Argument(..., metavar="DATE", help="YYYY-MM-DD | today | +Nd"),
             dry_run: bool = DRY_RUN_OPT) -> None:
    """Put a saved workout on the Garmin calendar (syncs to the watch)."""
    cmd = "workout schedule"
    try:
        iso = parse_date(date).isoformat()
    except PaceError as err:
        fail(cmd, err)
    path = f"/workout-service/schedule/{workout_id}"
    body = {"date": iso}
    spec = WriteSpec(resource="workout", action="create", method="connectapi", args=(path,),
                     kwargs={"method": "POST", "json": body}, payload={"path": path, "json": body},
                     echo={"workout_id": workout_id, "date": iso},
                     id_of=lambda r: r.get("workoutScheduleId") if isinstance(r, dict) else None)
    run(cmd, {"workout_id": workout_id, "date": iso, "dry_run": dry_run}, lambda: run_write(spec, dry_run=dry_run))


@app.command("delete")
def delete(workout_id: int = typer.Argument(..., metavar="WORKOUT_ID"), dry_run: bool = DRY_RUN_OPT) -> None:
    """Delete a saved workout (only workouts; activities are never deleted)."""
    path = f"/workout-service/workout/{workout_id}"
    spec = WriteSpec(resource="workout", action="delete", method="connectapi", args=(path,),
                     kwargs={"method": "DELETE"}, payload={"path": path}, echo={"workout_id": workout_id},
                     invalidate=("get_workouts:", f"get_workout_by_id:{workout_id}"))
    run("workout delete", {"workout_id": workout_id, "dry_run": dry_run}, lambda: run_write(spec, dry_run=dry_run))
