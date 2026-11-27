"""Requests the athlete made on the dashboard page: each is checked in code and applied at most once.

The page stores one document per press; the coach passes them here as a list of
`{"id", "version", "at", "op", "params"}`. Values come from a browser, so they are untrusted:
every one is type- and range-checked before it reaches a Garmin call, and messages quote them
only through a short `repr()`.

At most once: the applied log (`$PACE_HOME/requests-applied.jsonl`) holds every outcome, so a
request applied before (a retry after a lost store delete) is skipped, and failed attempts are
counted. A weigh-in, the one creating write, is also checked against the day's weigh-ins first.
"""

import hashlib
import json
import math
import os
import re
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pace import config, ratelimit, store
from pace.cache import Cache, CacheTracker
from pace.commands._helpers import cached_call
from pace.commands.activity import DETAIL_TTL, GEAR_TTL
from pace.effort import FEEL_LEVELS
from pace.envelope import PaceError, log
from pace.factory import resolve_upn, today_ttl
from pace.ids import validate_uuid
from pace.spec import REGISTRY
from pace.writes import WriteSpec, run_write
from pace.writespecs import effort_spec, gear_link_spec, manual_weigh_in_ids, weigh_in_spec

OPS = ("shoe.set", "effort.set", "weight.add")
#: Allowed params per op; REQUIRED lists those that must be present (effort.set also needs rpe or feel).
PARAMS: dict[str, tuple[str, ...]] = {"shoe.set": ("activity_id", "gear_uuid"),
                                      "effort.set": ("activity_id", "rpe", "feel"),
                                      "weight.add": ("kg", "date")}
REQUIRED: dict[str, tuple[str, ...]] = {"shoe.set": ("activity_id", "gear_uuid"),
                                        "effort.set": ("activity_id",),
                                        "weight.add": ("kg", "date")}
KG_RANGE = (25, 250)
MAX_AGE_DAYS = 365
#: A weigh-in may be dated one day after the host's today: the page's local date can be ahead of it.
MAX_AHEAD_DAYS = 1
MAX_ATTEMPTS = 3
#: A request still failing this many days after its first failure is refused, whatever the failures were:
#: failures Garmin's throttling or silence caused do not count as attempts, and must not keep a request
#: (and the requests queued behind it) waiting forever.
MAX_FAILING_DAYS = 7
#: The run's call and time budget (the export's mechanism): past it, or once Garmin throttles or does
#: not answer, calls are not sent and their requests wait for the next run.
MAX_CALLS = 30
MAX_SECONDS = 240
#: Answers a retry would not change. `endpoint_unavailable` (a 403 or a 400 on a read) is not one: Garmin's
#: edge sends a passing 403 at times, so it is a counted failure, refused after MAX_ATTEMPTS.
REFUSED_CODES = ("bad_args", "rejected", "not_found")
STATUSES = ("applied", "refused", "failed", "planned")
LOG_NAME = "requests-applied.jsonl"
#: The store collection the page writes to and the coach lists (docs only; pace never reads the store).
COLLECTION = "data/requests/pending"
ID_PATTERN = r"[A-Za-z0-9_-]{1,128}"

_ID = re.compile(ID_PATTERN)
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MAX_ID = 2**63
_REASON_CHARS = 300
_GEAR_LIST = next(s for s in REGISTRY if s.group == "gear" and s.name == "list")
_GAVE_UP = f"gave up after {MAX_ATTEMPTS} attempts"


def log_path() -> Path:
    return config.PACE_HOME / LOG_NAME


def content_hash(op: Any, params: Any) -> str:
    text = json.dumps({"op": op, "params": params}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


# --- validation (no network) ----------------------------------------------------------------

def _q(value: Any) -> str:
    """An untrusted value for a message: repr, cut to 80 characters."""
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


def _bad(message: str) -> PaceError:
    return PaceError("bad_args", message)


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _activity_id(value: Any) -> int:
    if not _int(value) or not 0 < value < _MAX_ID:
        raise _bad(f"activity_id must be a positive integer: {_q(value)}")
    return value


def _gear_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise _bad(f"gear_uuid must be a string of 32 hex characters: {_q(value)}")
    try:
        return validate_uuid(value)
    except PaceError:
        raise _bad(f"gear_uuid must be 32 hex characters: {_q(value)}") from None


def _rpe(value: Any) -> int:
    if not _int(value) or not 1 <= value <= 10:
        raise _bad(f"rpe must be an integer 1-10: {_q(value)}")
    return value


def _feel(value: Any) -> str:
    if not isinstance(value, str) or value not in FEEL_LEVELS:
        raise _bad(f"feel must be one of {', '.join(FEEL_LEVELS)}: {_q(value)}")
    return value


def _kg(value: Any) -> float:
    lo, hi = KG_RANGE
    # The range check comes first and also refuses NaN and infinities; math.isfinite would overflow
    # on a huge int, and an int in range is always finite.
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not lo <= value <= hi:
        raise _bad(f"kg must be a number {lo}-{hi}: {_q(value)}")
    assert math.isfinite(value)
    return float(value)


def _day(value: Any, today: date) -> str:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise _bad(f"date must be YYYY-MM-DD: {_q(value)}")
    try:
        d = date.fromisoformat(value)
    except ValueError:
        raise _bad(f"date is not a calendar date: {_q(value)}") from None
    if d > today + timedelta(days=MAX_AHEAD_DAYS):
        raise _bad(f"date is in the future: {value}")
    if d < today - timedelta(days=MAX_AGE_DAYS):
        raise _bad(f"date is more than {MAX_AGE_DAYS} days back: {value}")
    return value


def check(req: Any, today: date) -> tuple[str, dict[str, Any]]:
    """Validate one request without any network call: `(op, params)` with values normalized,
    or PaceError(bad_args) naming the key. `at`, `version` and unknown top-level keys are not checked."""
    if not isinstance(req, dict):
        raise _bad(f"a request must be a JSON object, not {type(req).__name__}")
    rid = req.get("id")
    if not isinstance(rid, str) or not _ID.fullmatch(rid):
        raise _bad(f"id must match {ID_PATTERN}: {_q(rid)}")
    op = req.get("op")
    if not isinstance(op, str) or op not in OPS:
        raise _bad(f"op must be one of {', '.join(OPS)}: {_q(op)}")
    params = req.get("params")
    if not isinstance(params, dict):
        raise _bad(f"params of {op} must be a JSON object: {_q(params)}")
    unknown = [k for k in params if k not in PARAMS[op]]
    if unknown:
        raise _bad(f"params of {op} take only {', '.join(PARAMS[op])}; unknown: {', '.join(_q(k) for k in unknown)}")
    missing = [k for k in REQUIRED[op] if k not in params]
    if missing:
        raise _bad(f"params of {op} need {', '.join(missing)}")
    out: dict[str, Any] = {}
    if op == "weight.add":
        out["kg"] = _kg(params["kg"])
        out["date"] = _day(params["date"], today)
        return op, out
    out["activity_id"] = _activity_id(params["activity_id"])
    if op == "shoe.set":
        out["gear_uuid"] = _gear_uuid(params["gear_uuid"])
        return op, out
    if "rpe" not in params and "feel" not in params:
        raise _bad("effort.set needs rpe or feel (or both)")
    if "rpe" in params:
        out["rpe"] = _rpe(params["rpe"])
    if "feel" in params:
        out["feel"] = _feel(params["feel"])
    return op, out


def _at(req: Any) -> datetime | None:
    """The press time, when it is an ISO datetime with a timezone; it only orders the requests."""
    value = req.get("at") if isinstance(req, dict) else None
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        return None
    return at if at.tzinfo is not None else None


def _ordered(reqs: list[Any]) -> list[Any]:
    """By press time (stable); requests without a usable `at` keep their order, after the others."""
    keyed = []
    for i, r in enumerate(reqs):
        at = _at(r)
        keyed.append(((0, at, i) if at is not None else (1, i), r))
    return [r for _, r in sorted(keyed, key=lambda p: p[0])]


def _targets(op: str, params: dict[str, Any]) -> set[tuple[str, Any]]:
    """What a request changes: a later request on the same target must not jump ahead of a failed one."""
    if op == "shoe.set":
        return {("gear", params["activity_id"])}
    if op == "effort.set":
        return {(k, params["activity_id"]) for k in ("rpe", "feel") if k in params}
    return {("weight", params["date"])}


# --- applied log ----------------------------------------------------------------------------

def _counts(code: Any, http: Any) -> bool:
    """Whether a failure counts toward MAX_ATTEMPTS: not when Garmin throttled or did not answer at all
    (that says nothing about the request), nor a refusal code, nor a call that was never sent."""
    if code in REFUSED_CODES or code in (None, "deferred", "rate_limited"):
        return False
    return not (code == "network" and http is None)


@dataclass
class _Seen:
    applied: bool = False
    failures: int = 0
    first_failed: date | None = None
    gave_up: bool = False  # a refusal logged after failures (not a refusal code): never tried again


def _logged_day(value: Any) -> date | None:
    """The host's local date of a logged time (written in UTC), comparable with the run's `today`."""
    try:
        return datetime.fromisoformat(value).astimezone().date() if isinstance(value, str) else None
    except (ValueError, OverflowError):
        return None


def _load_log(path: Path) -> dict[tuple[str, str], _Seen]:
    seen: dict[tuple[str, str], _Seen] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return seen
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            log(f"{path.name} line {n} unreadable; skipped")
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("hash"), str):
            continue
        s = seen.setdefault((entry["id"], entry["hash"]), _Seen())
        status, code = entry.get("status"), entry.get("code")
        if status == "applied":
            s.applied = True
        elif status in ("failed", "refused") and code not in REFUSED_CODES and code is not None:
            day = _logged_day(entry.get("time"))
            if day is not None and (s.first_failed is None or day < s.first_failed):
                s.first_failed = day
            if _counts(code, entry.get("http")):
                s.failures += 1
            if status == "refused":
                s.gave_up = True
    return seen


class _LogWriter:
    """Appends one durable line per outcome; a torn last line (a crash mid-append) is closed first."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.newline_first = False
        try:
            with path.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                if fh.tell() > 0:
                    fh.seek(-1, os.SEEK_END)
                    self.newline_first = fh.read(1) != b"\n"
        except FileNotFoundError:
            pass

    def append(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=True, separators=(", ", ": ")) + "\n"
        if self.newline_first:
            line = "\n" + line
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        self.newline_first = False


# --- the run --------------------------------------------------------------------------------

class _Run:
    def __init__(self, *, dry_run: bool, today: date, seen: dict[tuple[str, str], _Seen],
                 writer: _LogWriter | None, cache: Cache) -> None:
        self.dry_run = dry_run
        self.today = today
        self.seen = seen
        self.writer = writer
        self.cache = cache
        self.tracker = CacheTracker()
        self.ids: set[str] = set()
        self.failed_targets: set[tuple[str, Any]] = set()
        self.weights: dict[tuple[float, str], str] = {}  # weight.add content -> id that applied it
        self.activities: set[int] = set()  # found this run: an effort write drops the cached activity
        self._shoes: set[str] | None = None
        self._shoes_refreshed = False

    # Garmin reads and writes

    async def activity(self, activity_id: int) -> None:
        """Raises not_found when the activity does not exist; same cache key and TTL as `activity show`."""
        if activity_id not in self.activities:
            await cached_call("get_activity", activity_id, cache=self.cache, ttl=DETAIL_TTL, tracker=self.tracker)
            self.activities.add(activity_id)

    async def _read_shoes(self, refresh: bool) -> set[str]:
        upn = await resolve_upn(self.cache, self.tracker)
        gear = await cached_call("get_gear", upn, cache=self.cache, ttl=today_ttl(_GEAR_LIST), tracker=self.tracker,
                                 refresh=refresh)
        if not isinstance(gear, list):
            # Not "no shoes": refusing on it would delete a valid press.
            raise PaceError("internal", f"gear list: expected a list from Garmin, got {type(gear).__name__}")
        return {g["uuid"].lower() for g in gear
                if isinstance(g, dict) and isinstance(g.get("uuid"), str)
                and g.get("gearTypeName") == "Shoes" and g.get("gearStatusName") == "active"}

    async def active_shoe(self, uuid: str) -> bool:
        """From the `gear list` cache; one fresh read per run when the shoe is not there (added today)."""
        if self._shoes is None:
            self._shoes = await self._read_shoes(refresh=False)
        if uuid not in self._shoes and not self._shoes_refreshed:
            self._shoes_refreshed = True
            self._shoes = await self._read_shoes(refresh=True)
        return uuid in self._shoes

    async def write(self, spec: WriteSpec, did: list[Any]) -> None:
        did.append((await run_write(spec, dry_run=self.dry_run)).data)

    # operations: each returns a reason when it had nothing to write, else None

    async def shoe_set(self, p: dict[str, Any], did: list[Any]) -> str | None:
        aid, uuid = p["activity_id"], p["gear_uuid"]
        await self.activity(aid)
        if not await self.active_shoe(uuid):
            raise PaceError("bad_args", f"gear_uuid {uuid} is not an active shoe in `pace gear list`")
        # Fresh: the unlinks must see what is linked now (gear changed in the app is not invalidated).
        linked = await cached_call("get_activity_gear", aid, cache=self.cache, ttl=GEAR_TTL, tracker=self.tracker,
                                   refresh=True)
        if not isinstance(linked, list):
            raise PaceError("internal", f"activity gear: expected a list from Garmin, got {type(linked).__name__}")
        items = [g for g in linked if isinstance(g, dict) and isinstance(g.get("uuid"), str)]
        if uuid not in {g["uuid"].lower() for g in items}:
            # Link first: a failed link never leaves the activity without a shoe.
            await self.write(gear_link_spec("link", aid, uuid), did)
        done = {uuid}
        for g in items:
            other = g["uuid"].lower()
            if other not in done and g.get("gearTypeName") == "Shoes":
                done.add(other)
                await self.write(gear_link_spec("unlink", aid, other), did)
        return None if did else "shoe already set"

    async def effort_set(self, p: dict[str, Any], did: list[Any]) -> str | None:
        await self.activity(p["activity_id"])
        await self.write(effort_spec(p["activity_id"], p.get("rpe"), p.get("feel")), did)
        return None

    async def weight_add(self, p: dict[str, Any], did: list[Any]) -> str | None:
        # Uncached: a write whose response was lost never invalidated the cached day.
        day = await cached_call("get_daily_weigh_ins", p["date"], cache=None, ttl=0)
        if manual_weigh_in_ids(day, p["kg"]):
            return f"already recorded on {p['date']}"
        await self.write(weigh_in_spec(p["kg"], p["date"]), did)
        return None

    # one request

    def _result(self, req: Any, op: str | None, status: str, code: str | None = None, reason: str | None = None,
                did: list[Any] | None = None, http: int | None = None, hash_: str | None = None,
                record: bool = False) -> dict[str, Any]:
        rid = req.get("id") if isinstance(req, dict) else None
        rid = rid if isinstance(rid, str) and _ID.fullmatch(rid) else None
        version = req.get("version") if isinstance(req, dict) else None
        if not (_int(version) or (isinstance(version, str) and len(version) <= 64)):
            version = None
        if reason is not None and len(reason) > _REASON_CHARS:
            reason = reason[:_REASON_CHARS - 3] + "..."
        if record and self.writer is not None and rid is not None:
            self.writer.append({"id": rid, "hash": hash_, "op": op, "status": status, "code": code, "http": http,
                                "reason": reason, "time": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        return {"id": rid, "version": version, "op": op, "status": status, "code": code, "reason": reason,
                "did": did or []}

    async def one(self, req: Any) -> dict[str, Any]:
        raw_op = req.get("op") if isinstance(req, dict) else None
        h = content_hash(raw_op, req.get("params") if isinstance(req, dict) else None)
        try:
            op, params = check(req, self.today)
        except PaceError as err:
            return self._result(req, raw_op if raw_op in OPS else None, "refused", err.code, err.message,
                                hash_=h, record=True)
        rid = req["id"]
        if rid in self.ids:
            return self._result(req, op, "refused", "bad_args", f"duplicate id in this file: {rid}", hash_=h,
                                record=True)
        self.ids.add(rid)
        seen = self.seen.get((rid, h), _Seen())
        if seen.applied:
            return self._result(req, op, "applied", reason="already applied")
        if seen.failures >= MAX_ATTEMPTS:
            return self._result(req, op, "refused", reason=_GAVE_UP)
        if seen.gave_up:
            since = f": failing since {seen.first_failed.isoformat()}" if seen.first_failed else " earlier"
            return self._result(req, op, "refused", reason=f"gave up{since}")
        targets = _targets(op, params)
        if targets & self.failed_targets:
            return self._result(req, op, "failed",
                                reason="not attempted: an earlier request for the same target failed in this run")
        weight_key = (params["kg"], params["date"]) if op == "weight.add" else None
        if weight_key in self.weights:
            return self._result(req, op, "applied", reason=f"duplicate of {self.weights[weight_key]}")

        did: list[Any] = []
        handler = {"shoe.set": self.shoe_set, "effort.set": self.effort_set, "weight.add": self.weight_add}[op]
        try:
            note = await handler(params, did)
        except PaceError as err:
            if err.code == "auth_required":
                raise
            failure: PaceError = err
        except Exception as exc:  # noqa: BLE001 -- one odd Garmin answer must not lose the run's results
            failure = PaceError("internal", f"{type(exc).__name__}: {exc}")
        else:
            if weight_key is not None:
                self.weights[weight_key] = rid
            status = "planned" if self.dry_run and did else "applied"
            return self._result(req, op, status, reason=note, did=did, hash_=h, record=True)

        if failure.code == "deferred":
            self.failed_targets |= targets
            return self._result(req, op, "failed", failure.code, f"not attempted: {failure.message}", did)
        if failure.code in REFUSED_CODES:
            return self._result(req, op, "refused", failure.code, failure.message, did, failure.http, h, True)
        if _counts(failure.code, failure.http) and seen.failures + 1 >= MAX_ATTEMPTS:
            return self._result(req, op, "refused", failure.code, f"{_GAVE_UP}: {failure.message}", did,
                                failure.http, h, True)
        if seen.first_failed is not None and seen.first_failed <= self.today - timedelta(days=MAX_FAILING_DAYS):
            return self._result(req, op, "refused", failure.code,
                                f"gave up: failing since {seen.first_failed.isoformat()}: {failure.message}", did,
                                failure.http, h, True)
        self.failed_targets |= targets
        return self._result(req, op, "failed", failure.code, failure.message, did, failure.http, h, True)


async def apply_requests(reqs: list[Any], *, dry_run: bool, today: date) -> list[dict[str, Any]]:
    """Apply the requests in press order; one result per request, in that order.

    Raises PaceError(auth_required) when Garmin login fails: the outcomes logged so far stay logged.
    With `dry_run`, the log is read but never written, and nothing is written to Garmin."""
    path = log_path()
    with nullcontext() if dry_run else store.locked(path):
        seen = _load_log(path)
        writer = None if dry_run else _LogWriter(path)
        cache = Cache()
        try:
            run = _Run(dry_run=dry_run, today=today, seen=seen, writer=writer, cache=cache)
            with ratelimit.budget(MAX_CALLS, MAX_SECONDS):
                return [await run.one(r) for r in _ordered(reqs)]
        finally:
            cache.close()
