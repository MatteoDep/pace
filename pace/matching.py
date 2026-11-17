"""Match plan sessions to Garmin activities: pure functions, `today` is passed in."""

from datetime import date
from typing import Any

from pace.plan import MODIFIED_TOLERANCE
from pace.sports import UNTRACKED, family


def _type(a: dict[str, Any]) -> str | None:
    t = a.get("activityType")
    return t.get("typeKey") if isinstance(t, dict) else t


def _duration(a: dict[str, Any]) -> float:
    return a.get("movingDuration") or a.get("duration") or 0


def _day(a: dict[str, Any]) -> str:
    return str(a["startTimeLocal"])[:10]


def _zones(a: dict[str, Any]) -> list[Any]:
    return [a.get(f"hrTimeInZone_{i}") for i in range(1, 6)]


def _pace(a: dict[str, Any]) -> float | None:
    speed = a.get("averageSpeed")
    return 1000 / speed if speed and speed > 0 else None


def _unplanned(a: dict[str, Any], effort: dict[int, dict]) -> dict[str, Any]:
    e = effort.get(a["activityId"], {})
    return {"activity_id": a["activityId"], "type": _type(a), "sport": family(_type(a)),
            "duration_s": _duration(a), "distance_m": a.get("distance"), "avg_hr": a.get("averageHR"), "hr_zones_s": _zones(a),
            "rpe": e.get("rpe"), "feel": e.get("feel")}


def _actual(a: dict[str, Any], effort: dict[int, dict]) -> dict[str, Any]:
    e = effort.get(a["activityId"], {})
    return {"activity_id": a["activityId"], "type": _type(a), "duration_s": _duration(a),
            "distance_m": a.get("distance"), "elevation_m": a.get("elevationGain"),
            "avg_pace_s_per_km": _pace(a), "avg_hr": a.get("averageHR"), "hr_zones_s": _zones(a),
            "rpe": e.get("rpe"), "feel": e.get("feel")}


def _stub(activity_id: int) -> dict[str, Any]:
    """A manual link to an activity outside the fetched rows: only the id is known."""
    return {"activity_id": activity_id, "type": None, "duration_s": None, "distance_m": None,
            "elevation_m": None, "avg_pace_s_per_km": None, "avg_hr": None, "hr_zones_s": None,
            "rpe": None, "feel": None}


def _differs(target: float | None, actual: float | None) -> bool:
    return bool(target) and actual is not None and abs(actual - target) / target > MODIFIED_TOLERANCE


def _diff(s: dict[str, Any], a: dict[str, Any]) -> float:
    t = s["target"]
    if t.get("duration_s"):
        return abs(_duration(a) - t["duration_s"])
    if t.get("distance_m"):
        return abs((a.get("distance") or 0) - t["distance_m"])
    return 0


def _pair(sessions: list[dict[str, Any]], activities: list[dict[str, Any]]) -> dict[int, dict[str, Any] | None]:
    """session id -> activity row, or None when the session has a manual link outside the rows."""
    by_id = {a["activityId"]: a for a in activities}
    used: set[int] = set()
    pairs: dict[int, dict[str, Any] | None] = {}
    for s in sessions:  # 1. manual link always wins and never falls back
        aid = s.get("activity_id")
        if aid is not None:
            a = by_id.get(aid)
            pairs[s["id"]] = a if a is not None and aid not in used else None
            used.add(aid)
    for s in sessions:  # 2. the workout the activity was started from
        wid = (s.get("garmin") or {}).get("workout_id")
        if s["id"] in pairs or wid is None:
            continue
        for a in sorted(activities, key=lambda a: (a["startTimeLocal"], a["activityId"])):
            if a["activityId"] not in used and a.get("workoutId") == wid:
                pairs[s["id"]] = a
                used.add(a["activityId"])
                break
    candidates = sorted(
        ((_diff(s, a), s["id"], a["startTimeLocal"], a["activityId"], s, a)
         for s in sessions if s["id"] not in pairs for a in activities
         if a["activityId"] not in used and _day(a) == s["date"] and family(_type(a)) == s["sport"]),
        key=lambda c: c[:4])
    for _, sid, _, aid, _, a in candidates:  # 3. closest pairing on the same day and sport family
        if sid not in pairs and aid not in used:
            pairs[sid] = a
            used.add(aid)
    return pairs


def _unmatched_status(s: dict[str, Any], today: date) -> str:
    if s["status"] in ("done", "skipped"):
        return s["status"]
    if s["intensity"] == "rest":
        return "rest"
    if s["date"] < today.isoformat():
        return "unconfirmed" if s["sport"] in UNTRACKED else "missed"
    return s["status"]


def match(sessions: list[dict[str, Any]], activities: list[dict[str, Any]], *, today: date,
          effort: dict[int, dict] | None = None) -> dict[str, Any]:
    effort = effort or {}
    sessions = sorted(sessions, key=lambda s: (s["date"], s["id"]))
    pairs = _pair(sessions, activities)
    matched_ids = {a["activityId"] for a in pairs.values() if a is not None}
    days: dict[str, dict[str, Any]] = {}

    def day(d: str) -> dict[str, Any]:
        return days.setdefault(d, {"date": d, "sessions": [], "unplanned": []})

    totals: dict[str, dict[str, dict[str, float]]] = {}

    def total(sport: str) -> dict[str, dict[str, float]]:
        return totals.setdefault(sport, {"planned": {"duration_s": 0, "distance_m": 0},
                                         "actual": {"duration_s": 0, "distance_m": 0}})

    intensity_s: dict[str, float] = {}
    missing = 0
    for s in sessions:
        t = s["target"]
        if s["id"] in pairs:
            a = pairs[s["id"]]
            actual = _actual(a, effort) if a is not None else _stub(s["activity_id"])
            modified = a is not None and (_differs(t.get("duration_s"), actual["duration_s"])
                                          or _differs(t.get("distance_m"), actual["distance_m"]))
            status = "modified" if modified else "done"
        else:
            actual, status = None, _unmatched_status(s, today)
        day(s["date"])["sessions"].append(
            {"id": s["id"], "title": s["title"], "sport": s["sport"], "intensity": s["intensity"],
             "status": status, "target": t, "stale": s.get("stale", False), "actual": actual})
        if s["status"] == "skipped" or s["intensity"] == "rest":
            continue
        planned = total(s["sport"])["planned"]
        planned["duration_s"] += t.get("duration_s") or 0
        planned["distance_m"] += t.get("distance_m") or 0
        if t.get("duration_s"):
            intensity_s[s["intensity"]] = intensity_s.get(s["intensity"], 0) + t["duration_s"]
        else:
            missing += 1
    for a in sorted(activities, key=lambda a: (a["startTimeLocal"], a["activityId"])):
        actual_total = total(family(_type(a)))["actual"]
        actual_total["duration_s"] += _duration(a)
        actual_total["distance_m"] += a.get("distance") or 0
        if a["activityId"] not in matched_ids:
            day(_day(a))["unplanned"].append(_unplanned(a, effort))
    return {"days": [days[d] for d in sorted(days)], "totals": totals, "intensity_planned_s": intensity_s,
            "intensity_missing": missing}
