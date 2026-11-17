"""Push plan sessions to Garmin (upload + schedule) and take them off again.

Every landed Garmin write is persisted to the plan file right away, inside its own short
`plan.transaction()`, so a failure later in a session or the batch never loses an id.
The whole call holds a separate push lock, so two pace processes never push the same
session at once; the second waits, re-reads the session and sees it pushed.
"""

import copy
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from pace import plan, store
from pace.envelope import PaceError
from pace.sports import PUSHABLE
from pace.workoutwrites import delete_spec, schedule_spec, upload_spec
from pace.writes import WriteSpec, run_write

PUSH_LOCK_TIMEOUT_S = 10.0
#: Dry-run stand-in for the workout id the upload step would return.
NEW_WORKOUT_ID = "{workout_id}"


def _lock_path() -> Path:
    """Not plan.json itself: the per-step plan.transaction() locks that, and flock must never nest."""
    return plan.plan_path().with_name("plan-push.json")


def _err(exc: PaceError) -> dict[str, Any]:
    return {"code": exc.code, "http": exc.http, "message": exc.message, "hint": exc.hint}


def _result(sid: int, session: dict[str, Any] | None) -> dict[str, Any]:
    return {"id": sid, "ok": True, "action": None, "steps": [], "error": None,
            "garmin": copy.deepcopy(session["garmin"]) if session else None,
            "session": copy.deepcopy(session)}


def _find(data: dict[str, Any], sid: int) -> dict[str, Any] | None:
    return next((s for s in data["sessions"] if s["id"] == sid), None)


def _workout_name(name: str) -> str:
    name = name.strip()
    return name if name.startswith(plan.WORKOUT_PREFIX) else plan.WORKOUT_PREFIX + name


def _refusal(s: dict[str, Any] | None, sid: int, today: date, push: bool) -> PaceError | None:
    if s is None:
        return PaceError("bad_args", f"no session with id {sid}", hint="List ids with `pace plan show`.")
    if push:
        if s["status"] in ("skipped", "done"):
            return PaceError("bad_args", f"session {sid} is {s['status']}; only planned sessions are pushed")
        if s["sport"] not in PUSHABLE:
            return PaceError("bad_args", f"session {sid}: sport {s['sport']!r} cannot be pushed; only "
                             f"{', '.join(PUSHABLE)}")
        if s["workout"] is None:
            return PaceError("bad_args", f"session {sid} has no workout",
                             hint=f"Attach one: pace plan set {sid} --workout FILE")
    if s["date"] < today.isoformat():
        verb = "pushed" if push else "unpushed"
        return PaceError("bad_args", f"session {sid} is in the past ({s['date']}); past sessions are never {verb}",
                         hint="Deleting its workout would erase that day from the Garmin calendar.")
    return None


def _persist(sid: int, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Apply `mutate` to the session in a fresh transaction; re-derive stale against the file."""
    with plan.transaction() as data:
        s = _find(data, sid)
        if s is None:
            raise PaceError("not_found", f"session {sid} vanished from the plan during the push")
        mutate(s)
        s["stale"] = plan.is_stale(s)
        return copy.deepcopy(s)


async def _step(res: dict[str, Any], name: str, spec: WriteSpec, *, dry_run: bool,
                missing_ok: bool = False) -> Any:
    """Run one write and log it in `res["steps"]`; returns the Garmin id (None for dry-run/delete)."""
    if dry_run:
        res["steps"].append({"step": name, "payload": spec.payload, "id": None})
        return None
    try:
        ident = (await run_write(spec, dry_run=False)).data["id"]
    except PaceError as exc:
        if not (missing_ok and exc.code == "not_found"):
            raise
        ident = None
    res["steps"].append({"step": name, "payload": spec.payload, "id": ident})
    return ident


def _save(res: dict[str, Any], sid: int, mutate: Callable[[dict[str, Any]], None]) -> None:
    """Persist after a landed step; on failure the error names the landed ids to clean up."""
    try:
        res["session"] = _persist(sid, mutate)
    except PaceError as exc:
        g = res["garmin"]
        if not g:
            raise
        raise PaceError(exc.code, exc.message, exc.http,
                        f"Workout {g['workout_id']} is on Garmin but not recorded in the plan; remove it with "
                        f"`pace workout delete {g['workout_id']}`.") from None


def _clear(s: dict[str, Any]) -> None:
    s.update(garmin=None, status="planned", pushed_as=None)


async def _push_one(sid: int, *, dry_run: bool, force: bool, today: date) -> dict[str, Any]:
    s = _find(plan.load(), sid)
    res = _result(sid, s)
    refused = _refusal(s, sid, today, push=True)
    if refused is not None:
        return {**res, "ok": False, "error": _err(refused)}
    assert s is not None
    if s["status"] == "pushed" and not plan.is_stale(s) and not force:
        return {**res, "action": "skipped"}
    res["action"] = "pushed"
    snap = {"date": s["date"], "workout": copy.deepcopy(s["workout"])}
    try:
        if s["garmin"] is not None:
            await _step(res, "delete", delete_spec(s["garmin"]["workout_id"]), dry_run=dry_run, missing_ok=True)
            if not dry_run:
                res["garmin"] = None
                _save(res, sid, _clear)
        dsl = {**copy.deepcopy(s["workout"]), "name": _workout_name(s["workout"]["name"])}
        workout_id = await _step(res, "upload", upload_spec(dsl), dry_run=dry_run)
        if not dry_run:
            if workout_id is None:
                raise PaceError("internal", "upload response carries no workoutId",
                                hint=f"Check `pace workout list` for {dsl['name']!r} and delete it if present.")
            res["garmin"] = {"workout_id": workout_id, "schedule_id": None}

            def uploaded(x: dict[str, Any]) -> None:
                x.update(garmin=copy.deepcopy(res["garmin"]), pushed_as=copy.deepcopy(snap))

            _save(res, sid, uploaded)
        sched = schedule_spec(NEW_WORKOUT_ID if dry_run else workout_id, snap["date"])
        schedule_id = await _step(res, "schedule", sched, dry_run=dry_run)
        if not dry_run:
            if schedule_id is None:  # never `pushed` without a schedule id; the next push redoes it
                raise PaceError("internal", "schedule response carries no workoutScheduleId",
                                hint="The workout is recorded; push again to replace it.")
            res["garmin"]["schedule_id"] = schedule_id

            def scheduled(x: dict[str, Any]) -> None:
                x.update(garmin=copy.deepcopy(res["garmin"]), status="pushed")

            _save(res, sid, scheduled)
    except PaceError as exc:
        return {**res, "ok": False, "error": _err(exc)}
    return res


async def _unpush_one(sid: int, *, dry_run: bool, today: date) -> dict[str, Any]:
    s = _find(plan.load(), sid)
    res = _result(sid, s)
    refused = _refusal(s, sid, today, push=False)
    if refused is not None:
        return {**res, "ok": False, "error": _err(refused)}
    assert s is not None
    if s["garmin"] is None:
        return {**res, "action": "noop"}
    res["action"] = "unpushed"
    try:
        await _step(res, "delete", delete_spec(s["garmin"]["workout_id"]), dry_run=dry_run, missing_ok=True)
        if not dry_run:
            res["garmin"] = None
            _save(res, sid, _clear)
    except PaceError as exc:
        return {**res, "ok": False, "error": _err(exc)}
    return res


def _finish(results: list[dict[str, Any]], dry_run: bool) -> dict[str, Any]:
    """Batch result; raises the first error when every session failed and no step completed."""
    failed = [r for r in results if not r["ok"]]
    if results and len(failed) == len(results) and not any(r["steps"] for r in results):
        e = failed[0]["error"]
        raise PaceError(e["code"], e["message"], e["http"], e["hint"])
    out: dict[str, Any] = {"results": results, "done": len(results) - len(failed), "failed": len(failed)}
    if dry_run:
        out["dry_run"] = True
    return out


async def _batch(ids: list[int], dry_run: bool, one: Callable[[int], Any]) -> dict[str, Any]:
    ids = list(dict.fromkeys(ids))
    if dry_run:
        return _finish([await one(sid) for sid in ids], dry_run)
    with store.locked(_lock_path(), timeout_s=PUSH_LOCK_TIMEOUT_S):
        return _finish([await one(sid) for sid in ids], dry_run)


async def push_sessions(ids: list[int], *, dry_run: bool, force: bool, today: date) -> dict[str, Any]:
    return await _batch(ids, dry_run, lambda sid: _push_one(sid, dry_run=dry_run, force=force, today=today))


async def unpush_sessions(ids: list[int], *, dry_run: bool, today: date) -> dict[str, Any]:
    return await _batch(ids, dry_run, lambda sid: _unpush_one(sid, dry_run=dry_run, today=today))
