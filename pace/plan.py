"""Training plan: blocks and dated sessions, stored at $PACE_HOME/plan.json (archives in plans/)."""

import copy
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from pace import config, store
from pace.dates import parse_date
from pace.envelope import PaceError
from pace.sports import PUSHABLE, SESSION_SPORTS
from pace.workout import build_workout

INTENSITIES = ("rest", "easy", "moderate", "hard")
STATUSES = ("planned", "pushed", "skipped", "done")
SETTABLE_STATUSES = ("planned", "skipped", "done")
PHASES = ("base", "build", "peak", "taper", "recovery", "transition")
MODIFIED_TOLERANCE = 0.20
WORKOUT_PREFIX = "pace: "
SESSION_FIELDS = ("date", "sport", "title", "purpose", "intensity", "block", "target", "workout", "notes",
                  "status", "activity_id")
EMPTY: dict[str, Any] = {"plan": {}, "blocks": [], "sessions": [], "next_id": 1, "next_block_id": 1}

_REQUIRED = ("date", "sport", "title", "intensity")
_TARGET_KEYS = ("duration_s", "distance_m", "elevation_m")
_META_KEYS = ("name", "goal", "event_date")
_BLOCK_FIELDS = ("name", "phase", "start", "end", "focus")
_SHOW_HINT = "List ids with `pace plan show`."


def plan_path() -> Path:
    return config.PACE_HOME / "plan.json"


def archive_dir() -> Path:
    return config.PACE_HOME / "plans"


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _bad(msg: str, hint: str | None = None) -> PaceError:
    return PaceError("bad_args", msg, hint=hint)


def _block_num(block_id: str) -> int:
    m = re.fullmatch(r"b([0-9]+)", block_id)
    return int(m.group(1)) if m else 0


def load() -> dict[str, Any]:
    path = plan_path()
    data = store.load_json(path, EMPTY, "plan")
    plan, blocks, sessions = data.get("plan"), data.get("blocks"), data.get("sessions")
    if (not isinstance(plan, dict) or not isinstance(blocks, list) or not isinstance(sessions, list)
            or not all(isinstance(b, dict) and isinstance(b.get("id"), str) for b in blocks)
            or not all(isinstance(s, dict) and _is_int(s.get("id")) and isinstance(s.get("date"), str)
                       for s in sessions)):
        raise PaceError("internal", "plan has an unexpected shape",
                        hint=f"Inspect or move {path}; pace never overwrites it.")
    next_id, next_block = data.get("next_id"), data.get("next_block_id")
    data["next_id"] = max(next_id if _is_int(next_id) else 1, max((s["id"] for s in sessions), default=0) + 1)
    data["next_block_id"] = max(next_block if _is_int(next_block) else 1,
                                max((_block_num(b["id"]) for b in blocks), default=0) + 1)
    return data


@contextmanager
def transaction() -> Iterator[dict[str, Any]]:
    """Locked read-modify-write; the file is saved only when the block exits cleanly."""
    path = plan_path()
    with store.locked(path):
        data = load()
        yield data
        store.save_json(path, data)


def _iso(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise _bad(f"{what} must be a date string (YYYY-MM-DD)")
    return parse_date(value).isoformat()


def is_stale(s: dict[str, Any]) -> bool:
    if s.get("garmin") is None:
        return False
    snap = s.get("pushed_as") or {}
    return (s["date"], s["workout"]) != (snap.get("date"), snap.get("workout"))


def _check_target(target: Any) -> dict[str, Any]:
    if not isinstance(target, dict) or any(k not in _TARGET_KEYS for k in target):
        raise _bad(f"target must be an object with keys from {', '.join(_TARGET_KEYS)}")
    out = {k: target.get(k) for k in _TARGET_KEYS}
    for k, v in out.items():
        if v is not None and (not _is_num(v) or v <= 0):
            raise _bad(f"target.{k} must be a number > 0 or null")
    return out


def _validate(data: dict[str, Any], s: dict[str, Any], today: date, status_changed: bool) -> None:
    """Validate (and normalise in place) a merged session."""
    s["date"] = _iso(s["date"], "date")
    if s["sport"] not in SESSION_SPORTS:
        raise _bad(f"unknown sport {s['sport']!r}; one of {', '.join(SESSION_SPORTS)}")
    if s["intensity"] not in INTENSITIES:
        raise _bad(f"unknown intensity {s['intensity']!r}; one of {', '.join(INTENSITIES)}")
    for key in ("title", "purpose", "notes"):
        if not isinstance(s[key], str):
            raise _bad(f"{key} must be a string")
    if not s["title"].strip():
        raise _bad("title must be non-empty")
    if s["block"] is not None and not any(b["id"] == s["block"] for b in data["blocks"]):
        raise _bad(f"no block {s['block']!r}", hint=_SHOW_HINT)
    s["target"] = _check_target(s["target"])
    if s["activity_id"] is not None and not _is_int(s["activity_id"]):
        raise _bad("activity_id must be an integer or null")
    if s["workout"] is not None:
        if s["sport"] not in PUSHABLE:
            raise _bad(f"sport {s['sport']!r} cannot carry a workout; only {', '.join(PUSHABLE)}",
                       hint="Remove the workout or change the sport.")
        build_workout(s["workout"])
        if s["workout"].get("sport") != s["sport"]:
            raise _bad(f"workout sport {s['workout'].get('sport')!r} differs from session sport {s['sport']!r}")
    if status_changed:
        if s["status"] not in SETTABLE_STATUSES:
            raise _bad(f"status cannot be set to {s['status']!r}; one of {', '.join(SETTABLE_STATUSES)}")
        # A past session may still be marked skipped or done: its workout can no longer be unpushed.
        past = s["date"] < today.isoformat()
        if s["garmin"] is not None and not (past and s["status"] in ("skipped", "done")):
            raise _bad("session has a Garmin workout; its status can only become skipped or done once its "
                       "date is past", hint="unpush it first")
    if s["status"] == "done" and date.fromisoformat(s["date"]) > today:
        raise _bad(f"cannot mark a future session done ({s['date']})")


def _clean_changes(changes: Any, allowed: tuple[str, ...], what: str) -> None:
    if not isinstance(changes, dict):
        raise _bad(f"{what} must be an object")
    for k in changes:
        if k not in allowed:
            raise _bad(f"{what}: {k!r} is not settable; one of {', '.join(allowed)}")


def _new_session(data: dict[str, Any], item: Any, today: date) -> dict[str, Any]:
    _clean_changes(item, SESSION_FIELDS, "session")
    missing = [k for k in _REQUIRED if k not in item]
    if missing:
        raise _bad(f"missing required field(s): {', '.join(missing)}")
    s: dict[str, Any] = {"purpose": "", "block": None, "target": {}, "workout": None, "notes": "",
                         "status": "planned", "activity_id": None, **copy.deepcopy(item)}
    s.update(garmin=None, pushed_as=None)
    _validate(data, s, today, "status" in item)
    s["stale"] = False
    return s


def add_sessions(data: dict[str, Any], items: dict[str, Any] | list[dict[str, Any]],
                 today: date | None = None) -> list[dict[str, Any]]:
    """Add one or more sessions; a batch is all-or-nothing and errors name the item index."""
    today = today or date.today()
    batch = [items] if isinstance(items, dict) else items
    if not isinstance(batch, list) or not batch:
        raise _bad("expected a session object or a non-empty list of them")
    built = []
    for i, item in enumerate(batch):
        try:
            built.append(_new_session(data, item, today))
        except PaceError as exc:
            raise PaceError(exc.code, f"item {i}: {exc}", hint=exc.hint) from None
    for s in built:
        s["id"] = data["next_id"]
        data["next_id"] += 1
        data["sessions"].append(s)
    return built


def _find(data: dict[str, Any], session_id: int) -> dict[str, Any]:
    for s in data["sessions"]:
        if s["id"] == session_id:
            return s
    raise PaceError("not_found", f"no session with id {session_id}", hint=_SHOW_HINT)


def set_session(data: dict[str, Any], session_id: int, changes: dict[str, Any],
                today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    s = _find(data, session_id)
    _clean_changes(changes, SESSION_FIELDS, "changes")
    merged = {**copy.deepcopy(s), **copy.deepcopy(changes)}
    if "target" in changes and isinstance(changes["target"], dict):
        merged["target"] = {**s["target"], **changes["target"]}
    _validate(data, merged, today, "status" in changes)
    merged["stale"] = is_stale(merged)
    s.update(merged)
    return s


def remove_sessions(data: dict[str, Any], ids: list[int]) -> list[dict[str, Any]]:
    found = [_find(data, i) for i in dict.fromkeys(ids)]
    on_garmin = [s["id"] for s in found if s["garmin"] is not None]
    if on_garmin:
        raise _bad(f"session(s) {', '.join(map(str, on_garmin))} have a Garmin workout; nothing removed",
                   hint=f"unpush them first: pace plan unpush {' '.join(map(str, on_garmin))}")
    for s in found:
        data["sessions"].remove(s)
    return found


def garmin_horizon(s: dict[str, Any]) -> str:
    """The later of the session date and the date its workout was pushed to (a session may move after a push)."""
    return max(s["date"], (s.get("pushed_as") or {}).get("date") or s["date"])


def sessions_in(data: dict[str, Any], start: date, end: date) -> list[dict[str, Any]]:
    lo, hi = start.isoformat(), end.isoformat()
    return sorted((s for s in data["sessions"] if lo <= s["date"] <= hi), key=lambda s: (s["date"], s["id"]))


def _is_iso_day(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-") or "plan"


def new_plan(data: dict[str, Any], *, name: str, goal: str | None, event_date: str | None,
             today: date) -> str | None:
    """Start a new plan in place, archiving the current one; returns the archive path or None."""
    if not isinstance(name, str) or not name.strip():
        raise _bad("name must be non-empty")
    event = _iso(event_date, "event_date") if event_date is not None else None
    blocked = [s["id"] for s in data["sessions"]
               if s["garmin"] is not None and garmin_horizon(s) >= today.isoformat()]
    if blocked:
        raise _bad(f"session(s) {', '.join(map(str, blocked))} have a Garmin workout dated today or later",
                   hint=f"unpush them first: pace plan unpush {' '.join(map(str, blocked))}")
    archived = None
    if data["plan"] or data["blocks"] or data["sessions"]:
        old = data["plan"]
        created = old.get("created")
        if not _is_iso_day(created):  # the archive name must never carry a path
            created = today.isoformat()
        base = f"{created}-{_slug(str(old.get('name') or ''))}"
        target = archive_dir() / f"{base}.json"
        n = 2
        while target.exists():
            target = archive_dir() / f"{base}-{n}.json"
            n += 1
        store.save_json(target, copy.deepcopy(data))
        archived = str(target)
    next_id, next_block = data["next_id"], data["next_block_id"]
    data.clear()
    data.update(copy.deepcopy(EMPTY))
    data["next_id"], data["next_block_id"] = next_id, next_block
    data["plan"] = {"name": name.strip(), "goal": goal, "event_date": event,
                    "created": today.isoformat(), "updated": today.isoformat()}
    return archived


def set_meta(data: dict[str, Any], key: str, value: str) -> dict[str, Any]:
    if key not in _META_KEYS:
        raise _bad(f"unknown plan key {key!r}; one of {', '.join(_META_KEYS)}")
    if not isinstance(value, str) or not value.strip():
        raise _bad("value must be non-empty")
    data["plan"][key] = _iso(value, key) if key == "event_date" else value.strip()
    data["plan"]["updated"] = date.today().isoformat()
    return data["plan"]


def _check_block(b: dict[str, Any]) -> None:
    if not isinstance(b["name"], str) or not b["name"].strip():
        raise _bad("block name must be non-empty")
    if b["phase"] not in PHASES:
        raise _bad(f"unknown phase {b['phase']!r}; one of {', '.join(PHASES)}")
    b["start"], b["end"] = _iso(b["start"], "start"), _iso(b["end"], "end")
    if b["start"] > b["end"]:
        raise _bad(f"block start {b['start']} is after end {b['end']}")
    if not isinstance(b["focus"], str):
        raise _bad("focus must be a string")


def add_block(data: dict[str, Any], block: dict[str, Any]) -> dict[str, Any]:
    _clean_changes(block, _BLOCK_FIELDS, "block")
    missing = [k for k in ("name", "phase", "start", "end") if k not in block]
    if missing:
        raise _bad(f"missing required field(s): {', '.join(missing)}")
    b = {"focus": "", **block}
    _check_block(b)
    b = {"id": f"b{data['next_block_id']}", **b}
    data["next_block_id"] += 1
    data["blocks"].append(b)
    return b


def _find_block(data: dict[str, Any], block_id: str) -> dict[str, Any]:
    for b in data["blocks"]:
        if b["id"] == block_id:
            return b
    raise PaceError("not_found", f"no block {block_id!r}", hint=_SHOW_HINT)


def set_block(data: dict[str, Any], block_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    b = _find_block(data, block_id)
    _clean_changes(changes, _BLOCK_FIELDS, "changes")
    merged = {**b, **changes}
    _check_block(merged)
    b.update(merged)
    return b


def remove_block(data: dict[str, Any], block_id: str) -> dict[str, Any]:
    b = _find_block(data, block_id)
    users = [s["id"] for s in data["sessions"] if s.get("block") == block_id]
    if users:
        raise _bad(f"block {block_id} is used by session(s) {', '.join(map(str, users))}",
                   hint="Move or remove those sessions first.")
    data["blocks"].remove(b)
    return b
