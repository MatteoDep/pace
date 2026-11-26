"""`pace load`: weekly and daily training load (actual, planned, projected, rolling 7 days)."""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import typer

from pace import load as load_mod
from pace import matching
from pace import plan as plan_store
from pace.cache import Cache, CacheTracker
from pace.envelope import PaceError, Result, run
from pace.factory import NO_CACHE_OPT
from pace.store import save_json

SPORTS = ("running", "all")
_M = (("km", "km"), ("elev_m", "elev_m"), ("hours", "hours"))


def _flat(prefix: str, vals: dict | None) -> dict[str, Any]:
    return {f"{prefix}_{k}": (vals[k] if vals else None) for k, _ in _M}


def _week_rows(data: dict, today: date) -> list[dict]:
    rows = []
    for w in data["weeks"]:
        start = date.fromisoformat(w["start"])
        proj, act = w["projected"], w["actual"]
        if start + timedelta(days=6) < today:
            rem = dict.fromkeys(proj, 0.0)
        elif start <= today:
            rem = {k: round(proj[k] - act[k], 2) for k in proj}
        else:
            rem = proj
        planned = w["planned"]
        rows.append({"table": "week", "week": w["week"], "start": w["start"], "sport": data["sport"],
                     **_flat("actual", act), **_flat("planned", planned), **_flat("projected", proj),
                     **_flat("remaining", rem), **_flat("missing", planned["missing"] if planned else None)})
    return rows


def _day_rows(data: dict, today: date) -> list[dict]:
    return [{"table": "day", "date": d["date"], "sport": data["sport"], "projected": d["date"] > today.isoformat(),
             **_flat("rolling7", d["rolling7"])} for d in data["days"]]


def _next_row(sessions: list[dict], acts: list[dict], today: date) -> dict | None:
    iso = today.isoformat()
    cand = sorted((s for s in sessions if s["date"] >= iso and s["status"] != "skipped"
                   and s["intensity"] != "rest"), key=lambda s: (s["date"], s["id"]))
    done_today: set[int] = set()
    todays = [s for s in sessions if s["date"] == iso]
    if todays:
        res = matching.match(todays, [a for a in acts if str(a.get("startTimeLocal"))[:10] == iso], today=today)
        done_today = {s["id"] for d in res["days"] for s in d["sessions"] if s["status"] in ("done", "modified")}
    for s in cand:
        if s["status"] == "done" or s["id"] in done_today:
            continue
        t = s.get("target") or {}
        return {"table": "next", "date": s["date"], "title": s["title"], "sport": s["sport"],
                "intensity": s["intensity"], "duration_s": t.get("duration_s"), "distance_m": t.get("distance_m"),
                "status": s["status"]}
    return None


def _dashboard_rows(acts: list[dict], sessions: list[dict], plan_data: dict, *, start: date, end: date,
                    today: date) -> list[dict]:
    rows: list[dict] = []
    for sp in SPORTS:
        d = load_mod.aggregate(acts, sessions, start=start, end=end, today=today, sport=sp)
        rows += _week_rows(d, today) + _day_rows(d, today)
    nxt = _next_row(plan_data.get("sessions", []), acts, today)
    return rows + [nxt] if nxt else rows


def load(weeks: int = typer.Option(16, "--weeks", help="Whole weeks back, this week included (>= 1)."),
         weeks_ahead: int | None = typer.Option(None, "--weeks-ahead",
                                                help="Weeks after this one; default: through the plan's last session."),
         sport_opt: str | None = typer.Option(None, "--sport", help="running (default) or all."),
         export: Path | None = typer.Option(None, "--export", help="Write dashboard rows (both sports) to this JSON file."),
         rows_opt: bool = typer.Option(False, "--rows", help="Return the dashboard rows (both sports) as data."),
         no_cache: bool = NO_CACHE_OPT) -> None:
    """Weekly and daily load: km, elevation and hours; actual, planned, projected and a rolling 7-day sum."""
    sport = sport_opt or "running"

    async def work() -> Result:
        if rows_opt and export is not None:
            raise PaceError("bad_args", "--rows cannot be combined with --export")
        if (export is not None or rows_opt) and sport_opt is not None:
            raise PaceError("bad_args", "--sport cannot be combined with --export or --rows (both sports are used)")
        if sport not in SPORTS:
            raise PaceError("bad_args", f"--sport must be one of {', '.join(SPORTS)}")
        if weeks < 1:
            raise PaceError("bad_args", "--weeks must be >= 1")
        if weeks_ahead is not None and weeks_ahead < 0:
            raise PaceError("bad_args", "--weeks-ahead must be >= 0")
        today = date.today()
        plan_data = plan_store.load()
        start, end = load_mod.default_range(plan_data, today=today, weeks=weeks, weeks_ahead=weeks_ahead)
        cache = Cache()
        tracker = CacheTracker()
        try:
            acts, sessions = await load_mod.fetch_inputs(start, end, today=today, cache=cache, tracker=tracker,
                                                         refresh=no_cache)
        finally:
            cache.close()
        if rows_opt:
            return Result(_dashboard_rows(acts, sessions, plan_data, start=start, end=end, today=today),
                          tracker.report())
        if export is not None:
            rows = _dashboard_rows(acts, sessions, plan_data, start=start, end=end, today=today)
            meta = [{"label": "Generated", "value": datetime.now().astimezone().isoformat(timespec="seconds")},
                    {"label": "Command", "value": "pace load --export"}]
            save_json(export, {"rows": rows, "meta": meta})
            return Result({"path": str(export), "rows": len(rows)}, tracker.report())
        data = load_mod.aggregate(acts, sessions, start=start, end=end, today=today, sport=sport)
        return Result(data, tracker.report())

    run("load", {"weeks": weeks, "weeks_ahead": weeks_ahead, "sport": None if export is not None or rows_opt else sport}, work)
