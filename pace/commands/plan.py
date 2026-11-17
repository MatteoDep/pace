"""Plan commands: blocks and dated sessions in a local file; push and unpush write to Garmin."""

from datetime import date, timedelta
from typing import Any

import typer

from pace import matching, plan, planpush
from pace.cache import Cache, CacheTracker
from pace.commands._helpers import cached_call, read_json
from pace.dates import parse_date
from pace.effort import with_effort
from pace.envelope import PaceError, Result, run
from pace.factory import NO_CACHE_OPT
from pace.workout import parse_distance, parse_duration
from pace.writes import DRY_RUN_OPT

app = typer.Typer(no_args_is_help=True, rich_markup_mode=None, pretty_exceptions_enable=False,
                  help="Training plan: blocks and dated sessions (local file); push sessions to Garmin.")
block_app = typer.Typer(no_args_is_help=True, rich_markup_mode=None, pretty_exceptions_enable=False,
                        help="Plan blocks (phases of the plan).")
app.add_typer(block_app, name="block")

_NONE_HELP = " `none` clears it."
_ACTIVITIES_TTL = 600
_ACTIVITY_TTL = 86400  # same key and ttl as `activity show`


def plan_range(from_: str | date | None, to: str | date | None, past: int, ahead: int, today: date) -> tuple[date, date]:
    """Resolve --from/--to (winning when given) or fall back to today-past / today+ahead."""
    start = parse_date(from_) if isinstance(from_, str) else from_
    end = parse_date(to) if isinstance(to, str) else to
    if start is None:
        start = today - timedelta(days=past)
    if end is None:
        end = today + timedelta(days=ahead)
    if start > end:
        raise PaceError("bad_args", f"--from {start} is after --to {end}")
    return start, end


async def fetch_status_inputs(start: date, end: date, *, today: date, cache: Cache, tracker: Any,
                              refresh: bool) -> tuple[list[dict], list[dict]]:
    """(sessions, activity rows) for `plan status`: one cached activities call, none when the range is all future."""
    sessions = plan.sessions_in(plan.load(), start, end)
    rows: list[dict] = []
    if start <= today:
        rows = await cached_call("get_activities_by_date", start.isoformat(), min(end, today).isoformat(),
                                 None, cache=cache, ttl=_ACTIVITIES_TTL, tracker=tracker, refresh=refresh)
        rows = [a for a in rows or [] if a.get("activityId") is not None]
    return sessions, rows


def _clearable(value: str | None, parse: Any) -> tuple[bool, Any]:
    """(given, parsed): the literal `none` clears (None)."""
    if value is None:
        return False, None
    return True, None if value == "none" else parse(value)


def _int(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        raise PaceError("bad_args", f"expected an integer or `none`, got {text!r}") from None


def _float(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        raise PaceError("bad_args", f"expected a number or `none`, got {text!r}") from None


@app.command("new")
def new(name: str = typer.Option(..., "--name", help="Plan name."),
        goal: str | None = typer.Option(None, "--goal"),
        event_date: str | None = typer.Option(None, "--event-date", help="Target event date.")) -> None:
    """Start a new plan; the current one is archived to $PACE_HOME/plans/."""
    async def work() -> Result:
        with planpush.push_lock(), plan.transaction() as data:
            archived = plan.new_plan(data, name=name, goal=goal, event_date=event_date, today=date.today())
            return Result({"plan": data["plan"], "archived": archived})

    run("plan new", {"name": name, "goal": goal, "event_date": event_date}, work)


@app.command("show")
def show(from_: str | None = typer.Option(None, "--from", help="Range start; default today - --past."),
         to: str | None = typer.Option(None, "--to", help="Range end; default today + --ahead."),
         past: int = typer.Option(7, "--past", help="Days before today when --from is not given."),
         ahead: int = typer.Option(28, "--ahead", help="Days after today when --to is not given.")) -> None:
    """Plan meta, all blocks and the sessions in the range."""
    async def work() -> Result:
        start, end = plan_range(from_, to, past, ahead, date.today())
        data = plan.load()
        return Result({"plan": data["plan"], "blocks": data["blocks"], "sessions": plan.sessions_in(data, start, end)})

    run("plan show", {"from": from_, "to": to, "past": past, "ahead": ahead}, work)


@app.command("status")
def status(from_: str | None = typer.Option(None, "--from", help="Range start; default today - --past."),
           to: str | None = typer.Option(None, "--to", help="Range end; default today + --ahead."),
           past: int = typer.Option(7, "--past", help="Days before today when --from is not given."),
           ahead: int = typer.Option(7, "--ahead", help="Days after today when --to is not given."),
           no_effort: bool = typer.Option(False, "--no-effort",
                                          help="Skip RPE and feel: they cost one Garmin call per activity (cached one day)."),
           no_cache: bool = NO_CACHE_OPT) -> None:
    """Planned vs done for a range: sessions matched to Garmin activities, unplanned activities, totals."""
    async def work() -> Result:
        today = date.today()
        start, end = plan_range(from_, to, past, ahead, today)
        cache = Cache()
        tracker = CacheTracker()
        try:
            sessions, rows = await fetch_status_inputs(start, end, today=today, cache=cache, tracker=tracker,
                                                       refresh=no_cache)
            effort: dict[int, dict] = {}
            if not no_effort:
                for a in rows:
                    try:
                        full = with_effort(await cached_call("get_activity", a["activityId"], cache=cache,
                                                             ttl=_ACTIVITY_TTL, tracker=tracker, refresh=no_cache))
                    except PaceError:  # one unreadable activity leaves its rpe and feel null, not the whole call
                        continue
                    effort[a["activityId"]] = {"rpe": full.get("rpe"), "feel": full.get("feel")}
        finally:
            cache.close()
        return Result(matching.match(sessions, rows, today=today, effort=effort), tracker.report())

    run("plan status", {"from": from_, "to": to, "past": past, "ahead": ahead, "no_effort": no_effort}, work)


@app.command("meta")
def meta(key: str = typer.Argument(..., metavar="KEY", help="name, goal or event_date."),
         value: str = typer.Argument(..., metavar="VALUE")) -> None:
    """Set a plan field."""
    async def work() -> Result:
        with plan.transaction() as data:
            return Result({"plan": plan.set_meta(data, key, value)})

    run("plan meta", {"key": key, "value": value}, work)


@block_app.command("add")
def block_add(name: str = typer.Option(..., "--name"),
              phase: str = typer.Option(..., "--phase", help=f"One of {', '.join(plan.PHASES)}."),
              start: str = typer.Option(..., "--start", help="First date."),
              end: str = typer.Option(..., "--end", help="Last date."),
              focus: str | None = typer.Option(None, "--focus")) -> None:
    """Add a block; ids are b1, b2, ..."""
    async def work() -> Result:
        item = {"name": name, "phase": phase, "start": start, "end": end}
        if focus is not None:
            item["focus"] = focus
        with plan.transaction() as data:
            return Result({"block": plan.add_block(data, item)})

    run("plan block add", {"name": name, "phase": phase, "start": start, "end": end, "focus": focus}, work)


@block_app.command("set")
def block_set(block_id: str = typer.Argument(..., metavar="ID"),
              name: str | None = typer.Option(None, "--name"),
              phase: str | None = typer.Option(None, "--phase", help=f"One of {', '.join(plan.PHASES)}."),
              start: str | None = typer.Option(None, "--start"),
              end: str | None = typer.Option(None, "--end"),
              focus: str | None = typer.Option(None, "--focus")) -> None:
    """Change a block; only the given fields change."""
    async def work() -> Result:
        changes = {k: v for k, v in (("name", name), ("phase", phase), ("start", start), ("end", end),
                                     ("focus", focus)) if v is not None}
        if not changes:
            raise PaceError("bad_args", "give at least one of --name, --phase, --start, --end, --focus")
        with plan.transaction() as data:
            return Result({"block": plan.set_block(data, block_id, changes)})

    run("plan block set", {"id": block_id, "name": name, "phase": phase, "start": start, "end": end,
                           "focus": focus}, work)


@block_app.command("remove")
def block_remove(block_id: str = typer.Argument(..., metavar="ID")) -> None:
    """Delete a block no session uses."""
    async def work() -> Result:
        with plan.transaction() as data:
            return Result({"removed": plan.remove_block(data, block_id)})

    run("plan block remove", {"id": block_id}, work)


@app.command("add")
def add(file: str = typer.Argument(..., metavar="FILE", help="JSON file, or - for stdin: one session or a list.")) -> None:
    """Add sessions (all or nothing)."""
    async def work() -> Result:
        items = read_json(file)
        with plan.transaction() as data:
            return Result({"sessions": plan.add_sessions(data, items)})

    run("plan add", {"file": file}, work)


@app.command("set")
def set_(session_id: int = typer.Argument(..., metavar="ID"),
         on: str | None = typer.Option(None, "--date"),
         title: str | None = typer.Option(None, "--title"),
         purpose: str | None = typer.Option(None, "--purpose"),
         intensity: str | None = typer.Option(None, "--intensity", help=f"One of {', '.join(plan.INTENSITIES)}."),
         sport: str | None = typer.Option(None, "--sport"),
         block: str | None = typer.Option(None, "--block", help="Block id." + _NONE_HELP),
         duration: str | None = typer.Option(None, "--duration", help="45s, 50m, 1h10m." + _NONE_HELP),
         distance: str | None = typer.Option(None, "--distance", help="400m, 12km." + _NONE_HELP),
         elevation: str | None = typer.Option(None, "--elevation", help="Meters." + _NONE_HELP),
         notes: str | None = typer.Option(None, "--notes"),
         status: str | None = typer.Option(None, "--status", help=f"One of {', '.join(plan.SETTABLE_STATUSES)}."),
         activity: str | None = typer.Option(None, "--activity", help="Garmin activity id." + _NONE_HELP),
         workout: str | None = typer.Option(None, "--workout", help="Workout DSL file, - for stdin." + _NONE_HELP)) -> None:
    """Change a session; only the given fields change."""
    async def work() -> Result:
        changes: dict[str, Any] = {k: v for k, v in (("date", on), ("title", title), ("purpose", purpose),
                                                     ("intensity", intensity), ("sport", sport), ("notes", notes),
                                                     ("status", status)) if v is not None}
        target: dict[str, Any] = {}
        for key, value, parse in (("duration_s", duration, lambda t: parse_duration(t, "--duration")),
                                  ("distance_m", distance, lambda t: parse_distance(t, "--distance")),
                                  ("elevation_m", elevation, _float)):
            given, parsed = _clearable(value, parse)
            if given:
                target[key] = parsed
        if target:
            changes["target"] = target
        for key, value, parse in (("block", block, str), ("activity_id", activity, _int),
                                  ("workout", workout, read_json)):
            given, parsed = _clearable(value, parse)
            if given:
                changes[key] = parsed
        if not changes:
            raise PaceError("bad_args", "give at least one field to change")
        with plan.transaction() as data:
            return Result({"session": plan.set_session(data, session_id, changes)})

    run("plan set", {"id": session_id, "date": on, "title": title, "purpose": purpose, "intensity": intensity,
                     "sport": sport, "block": block, "duration": duration, "distance": distance,
                     "elevation": elevation, "notes": notes, "status": status, "activity": activity,
                     "workout": workout}, work)


@app.command("remove")
def remove(ids: list[int] = typer.Argument(..., metavar="ID...")) -> None:
    """Delete sessions; refused for any with a Garmin workout (unpush first)."""
    async def work() -> Result:
        with planpush.push_lock(), plan.transaction() as data:
            return Result({"removed": plan.remove_sessions(data, ids)})

    run("plan remove", {"ids": ids}, work)


@app.command("push")
def push(ids: list[int] = typer.Argument(..., metavar="ID..."),
         force: bool = typer.Option(False, "--force", help="Re-push sessions already pushed and unchanged."),
         dry_run: bool = DRY_RUN_OPT) -> None:
    """Upload and schedule sessions on Garmin; unchanged pushed sessions are skipped."""
    async def work() -> Result:
        return Result(await planpush.push_sessions(ids, dry_run=dry_run, force=force, today=date.today()))

    run("plan push", {"ids": ids, "force": force, "dry_run": dry_run}, work)


@app.command("unpush")
def unpush(ids: list[int] = typer.Argument(..., metavar="ID..."), dry_run: bool = DRY_RUN_OPT) -> None:
    """Delete the sessions' workouts from Garmin; the sessions stay, planned."""
    async def work() -> Result:
        return Result(await planpush.unpush_sessions(ids, dry_run=dry_run, today=date.today()))

    run("plan unpush", {"ids": ids, "dry_run": dry_run}, work)
