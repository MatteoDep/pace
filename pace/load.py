"""Weekly and daily training load: activities and plan sessions aggregated into week and day rows."""

from datetime import date, timedelta
from typing import Any

from pace import plan as plan_store
from pace.cache import Cache
from pace.commands._helpers import cached_call
from pace.sports import family

METRICS = ("km", "elev_m", "hours")
MAX_WEEKS_AHEAD = 26
_ACTIVITIES_TTL = 600


def week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _zero() -> dict[str, float]:
    return dict.fromkeys(METRICS, 0.0)


def _add(into: dict[str, float], other: dict[str, float]) -> None:
    for m in METRICS:
        into[m] += other[m]


def _round(v: dict[str, float]) -> dict[str, float]:
    return {m: round(v[m], 2) for m in METRICS}


def _type_key(a: dict) -> str | None:
    t = a.get("activityType")
    return t.get("typeKey") if isinstance(t, dict) else t


def _activity_values(a: dict) -> dict[str, float]:
    moving = a.get("movingDuration") or 0
    secs = moving if moving > 0 else (a.get("duration") or 0)
    return {"km": (a.get("distance") or 0) / 1000, "elev_m": float(a.get("elevationGain") or 0),
            "hours": secs / 3600}


def _session_values(s: dict) -> tuple[dict[str, float], dict[str, int]]:
    t = s.get("target") or {}
    parts = {"km": t.get("distance_m"), "elev_m": t.get("elevation_m"), "hours": t.get("duration_s")}
    scale = {"km": 1000, "elev_m": 1, "hours": 3600}
    vals = {m: (parts[m] or 0) / scale[m] for m in METRICS}
    return vals, {m: int(parts[m] is None) for m in METRICS}


def aggregate(activities: list[dict], sessions: list[dict], *, start: date, end: date, today: date,
              sport: str) -> dict:
    """Week and day rows for start..end. Inputs may reach 6 days before start (rolling7 of the first rows)."""
    actual: dict[date, dict[str, float]] = {}
    for a in activities:
        if sport != "all" and family(_type_key(a)) != sport:
            continue
        day = date.fromisoformat((a.get("startTimeLocal") or "")[:10])
        _add(actual.setdefault(day, _zero()), _activity_values(a))
    planned: dict[date, dict[str, float]] = {}
    missing: dict[date, dict[str, int]] = {}
    for s in sessions:
        if s.get("status") == "skipped" or s.get("intensity") == "rest":
            continue
        if sport != "all" and s.get("sport") != sport:
            continue
        day = date.fromisoformat(s["date"])
        vals, miss = _session_values(s)
        _add(planned.setdefault(day, _zero()), vals)
        m = missing.setdefault(day, dict.fromkeys(METRICS, 0))
        for k in METRICS:
            m[k] += miss[k]

    def effective(d: date) -> dict[str, float]:
        if d < today or (d == today and d in actual):
            return actual.get(d, _zero())
        return planned.get(d, _zero())

    days: list[dict] = []
    weeks: dict[str, dict] = {}
    window: list[dict[str, float]] = [effective(start - timedelta(days=i)) for i in range(6, 0, -1)]
    d = start
    while d <= end:
        eff = effective(d)
        window.append(eff)
        roll = _zero()
        for v in window[-7:]:
            _add(roll, v)
        days.append({
            "date": d.isoformat(),
            "actual": _round(actual.get(d, _zero())) if d <= today else None,
            "planned": {**_round(planned[d]), "missing": missing[d]} if d in planned else None,
            "effective": _round(eff), "rolling7": _round(roll),
        })
        mon = _monday(d)
        w = weeks.setdefault(week_key(d), {"start": mon, "actual": _zero(), "planned": _zero(),
                                           "missing": dict.fromkeys(METRICS, 0), "projected": _zero()})
        if d <= today:
            _add(w["actual"], actual.get(d, _zero()))
        if d in planned:
            _add(w["planned"], planned[d])
            for k in METRICS:
                w["missing"][k] += missing[d][k]
        _add(w["projected"], eff)
        d += timedelta(days=1)

    rows = []
    for key, w in weeks.items():
        sunday = w["start"] + timedelta(days=6)
        rows.append({
            "week": key, "start": w["start"].isoformat(),
            "actual": _round(w["actual"]) if w["start"] <= today else None,
            "planned": {**_round(w["planned"]), "missing": w["missing"]} if sunday >= today else None,
            "projected": _round(w["projected"]),
        })
    return {"weeks": rows, "days": days, "range": {"from": start.isoformat(), "to": end.isoformat()},
            "sport": sport, "today": today.isoformat()}


def default_range(plan_data: dict, *, today: date, weeks: int, weeks_ahead: int | None) -> tuple[date, date]:
    this_week = _monday(today)
    start = this_week - timedelta(weeks=weeks - 1)
    if weeks_ahead is not None:
        last = this_week + timedelta(weeks=weeks_ahead)
    else:
        dates = [s["date"] for s in plan_data.get("sessions", [])]
        last = max(this_week, _monday(date.fromisoformat(max(dates)))) if dates else this_week
    last = min(last, this_week + timedelta(weeks=MAX_WEEKS_AHEAD))
    return start, last + timedelta(days=6)


async def fetch_inputs(start: date, end: date, *, today: date, cache: Cache, tracker: Any,
                       refresh: bool) -> tuple[list[dict], list[dict]]:
    """(activities, sessions) for start-6..end; one cached Garmin call, none when the range is all future."""
    first = start - timedelta(days=6)
    activities: list[dict] = []
    if first <= today:
        rows = await cached_call("get_activities_by_date", first.isoformat(), min(end, today).isoformat(), None,
                                 cache=cache, ttl=_ACTIVITIES_TTL, tracker=tracker, refresh=refresh)
        for a in rows or []:
            if a.get("activityId") is None:
                continue
            activities.append({**a, "activityType": _type_key(a)})
    return activities, plan_store.sessions_in(plan_store.load(), first, end)
