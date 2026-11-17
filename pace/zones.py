"""Thresholds and HR/pace zones: record store, per-threshold current, derivation, due rule.

Stored at $PACE_HOME/zones.json. Pure module: no Garmin calls. Methods: docs/superpowers/specs/2026-11-17-zones-methods.md.
"""

import copy
import math
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from pace import config, store
from pace.envelope import PaceError
from pace.rules import HARD_EFFORT_TE, HR_MAX, HR_MIN

SOURCES = ("estimate", "field_test", "lab")
THRESHOLDS = ("lt1", "lt2")
CONFIDENCES = ("low", "medium", "high")
LAB_VALID_DAYS = 183
ZONE_RULE = "lt-5z-v1"
ESTIMATE_EVERY_DAYS = 28
FIELD_TEST_EVERY_DAYS = 84
BREAK_DAYS = 14
RETRAIN_DAYS = 14
HR_DRIFT_BPM = 4
PACE_DRIFT_FRAC = 0.03
H2_RANGE_SLACK = 0.10
OUTCOMES = ("usable", "not_monotonic", "one_sided", "insufficient_data")  # the last two leave the streak alone
PUSH_SPORTS = ("RUNNING", "DEFAULT")  # watch entry compared by status(); pinned in plan 5 Task 5c
FLOOR_KEYS = ("zone1Floor", "zone2Floor", "zone3Floor", "zone4Floor", "zone5Floor")

EMPTY: dict[str, Any] = {
    "records": [], "current": {"lt1": None, "lt2": None}, "next_id": 1, "estimates": [], "hr_max": None,
    "h4": {"streak": 0, "unreliable": False, "since": None, "counted": []},
}
_SHOW_HINT = "List ids with `pace zones show`."


def zones_path() -> Path:
    return config.PACE_HOME / "zones.json"


def R(x: float) -> int:
    """Round half up (methods doc 14.5); Python's round() rounds halves to even."""
    return math.floor(x + 0.5)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _bad(msg: str, hint: str | None = None) -> PaceError:
    return PaceError("bad_args", msg, hint=hint)


# --- store ---

def load() -> dict[str, Any]:
    path = zones_path()
    data = store.load_json(path, EMPTY, "zones")
    records, current, estimates, h4 = (data.get("records"), data.get("current"),
                                       data.get("estimates"), data.get("h4"))
    hr_max = data.get("hr_max")
    if (not isinstance(records, list) or not all(isinstance(r, dict) and _is_int(r.get("id")) for r in records)
            or not isinstance(current, dict) or not isinstance(estimates, list)
            or not all(isinstance(e, dict) for e in estimates)
            or not (hr_max is None or isinstance(hr_max, dict))
            or not (h4 is None or isinstance(h4, dict))):
        raise PaceError("internal", "zones has an unexpected shape",
                        hint=f"Inspect or move {path}; pace never overwrites it.")
    ids = {r["id"] for r in records}
    data["current"] = {t: (current.get(t) if current.get(t) in ids else None) for t in THRESHOLDS}
    data["hr_max"] = hr_max
    data["h4"] = {**copy.deepcopy(EMPTY["h4"]), **(h4 or {})}
    next_id = data.get("next_id")
    data["next_id"] = max(next_id if _is_int(next_id) else 1, max(ids, default=0) + 1)
    for r in records:
        for t in THRESHOLDS:
            r.setdefault(t, None)
        r.setdefault("details", {})
        r.setdefault("activities", [])
    return data


@contextmanager
def transaction() -> Iterator[dict[str, Any]]:
    """Locked read-modify-write; the file is saved only when the block exits cleanly."""
    path = zones_path()
    with store.locked(path):
        data = load()
        yield data
        store.save_json(path, data)


# --- records ---

def _has(record: dict[str, Any] | None, t: str) -> bool:
    v = (record or {}).get(t)
    return isinstance(v, dict) and (v.get("hr") is not None or v.get("pace_s_per_km") is not None)


def _hr(record: dict[str, Any] | None, t: str) -> int | None:
    return ((record or {}).get(t) or {}).get("hr")


def _pace(record: dict[str, Any] | None, t: str) -> float | None:
    return ((record or {}).get(t) or {}).get("pace_s_per_km")


def _check_hr(value: Any, what: str) -> int | None:
    if value is None:
        return None
    if not _is_int(value) or not HR_MIN <= value <= HR_MAX:
        raise _bad(f"{what} must be an integer between {HR_MIN} and {HR_MAX}, got {value!r}")
    return value


def _threshold(raw: Any, t: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _bad(f"{t} must be an object with hr and pace_s_per_km")
    hr = _check_hr(raw.get("hr"), f"{t} hr")
    pace = raw.get("pace_s_per_km")
    if pace is not None and (not _is_num(pace) or pace <= 0):
        raise _bad(f"{t} pace must be a positive number of s/km, got {pace!r}")
    return {"hr": hr, "pace_s_per_km": pace} if hr is not None or pace is not None else None


def _check_pair(lt1: dict[str, Any] | None, lt2: dict[str, Any] | None, max_hr: int | None = None) -> None:
    a, b = _hr(lt1, "lt1"), _hr(lt2, "lt2")
    if a is not None and b is not None and a >= b:
        raise _bad(f"LT1 HR ({a}) must be below LT2 HR ({b})")
    top = b if b is not None else a
    if max_hr is not None and top is not None and top >= max_hr:
        raise _bad(f"max HR ({max_hr}) must be above the LT1 and LT2 HR")
    p1, p2 = _pace(lt1, "lt1"), _pace(lt2, "lt2")
    if p1 is not None and p2 is not None and p1 <= p2:
        raise _bad(f"LT1 pace ({p1} s/km) must be slower than LT2 pace ({p2} s/km)")


def _by_id(data: dict[str, Any], record_id: int) -> dict[str, Any]:
    for r in data["records"]:
        if r["id"] == record_id:
            return r
    raise PaceError("not_found", f"no zone record with id {record_id}", hint=_SHOW_HINT)


def _current_record(data: dict[str, Any], t: str) -> dict[str, Any] | None:
    rid = data["current"].get(t)
    return next((r for r in data["records"] if r["id"] == rid), None) if rid is not None else None


def current(data: dict[str, Any]) -> dict[str, Any]:
    return {t: _current_record(data, t) for t in THRESHOLDS}


def _pair_with(data: dict[str, Any], record: dict[str, Any], ts: list[str]) -> None:
    """Validate the current pair that results from making `record` current for `ts`."""
    cur = current(data)
    for t in ts:
        cur[t] = record
    _check_pair(cur["lt1"], cur["lt2"])


def add_record(data: dict[str, Any], record: dict[str, Any], *, today: date) -> dict[str, Any]:
    """Validate and store a record; a lab record becomes current per threshold when not older."""
    source = record.get("source")
    if source not in SOURCES:
        raise _bad(f"source must be one of {', '.join(SOURCES)}, got {source!r}")
    try:
        day = date.fromisoformat(str(record.get("date")))
    except ValueError:
        raise _bad(f"invalid date {record.get('date')!r}; use YYYY-MM-DD") from None
    if day > today:
        raise _bad(f"date {day.isoformat()} is in the future")
    lt1, lt2 = _threshold(record.get("lt1"), "lt1"), _threshold(record.get("lt2"), "lt2")
    if lt1 is None and lt2 is None:
        raise _bad("a record needs at least one threshold value (lt1 or lt2, hr or pace)")
    max_hr = _check_hr(record.get("max_hr"), "max_hr")
    rest_hr = _check_hr(record.get("rest_hr"), "rest_hr")
    _check_pair({"lt1": lt1}, {"lt2": lt2}, max_hr)
    confidence = record.get("confidence")
    if confidence is not None and confidence not in CONFIDENCES:
        raise _bad(f"confidence must be one of {', '.join(CONFIDENCES)}")
    stored = {
        "id": data["next_id"], "date": day.isoformat(), "source": source, "method": record.get("method"),
        "lt1": lt1, "lt2": lt2, "max_hr": max_hr, "rest_hr": rest_hr, "cs_mps": record.get("cs_mps"),
        "d_prime_m": record.get("d_prime_m"), "activities": list(record.get("activities") or []),
        "confidence": confidence, "notes": record.get("notes"), "details": dict(record.get("details") or {}),
    }
    auto: list[str] = []
    if source == "lab":
        for t in THRESHOLDS:
            if stored[t] is None:
                continue
            cur = _current_record(data, t)
            if cur is None or day.isoformat() >= cur["date"]:
                auto.append(t)
        _pair_with(data, stored, auto)
    data["records"].append(stored)
    data["next_id"] += 1
    for t in auto:
        data["current"][t] = stored["id"]
    if max_hr is not None:
        update_hr_max(data, max_hr, activity=None, day=day)
    return stored


def accept(data: dict[str, Any], record_id: int, *, only: str | None = None) -> dict[str, Any]:
    record = _by_id(data, record_id)
    if only is not None and only not in THRESHOLDS:
        raise _bad(f"--only must be one of {', '.join(THRESHOLDS)}")
    have = [t for t in THRESHOLDS if _has(record, t)]
    if only is not None:
        if only not in have:
            raise _bad(f"record {record_id} has no {only} value")
        have = [only]
    _pair_with(data, record, have)
    for t in have:
        data["current"][t] = record_id
    return record


def remove(data: dict[str, Any], record_id: int, *, force: bool) -> dict[str, Any]:
    record = _by_id(data, record_id)
    held = [t for t in THRESHOLDS if data["current"].get(t) == record_id]
    if held and not force:
        raise _bad(f"record {record_id} is current for {', '.join(held)}", hint="Use --force to remove it anyway.")
    for t in held:
        data["current"][t] = None
    data["records"].remove(record)
    return record


# --- derivation ---

def h2_of(record_lt1: dict[str, Any] | None, record_lt2: dict[str, Any] | None) -> dict[str, Any] | None:
    """The H2 regression: the LT1 record's details, else the LT2 record's (methods doc 14.5)."""
    for rec in (record_lt1, record_lt2):
        h2 = ((rec or {}).get("details") or {}).get("h2")
        if h2:
            return h2
    return None


def _pace_at_hr(hr: int, h2: dict[str, Any] | None) -> int | None:
    if not h2 or not h2.get("b"):
        return None
    v = (hr - h2["a"]) / h2["b"]
    if v <= 0 or v < (1 - H2_RANGE_SLACK) * h2["v_min"] or v > (1 + H2_RANGE_SLACK) * h2["v_max"]:
        return None
    return R(1000 / v)


def derive(record_lt1: dict[str, Any] | None, record_lt2: dict[str, Any] | None, *,
           h2: dict[str, Any] | None, hr_max: int | None) -> dict[str, Any]:
    """Zones per methods doc 14.5 (rule lt-5z-v1); missing inputs give None pieces.

    Three-zone entries: HR {"zone", "hr_min", "hr_max"} (inclusive bounds); pace
    {"zone", "slower_than", "not_slower_than"} in s/km (Z1 slower than LT1 pace, Z2 between, Z3 at or faster than LT2 pace).
    """
    h1, h2hr = _hr(record_lt1, "lt1"), _hr(record_lt2, "lt2")
    p1, p2 = _pace(record_lt1, "lt1"), _pace(record_lt2, "lt2")
    three_hr = None
    floors = None
    if h1 is not None and h2hr is not None:
        three_hr = [{"zone": 1, "hr_min": None, "hr_max": h1 - 1},
                    {"zone": 2, "hr_min": h1, "hr_max": h2hr - 1},
                    {"zone": 3, "hr_min": h2hr, "hr_max": None}]
        top = R(0.5 * (hr_max - h2hr)) if hr_max is not None else R(0.5 * (h2hr - h1))
        floors = {"zone1Floor": R(0.80 * h1), "zone2Floor": R(0.90 * h1), "zone3Floor": h1,
                  "zone4Floor": h2hr, "zone5Floor": h2hr + top, "maxHeartRateUsed": hr_max}
    three_pace = None
    if p1 is not None and p2 is not None:
        three_pace = [{"zone": 1, "slower_than": R(p1), "not_slower_than": None},
                      {"zone": 2, "slower_than": R(p2), "not_slower_than": R(p1)},
                      {"zone": 3, "slower_than": None, "not_slower_than": R(p2)}]
    cs, dp = (record_lt2 or {}).get("cs_mps"), (record_lt2 or {}).get("d_prime_m")
    z5 = R(1000 / (cs + dp / 720)) if cs and dp is not None else None
    pace_floors = {
        "z1": _pace_at_hr(floors["zone1Floor"], h2) if floors else None,
        "z2": _pace_at_hr(floors["zone2Floor"], h2) if floors else None,
        "z3": R(p1) if p1 is not None else None,
        "z4": R(p2) if p2 is not None else None,
        "z5": z5,
    }
    return {"rule": ZONE_RULE, "three_hr": three_hr, "three_pace": three_pace, "floors": floors,
            "pace_floors": pace_floors, "floor_errors": floor_errors(floors, hr_max)}


def floor_errors(floors: dict[str, Any] | None, hr_max: int | None) -> list[str]:
    """Methods doc 14.5 validation: the five floors strictly increasing, zone5Floor below HRmax when known."""
    if not floors:
        return []
    errors = [f"{a} {floors[a]} is not below {b} {floors[b]} (floors must be strictly increasing)"
              for a, b in zip(FLOOR_KEYS, FLOOR_KEYS[1:]) if floors[a] >= floors[b]]
    if hr_max is not None and floors["zone5Floor"] >= hr_max:
        errors.append(f"zone5Floor {floors['zone5Floor']} is not below max HR {hr_max}")
    return errors


# --- logs and memory ---

def log_estimate(data: dict[str, Any], summary: dict[str, Any]) -> None:
    data["estimates"].append(dict(summary))


def update_hr_max(data: dict[str, Any], bpm: int, *, activity: int | None, day: date) -> bool:
    """Raise the running HRmax; it never goes down. True when raised."""
    cur = data.get("hr_max")
    if cur is not None and bpm <= cur["bpm"]:
        return False
    data["hr_max"] = {"bpm": bpm, "activity": activity, "date": day.isoformat()}
    return True


def record_alpha1_outcome(data: dict[str, Any], outcome: str, *, day: date, activity: int | None = None) -> None:
    """H4 memory (methods doc 6): each activity counts at most once; two not_monotonic in a row -> unreliable.

    `insufficient_data` judged nothing: it changes nothing and does not mark the activity as counted."""
    if outcome not in OUTCOMES:
        raise _bad(f"outcome must be one of {', '.join(OUTCOMES)}, got {outcome!r}")
    if outcome == "insufficient_data":
        return
    h4 = data["h4"]
    if activity is not None:
        if activity in h4["counted"]:
            return
        h4["counted"].append(activity)
    if outcome == "usable":
        h4["streak"] = 0
    elif outcome == "not_monotonic":
        h4["streak"] += 1
        if h4["streak"] >= 2 and not h4["unreliable"]:
            h4["unreliable"] = True
            h4["since"] = day.isoformat()


def reset_h4(data: dict[str, Any]) -> None:
    """`zones estimate --retry-h4`: clear the mark, the streak and the counted ids (every run is judged again)."""
    data["h4"].update(streak=0, unreliable=False, since=None, counted=[])


# --- due rule (methods doc 14.6) ---

def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _last_estimates(data: dict[str, Any], n: int) -> list[dict[str, Any]]:
    return [e for e in data["estimates"] if not e.get("partial")][-n:]


def last_complete_estimate_day(data: dict[str, Any]) -> date | None:
    """Date of the newest non-partial entry of `estimates`, or None."""
    last = _last_estimates(data, 1)
    return _day(last[0].get("date")) if last else None


def _estimate_reasons(data: dict[str, Any], today: date, runs_since: list[dict[str, Any]]) -> list[str]:
    reasons = []
    last = _last_estimates(data, 1)
    last_day = _day(last[0].get("date")) if last else None
    if last_day is None:
        reasons.append("no complete estimate yet")
    elif (today - last_day).days >= ESTIMATE_EVERY_DAYS:
        reasons.append(f"estimate: last complete one is {(today - last_day).days} days old")
    # the run before the estimate is there for the break rule only: its race or effect was already estimated
    fresh = [r for r in runs_since if last_day is None or ((_day(r.get("startTimeLocal") or r.get("date")) or last_day) > last_day)]
    for run in fresh:
        if ((run.get("eventType") or {}).get("typeKey") == "race"):
            reasons.append("estimate: a race since the last estimate")
            break
    if any((run.get("aerobicTrainingEffect") or 0) >= HARD_EFFORT_TE for run in fresh):
        reasons.append(f"estimate: a run with aerobic training effect >= {HARD_EFFORT_TE} since the last estimate")
    days = sorted(d for d in (_day(r.get("startTimeLocal") or r.get("date")) for r in runs_since) if d)
    for prev, nxt in zip(days, days[1:], strict=False):
        if (nxt - prev).days >= BREAK_DAYS and (today - nxt).days >= RETRAIN_DAYS:
            reasons.append(f"estimate: {RETRAIN_DAYS} days of training after a break of {(nxt - prev).days} days")
            break
    return reasons


def _field_tests(data: dict[str, Any], t: str) -> list[dict[str, Any]]:
    return [r for r in data["records"] if r.get("source") == "field_test" and _has(r, t)]


def _differs(t: str, est: dict[str, Any], cur: dict[str, Any]) -> bool:
    e_hr, c_hr = est.get(f"{t}_hr"), _hr(cur, t)
    if e_hr is not None and c_hr is not None and abs(e_hr - c_hr) >= HR_DRIFT_BPM:
        return True
    if t == "lt2":
        e_p, c_p = est.get("lt2_pace"), _pace(cur, "lt2")
        if e_p is not None and c_p and abs(e_p - c_p) / c_p >= PACE_DRIFT_FRAC:
            return True
    return False


def _field_test_reasons(data: dict[str, Any], t: str, today: date) -> list[str]:
    """Rules tied to the current record (estimate without a field test, estimates drifting away) need one;
    the age of the latest field test does not."""
    cur = _current_record(data, t)
    reasons = []
    tests = _field_tests(data, t)
    if cur is not None and cur.get("source") == "estimate" and not tests:
        reasons.append(f"{t}: current record is a history estimate and no field test exists")
    if tests:
        latest = max(d for d in (_day(r["date"]) for r in tests) if d)
        age = (today - latest).days
        if age >= FIELD_TEST_EVERY_DAYS:
            reasons.append(f"{t}: latest field test is {age} days old (>= {FIELD_TEST_EVERY_DAYS})")
    last2 = _last_estimates(data, 2)
    if cur is not None and len(last2) == 2 and all(_differs(t, e, cur) for e in last2):
        reasons.append(f"{t}: the last two estimates both differ from the current record")
    return reasons


def due(data: dict[str, Any], *, today: date, runs_since: list[dict[str, Any]]) -> dict[str, Any]:
    """Which re-estimate or retest is due.

    `runs_since`: list rows of the runs after the last estimate plus the last run before it, so the detraining rule
    sees a break that started before the estimate.
    """
    est = _estimate_reasons(data, today, runs_since)
    fields = {t: _field_test_reasons(data, t, today) for t in THRESHOLDS}
    return {"estimate": bool(est), "field_test": {t: bool(fields[t]) for t in THRESHOLDS},
            "reasons": est + fields["lt1"] + fields["lt2"]}


# --- status ---

def provenance(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {k: record.get(k) for k in ("id", "source", "date", "method", "confidence")}


def _expired(record: dict[str, Any] | None, today: date) -> bool:
    if record is None or record.get("source") != "lab":
        return False
    return (today - date.fromisoformat(record["date"])).days > LAB_VALID_DAYS


def _garmin_entry(garmin_zones: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for sport in PUSH_SPORTS:
        for entry in garmin_zones or []:
            if entry.get("sport") == sport:
                return entry
    return None


def status(data: dict[str, Any], *, today: date, garmin_zones: list[dict[str, Any]] | None,
           runs_since: list[dict[str, Any]]) -> dict[str, Any]:
    cur = current(data)
    hr_max = (data.get("hr_max") or {}).get("bpm")
    derived = derive(cur["lt1"], cur["lt2"], h2=h2_of(cur["lt1"], cur["lt2"]), hr_max=hr_max)
    records = []
    candidates = []
    for r in sorted(data["records"], key=lambda r: (r["date"], r["id"]), reverse=True):
        held = [t for t in THRESHOLDS if data["current"].get(t) == r["id"]]
        records.append({**r, "current_for": held})
        newer = [t for t in THRESHOLDS if _has(r, t) and t not in held
                 and (cur[t] is None or r["date"] > cur[t]["date"])]
        if newer:
            candidates.append({**r, "candidate_for": newer})
    garmin = _garmin_entry(garmin_zones)
    floors = derived["floors"]
    in_sync = None
    if garmin is not None and floors is not None:
        in_sync = all(garmin.get(k) == floors[k] for k in FLOOR_KEYS)
    return {
        "current": {t: provenance(cur[t]) for t in THRESHOLDS},
        "expired": {t: _expired(cur[t], today) for t in THRESHOLDS},
        "zones": derived, "records": records, "candidates": candidates,
        "due": due(data, today=today, runs_since=runs_since),
        "hr_max": data.get("hr_max"), "h4": copy.deepcopy(data["h4"]),
        "garmin": garmin, "in_sync": in_sync,
    }


__all__ = ["R", "load", "transaction", "add_record", "accept", "remove", "current", "derive", "floor_errors", "h2_of",
           "provenance", "last_complete_estimate_day", "log_estimate", "update_hr_max", "record_alpha1_outcome", "reset_h4", "due", "status"]
