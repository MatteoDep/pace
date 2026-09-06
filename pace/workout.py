"""Compact workout DSL -> Garmin Connect workout JSON.

IDs and keys below were read from a real workout on the user's account
(2026-09-06); the garminconnect ConditionType constants are NOT used because
they disagree with Garmin for `distance` and `lap.button`.
"""

import re
from typing import Any

from pace.envelope import PaceError

SPORTS: dict[str, tuple[int, str]] = {
    "running": (1, "running"), "cycling": (2, "cycling"), "swimming": (3, "swimming"),
    "walking": (4, "walking"), "hiking": (7, "hiking"),
}
STEP_TYPES = {"warmup": 1, "cooldown": 2, "interval": 3, "recovery": 4, "rest": 5}
REPEAT_TYPE = 6
END = {"lap.button": 1, "time": 2, "distance": 3, "iterations": 7}
TARGET = {"no.target": 1, "power.zone": 2, "cadence.zone": 3, "heart.rate.zone": 4, "speed.zone": 5, "pace.zone": 6}
TARGET_KEYS = ("pace", "hr", "power", "cadence")
END_KEYS = ("duration", "distance", "open")

_DURATION = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
_DISTANCE = re.compile(r"^(\d+(?:\.\d+)?)\s*(m|km)$")
_PACE = re.compile(r"^(\d{1,2}):(\d{2})$")
_RANGE = re.compile(r"^(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?$")
_ZONE = re.compile(r"^z([1-5])$", re.IGNORECASE)


def _bad(path: str, why: str) -> PaceError:
    return PaceError("bad_args", f"{path}: {why}")


def parse_duration(text: str, path: str) -> int:
    m = _DURATION.match(str(text).strip()) if text else None
    if not m or not any(m.groups()):
        raise _bad(path, f"invalid duration {text!r}; use 45s, 10m, 1h, 1h30m")
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mi * 60 + s


def parse_distance(text: str, path: str) -> float:
    m = _DISTANCE.match(str(text).strip()) if text else None
    if not m:
        raise _bad(path, f"invalid distance {text!r}; use 400m, 1km, 5.5km")
    value, unit = float(m.group(1)), m.group(2)
    return value * 1000.0 if unit == "km" else value


def parse_pace(text: str, path: str) -> float:
    """min:sec per km -> m/s."""
    m = _PACE.match(str(text).strip())
    if not m:
        raise _bad(path, f"invalid pace {text!r}; use min:sec per km like 4:15")
    secs = int(m.group(1)) * 60 + int(m.group(2))
    if secs <= 0:
        raise _bad(path, "pace must be positive")
    return 1000.0 / secs


def parse_range(text: str, path: str, kind: str) -> tuple[float, float]:
    m = _RANGE.match(str(text).strip())
    if not m:
        raise _bad(path, f"invalid {kind} {text!r}; use low-high like 140-150")
    lo = float(m.group(1))
    hi = float(m.group(2)) if m.group(2) else lo
    if lo > hi:
        raise _bad(path, f"{kind} range low > high")
    return lo, hi


def _sport(dsl: dict, path: str) -> dict[str, Any]:
    key = dsl.get("sport")
    if key not in SPORTS:
        raise _bad(f"{path}sport", f"unknown sport {key!r}; one of {', '.join(SPORTS)}")
    sid, skey = SPORTS[key]
    return {"sportTypeId": sid, "sportTypeKey": skey, "displayOrder": sid}


def _target(step: dict, path: str) -> dict[str, Any]:
    tgt = step.get("target")
    if tgt is None:
        return {"targetType": _ttype("no.target"), "targetValueOne": None, "targetValueTwo": None, "zoneNumber": None}
    if not isinstance(tgt, dict):
        raise _bad(f"{path}.target", "must be an object")
    keys = [k for k in TARGET_KEYS if k in tgt]
    if len(keys) != 1:
        raise _bad(f"{path}.target", f"exactly one of {', '.join(TARGET_KEYS)}")
    kind = keys[0]
    raw = str(tgt[kind])
    if kind == "pace":
        parts = [p.strip() for p in raw.split("-")]
        if len(parts) not in (1, 2):
            raise _bad(f"{path}.target.pace", f"invalid pace range {raw!r}; use 4:15-4:25")
        speeds = sorted(parse_pace(p, f"{path}.target.pace") for p in parts)
        fast, slow = speeds[-1], speeds[0]
        return {"targetType": _ttype("pace.zone"), "targetValueOne": fast, "targetValueTwo": slow, "zoneNumber": None}
    if kind == "hr":
        z = _ZONE.match(raw.strip())
        if z:
            return {"targetType": _ttype("heart.rate.zone"), "targetValueOne": None, "targetValueTwo": None,
                    "zoneNumber": int(z.group(1))}
        if raw.strip().lower().startswith("z"):
            raise _bad(f"{path}.target.hr", f"invalid zone {raw!r}; use z1..z5")
        lo, hi = parse_range(raw, f"{path}.target.hr", "hr")
        return {"targetType": _ttype("heart.rate.zone"), "targetValueOne": lo, "targetValueTwo": hi, "zoneNumber": None}
    tkey = "power.zone" if kind == "power" else "cadence.zone"
    lo, hi = parse_range(raw, f"{path}.target.{kind}", kind)
    return {"targetType": _ttype(tkey), "targetValueOne": lo, "targetValueTwo": hi, "zoneNumber": None}


def _ttype(key: str) -> dict[str, Any]:
    return {"workoutTargetTypeId": TARGET[key], "workoutTargetTypeKey": key, "displayOrder": TARGET[key]}


def _end(step: dict, path: str) -> tuple[dict[str, Any], float]:
    keys = [k for k in END_KEYS if k in step]
    if len(keys) != 1:
        raise _bad(path, f"exactly one of {', '.join(END_KEYS)} is required")
    k = keys[0]
    if k == "duration":
        return _econd("time", True), float(parse_duration(step[k], f"{path}.duration"))
    if k == "distance":
        return _econd("distance", True), parse_distance(step[k], f"{path}.distance")
    if step[k] is not True:
        raise _bad(f"{path}.open", "must be true")
    return _econd("lap.button", True), 0.0


def _econd(key: str, displayable: bool) -> dict[str, Any]:
    return {"conditionTypeId": END[key], "conditionTypeKey": key, "displayOrder": END[key], "displayable": displayable}


def _steps(items: Any, path: str, order: list[int]) -> list[dict[str, Any]]:
    if not isinstance(items, list) or not items:
        raise _bad(path, "must be a non-empty list of steps")
    out: list[dict[str, Any]] = []
    for i, step in enumerate(items):
        p = f"{path}[{i}]"
        if not isinstance(step, dict):
            raise _bad(p, "must be an object")
        if "repeat" in step:
            n = step["repeat"]
            if not isinstance(n, int) or isinstance(n, bool) or n < 1:
                raise _bad(f"{p}.repeat", "must be an integer >= 1")
            order[0] += 1
            my_order = order[0]
            children = _steps(step.get("steps"), f"{p}.steps", order)
            out.append({
                "type": "RepeatGroupDTO", "stepOrder": my_order,
                "stepType": {"stepTypeId": REPEAT_TYPE, "stepTypeKey": "repeat", "displayOrder": REPEAT_TYPE},
                "numberOfIterations": n, "workoutSteps": children,
                "endCondition": _econd("iterations", False), "endConditionValue": float(n),
                "smartRepeat": False, "childStepId": None,
            })
            continue
        kind = step.get("type")
        if kind not in STEP_TYPES:
            raise _bad(f"{p}.type", f"unknown step type {kind!r}; one of {', '.join(STEP_TYPES)}")
        end, value = _end(step, p)
        order[0] += 1
        out.append({
            "type": "ExecutableStepDTO", "stepOrder": order[0],
            "stepType": {"stepTypeId": STEP_TYPES[kind], "stepTypeKey": kind, "displayOrder": STEP_TYPES[kind]},
            "endCondition": end, "endConditionValue": value,
            **_target(step, p),
            "description": step.get("note") or None, "childStepId": None,
        })
    return out


def _step_seconds(step: dict) -> float:
    if "repeat" in step:
        return step["repeat"] * sum(_step_seconds(s) for s in step.get("steps", []))
    if "duration" in step:
        return parse_duration(step["duration"], "steps")
    if "distance" in step and isinstance(step.get("target"), dict) and "pace" in step["target"]:
        parts = [p.strip() for p in str(step["target"]["pace"]).split("-")]
        secs_per_km = [1000.0 / parse_pace(p, "steps") for p in parts]
        mid_secs_per_km = sum(secs_per_km) / len(secs_per_km)
        return parse_distance(step["distance"], "steps") / 1000.0 * mid_secs_per_km
    return 0.0


def estimate_duration_s(dsl_steps: list[dict]) -> int:
    return int(round(sum(_step_seconds(s) for s in dsl_steps)))


def build_workout(dsl: Any) -> dict[str, Any]:
    if not isinstance(dsl, dict):
        raise _bad("", "workout must be a JSON object")
    name = dsl.get("name")
    if not isinstance(name, str) or not name.strip():
        raise _bad("name", "required non-empty string")
    sport = _sport(dsl, "")
    steps = _steps(dsl.get("steps"), "steps", [0])
    return {
        "workoutName": name.strip(),
        "description": dsl.get("description") or None,
        "sportType": sport,
        "estimatedDurationInSecs": estimate_duration_s(dsl["steps"]),
        "workoutSegments": [{"segmentOrder": 1, "sportType": sport, "workoutSteps": steps}],
    }


DSL_REFERENCE: dict[str, Any] = {
    "sports": list(SPORTS),
    "step_types": list(STEP_TYPES),
    "repeat": {"repeat": "N (int >= 1)", "steps": "[nested steps]"},
    "end_condition": {"exactly_one_of": ["duration", "distance", "open"],
                      "duration": "45s | 10m | 1h | 1h30m", "distance": "400m | 1km | 5.5km", "open": True},
    "target": {"optional_exactly_one_of": ["pace", "hr", "power", "cadence"],
               "pace": "min:sec per km, single or range, e.g. 4:15-4:25",
               "hr": "z1..z5 or bpm range 140-150", "power": "W range 200-220", "cadence": "range 170-180"},
    "optional": {"description": "workout description", "note": "per-step description"},
    "example": {
        "name": "Tempo 3x10", "sport": "running",
        "steps": [
            {"type": "warmup", "duration": "15m"},
            {"repeat": 3, "steps": [
                {"type": "interval", "duration": "10m", "target": {"pace": "4:15-4:25"}},
                {"type": "recovery", "duration": "3m"}]},
            {"type": "cooldown", "distance": "1km"}]},
}
