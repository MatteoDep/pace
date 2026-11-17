"""Athlete diary: the coach's durable memory, stored at $PACE_HOME/diary.json."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pace import config, store
from pace.dates import parse_date
from pace.envelope import PaceError

KINDS = ("note", "plan", "recommendation", "feedback", "constraint", "injury", "decision")
ACTIVE_KINDS = ("constraint", "injury")
PROFILE_KEYS = ("goal", "event.name", "event.date", "availability", "background", "other_sports",
                "strength", "preferences", "checkin.weight_days", "checkin.last", "dashboard.url")
WEIGHT_DAYS_DEFAULT = 7
REF_PATTERN = r"(session|activity|entry):[1-9][0-9]*"  # always used with re.fullmatch
EMPTY: dict[str, Any] = {"profile": {}, "entries": [], "next_id": 1}

_BOOKKEEPING_PREFIXES = ("checkin.", "dashboard.")
_PARENT_KEYS = ("event", "checkin", "dashboard")


def diary_path() -> Path:
    return config.PACE_HOME / "diary.json"


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _entry_ok(e: Any) -> bool:
    return (isinstance(e, dict) and _is_int(e.get("id"))
            and all(isinstance(e.get(k), str) for k in ("date", "kind", "text"))
            and all(k not in e or _str_list(e[k]) for k in ("tags", "refs"))
            and all(e.get(k) is None or isinstance(e[k], str) for k in ("due", "resolved")))


def load() -> dict[str, Any]:
    path = diary_path()
    data = store.load_json(path, EMPTY, "diary")
    entries = data.get("entries")
    profile = data.get("profile")
    if (not isinstance(profile, dict) or not isinstance(entries, list)
            or any(k in profile and not isinstance(profile[k], dict) for k in _PARENT_KEYS)
            or not all(_entry_ok(e) for e in entries)):
        raise PaceError("internal", "diary has an unexpected shape",
                        hint=f"Inspect or move {path}; pace never overwrites it.")
    for e in entries:
        e.setdefault("tags", [])
        e.setdefault("refs", [])
        e.setdefault("due", None)
        e.setdefault("resolved", None)
    next_id = data.get("next_id")
    data["next_id"] = max(next_id if _is_int(next_id) else 1, max((e["id"] for e in entries), default=0) + 1)
    return data


@contextmanager
def transaction() -> Iterator[dict[str, Any]]:
    """Locked read-modify-write; the file is saved only when the block exits cleanly."""
    path = diary_path()
    with store.locked(path):
        data = load()
        yield data
        store.save_json(path, data)


def _bad(msg: str) -> PaceError:
    return PaceError("bad_args", msg)


def _parse_checkin_last(value: str) -> str:
    if value == "now":
        moment = datetime.now().astimezone()
    else:
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            raise _bad(f"checkin.last must be 'now' or an ISO datetime, got {value!r}") from None
        moment = moment.astimezone()  # naive -> local zone; aware -> unchanged instant
    return moment.isoformat(timespec="seconds")


def _normalize(key: str, value: str) -> str | int:
    if key == "event.date":
        return parse_date(value).isoformat()
    if key == "checkin.weight_days":
        if not re.fullmatch(r"[0-9]+", value) or not 1 <= int(value) <= 90:
            raise _bad("checkin.weight_days must be an integer between 1 and 90")
        return int(value)
    if key == "checkin.last":
        return _parse_checkin_last(value)
    if key == "dashboard.url" and not value.startswith("https://claude.ai/"):
        raise _bad("dashboard.url must start with https://claude.ai/")
    return value


def _check_key(key: str) -> None:
    if key not in PROFILE_KEYS:
        raise _bad(f"unknown profile key {key!r}; one of {', '.join(PROFILE_KEYS)}")


def _touch(profile: dict[str, Any], key: str) -> None:
    if not key.startswith(_BOOKKEEPING_PREFIXES):
        profile["updated"] = date.today().isoformat()


def set_profile(data: dict[str, Any], key: str, value: str) -> dict[str, Any]:
    _check_key(key)
    value = value.strip()
    if not value:
        raise _bad("value must be non-empty")
    stored = _normalize(key, value)
    prof = data["profile"]
    if "." in key:
        parent, child = key.split(".", 1)
        prof.setdefault(parent, {})[child] = stored
    else:
        prof[key] = stored
    _touch(prof, key)
    return prof


def unset_profile(data: dict[str, Any], key: str) -> dict[str, Any]:
    _check_key(key)
    prof = data["profile"]
    if "." in key:
        parent, child = key.split(".", 1)
        sub = prof.get(parent)
        if isinstance(sub, dict) and child in sub:
            del sub[child]
            if not sub:
                del prof[parent]
            _touch(prof, key)
    elif key in prof:
        del prof[key]
        _touch(prof, key)
    return prof


def _iso(value: str | None) -> str:
    return parse_date(value).isoformat()


def _since_iso(value: str) -> str:
    """A date form, or an ISO datetime (offset-aware ones are converted to the local zone)."""
    if "T" in value:
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            raise _bad(f"invalid --since {value!r}; use a date or an ISO datetime") from None
        return (moment.astimezone() if moment.tzinfo else moment).date().isoformat()
    return _iso(value)


def add_entry(data: dict[str, Any], *, kind: str, text: str, on: str | None, tags: list[str],
              refs: list[str], due: str | None) -> dict[str, Any]:
    if kind not in KINDS:
        raise _bad(f"unknown kind {kind!r}; one of {', '.join(KINDS)}")
    if not isinstance(text, str) or not text.strip():
        raise _bad("text must be non-empty")
    for ref in refs:
        if not re.fullmatch(REF_PATTERN, ref):
            raise _bad(f"invalid ref {ref!r}; use session:<id>, activity:<id> or entry:<id>")
    entry = {"id": data["next_id"], "date": _iso(on), "kind": kind, "text": text.strip(),
             "tags": list(dict.fromkeys(t.strip() for t in tags if t.strip())), "refs": list(refs),
             "due": _iso(due) if due is not None else None, "resolved": None}
    data["next_id"] += 1
    data["entries"].append(entry)
    return entry


def _find(data: dict[str, Any], entry_id: int) -> dict[str, Any]:
    for e in data["entries"]:
        if e["id"] == entry_id:
            return e
    raise PaceError("not_found", f"no diary entry with id {entry_id}",
                    hint="List ids with `pace diary show`.")


def remove_entry(data: dict[str, Any], entry_id: int) -> dict[str, Any]:
    entry = _find(data, entry_id)
    data["entries"].remove(entry)
    return entry


def set_due(data: dict[str, Any], entry_id: int, due: str | None) -> dict[str, Any]:
    entry = _find(data, entry_id)
    entry["due"] = _iso(due) if due is not None else None
    return entry


def resolve(data: dict[str, Any], entry_id: int, on: str | None) -> dict[str, Any]:
    entry = _find(data, entry_id)
    if entry["kind"] not in ACTIVE_KINDS:
        raise _bad(f"only {' or '.join(ACTIVE_KINDS)} entries can be resolved, not {entry['kind']!r}")
    when = _iso(on)
    if when > date.today().isoformat():
        raise _bad(f"resolved date {when} is in the future")
    if when < entry["date"]:
        raise _bad(f"resolved date {when} is before the entry date {entry['date']}")
    entry["resolved"] = when
    return entry


def reopen(data: dict[str, Any], entry_id: int) -> dict[str, Any]:
    entry = _find(data, entry_id)
    if entry["kind"] not in ACTIVE_KINDS:
        raise _bad(f"only {' or '.join(ACTIVE_KINDS)} entries can be reopened, not {entry['kind']!r}")
    entry["resolved"] = None
    return entry


def show(data: dict[str, Any], *, since: str | None, kind: str | None, ref: str | None, limit: int,
         today: date | None = None) -> tuple[dict[str, Any], bool]:
    if kind is not None and kind not in KINDS:
        raise _bad(f"unknown kind {kind!r}; one of {', '.join(KINDS)}")
    if ref is not None and not re.fullmatch(REF_PATTERN, ref):
        raise _bad(f"invalid ref {ref!r}; use session:<id>, activity:<id> or entry:<id>")
    if limit < 1:
        raise _bad("--limit must be >= 1")
    today_iso = (today or date.today()).isoformat()
    since_iso = _since_iso(since) if since else None
    entries = sorted(data["entries"], key=lambda e: (e["date"], e["id"]), reverse=True)
    if since_iso:
        entries = [e for e in entries if e["date"] >= since_iso]
    if kind:
        entries = [e for e in entries if e["kind"] == kind]
    if ref:
        entries = [e for e in entries if ref in e["refs"]]
    active = sorted((e for e in data["entries"] if e["kind"] in ACTIVE_KINDS and e["resolved"] is None),
                    key=lambda e: (e["date"], e["id"]))
    due = sorted((e for e in data["entries"] if e["due"] is not None and e["due"] <= today_iso),
                 key=lambda e: (e["due"], e["id"]))
    out = {"profile": data["profile"], "active": active, "due": due, "entries": entries[:limit]}
    return out, len(entries) > limit
