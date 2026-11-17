"""plan push / unpush: ordering, persistence after each landed step, locking, dry-run."""

import asyncio
import fcntl
import json
import threading
from datetime import date

import pytest
from typer.testing import CliRunner

from pace import plan, planpush
from pace.api import ApiError
from pace.cli import app
from pace.envelope import PaceError

TODAY = date(2026, 11, 17)
WK = {"name": " Easy ", "sport": "running", "steps": [{"type": "interval", "duration": "30m"}]}
runner = CliRunner()


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")
    return tmp_path


def _kind(method_name, args, kwargs):
    if method_name == "upload_workout":
        return "upload"
    if method_name == "connectapi" and kwargs.get("method") == "DELETE":
        return "delete"
    if method_name == "connectapi" and args[0].startswith("/workout-service/schedule/"):
        return "schedule"
    return method_name


@pytest.fixture
def fake_api(monkeypatch):
    """Answers per call kind (upload, schedule, delete); `fail` maps a 1-based call number
    or a kind to an ApiError; `on` maps a kind to a callback run before it answers."""
    state = {"calls": [], "fail": {}, "on": {}, "next_workout": 77, "next_schedule": 9}

    async def safe_call(method_name, *args, **kwargs):
        kind = _kind(method_name, args, kwargs)
        state["calls"].append((kind, method_name, args, kwargs))
        if kind in state["on"]:
            state["on"][kind]()
        err = state["fail"].get(len(state["calls"])) or state["fail"].get(kind)
        if err is not None:
            return False, None, err
        if kind == "upload":
            state["next_workout"] += 1
            return True, {"workoutId": state["next_workout"] - 1}, None
        if kind == "schedule":
            state["next_schedule"] += 1
            return True, {"workoutScheduleId": state["next_schedule"] - 1}, None
        return True, None, None

    monkeypatch.setattr("pace.writes.safe_call", safe_call)
    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    return state


def _kinds(fake):
    return [c[0] for c in fake["calls"]]


def _add(*items):
    with plan.transaction() as data:
        return [s["id"] for s in plan.add_sessions(data, list(items), today=TODAY)]


def _s(day="2026-11-18", **kw):
    return {"date": day, "sport": "running", "title": "Easy", "intensity": "easy", "workout": WK, **kw}


def _session(sid):
    return next(s for s in plan.load()["sessions"] if s["id"] == sid)


def _push(ids, *, dry_run=False, force=False):
    return asyncio.run(planpush.push_sessions(ids, dry_run=dry_run, force=force, today=TODAY))


def _unpush(ids, *, dry_run=False):
    return asyncio.run(planpush.unpush_sessions(ids, dry_run=dry_run, today=TODAY))


def test_push_one_records_ids_and_prefix(fake_api) -> None:
    (sid,) = _add(_s())
    out = _push([sid])
    assert out["done"] == 1 and out["failed"] == 0
    r = out["results"][0]
    assert r["ok"] and r["action"] == "pushed" and r["error"] is None
    assert [st["step"] for st in r["steps"]] == ["upload", "schedule"]
    assert _kinds(fake_api) == ["upload", "schedule"]
    upload = fake_api["calls"][0][2][0]
    assert upload["workoutName"] == "pace: Easy"
    assert fake_api["calls"][1][2] == ("/workout-service/schedule/77",)
    assert fake_api["calls"][1][3]["json"] == {"date": "2026-11-18"}
    s = _session(sid)
    assert s["status"] == "pushed" and s["garmin"] == {"workout_id": 77, "schedule_id": 9}
    assert s["stale"] is False and s["pushed_as"] == {"date": "2026-11-18", "workout": WK}
    assert r["garmin"] == s["garmin"] and r["session"] == s


def test_prefix_not_doubled(fake_api) -> None:
    (sid,) = _add(_s(workout={**WK, "name": "pace: Tempo"}))
    _push([sid])
    assert fake_api["calls"][0][2][0]["workoutName"] == "pace: Tempo"


def test_unchanged_pushed_is_skipped_unless_force(fake_api) -> None:
    (sid,) = _add(_s())
    _push([sid])
    fake_api["calls"].clear()
    r = _push([sid])["results"][0]
    assert r["ok"] and r["action"] == "skipped" and r["steps"] == [] and fake_api["calls"] == []
    r = _push([sid], force=True)["results"][0]
    assert r["action"] == "pushed" and _kinds(fake_api) == ["delete", "upload", "schedule"]
    assert fake_api["calls"][0][2] == ("/workout-service/workout/77",)
    assert _session(sid)["garmin"] == {"workout_id": 78, "schedule_id": 10}


def test_moved_session_repushes(fake_api) -> None:
    (sid,) = _add(_s())
    _push([sid])
    with plan.transaction() as data:
        plan.set_session(data, sid, {"date": "2026-11-20"}, today=TODAY)
    assert _session(sid)["stale"] is True
    fake_api["calls"].clear()
    _push([sid])
    assert _kinds(fake_api) == ["delete", "upload", "schedule"]
    assert fake_api["calls"][0][2] == ("/workout-service/workout/77",)
    assert fake_api["calls"][2][3]["json"] == {"date": "2026-11-20"}
    s = _session(sid)
    assert s["stale"] is False and s["garmin"] == {"workout_id": 78, "schedule_id": 10}


def test_schedule_failure_keeps_uploaded_id(fake_api) -> None:
    (sid,) = _add(_s())
    fake_api["fail"]["schedule"] = ApiError("rate_limited", "slow down", 429)
    out = _push([sid])
    r = out["results"][0]
    assert not r["ok"] and r["error"]["code"] == "rate_limited"
    assert r["garmin"] == {"workout_id": 77, "schedule_id": None}
    assert [st["step"] for st in r["steps"]] == ["upload"]
    assert "not recorded" not in r["error"]["hint"]
    assert out["failed"] == 1 and out["done"] == 0  # returned, not raised: a write landed
    s = _session(sid)
    assert s["garmin"] == {"workout_id": 77, "schedule_id": None} and s["status"] == "planned"
    fake_api["fail"].clear()
    fake_api["calls"].clear()
    _push([sid])
    assert _kinds(fake_api) == ["delete", "upload", "schedule"]
    assert fake_api["calls"][0][2] == ("/workout-service/workout/77",)
    assert _session(sid)["status"] == "pushed"


def test_edit_during_push_marks_stale(fake_api) -> None:
    (sid,) = _add(_s())

    def edit():
        with plan.transaction() as data:
            plan.set_session(data, sid, {"date": "2026-11-21"}, today=TODAY)

    fake_api["on"]["upload"] = edit
    _push([sid])
    s = _session(sid)
    assert s["date"] == "2026-11-21" and s["stale"] is True
    assert s["pushed_as"]["date"] == "2026-11-18" and s["status"] == "pushed"
    assert fake_api["calls"][1][3]["json"] == {"date": "2026-11-18"}


def test_vanished_session_reports_landed_ids(fake_api) -> None:
    (sid,) = _add(_s())

    def vanish():
        with plan.transaction() as data:
            plan.remove_sessions(data, [sid])

    fake_api["on"]["upload"] = vanish
    out = _push([sid])
    r = out["results"][0]
    assert not r["ok"] and r["garmin"] == {"workout_id": 77, "schedule_id": None}
    assert "workout delete 77" in r["error"]["hint"]
    assert _kinds(fake_api) == ["upload"]
    assert plan.load()["sessions"] == []


def test_failed_save_after_upload_reports_landed_ids(fake_api) -> None:
    (sid,) = _add(_s())
    good = plan.plan_path().read_text()

    def corrupt():
        plan.plan_path().write_text("{not json")

    fake_api["on"]["upload"] = corrupt
    r = _push([sid])["results"][0]
    assert not r["ok"] and r["error"]["code"] == "internal"
    assert r["garmin"] == {"workout_id": 77, "schedule_id": None}
    assert "workout delete 77" in r["error"]["hint"] and _kinds(fake_api) == ["upload"]
    assert plan.plan_path().read_text() == "{not json"  # never overwritten
    plan.plan_path().write_text(good)


def test_concurrent_push_waits_and_skips(fake_api, monkeypatch) -> None:
    (sid,) = _add(_s())
    _push([sid])
    fake_api["calls"].clear()
    monkeypatch.setattr(planpush, "PUSH_LOCK_TIMEOUT_S", 0.2)
    lock = plan.plan_path().with_name("plan-push.json.lock")
    with lock.open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        with pytest.raises(PaceError) as exc:
            _push([sid])
        fcntl.flock(fh, fcntl.LOCK_UN)
    assert exc.value.code == "internal" and "busy" in exc.value.message
    assert fake_api["calls"] == []
    assert fake_api["calls"] == []
    monkeypatch.setattr(planpush, "PUSH_LOCK_TIMEOUT_S", 5.0)
    with lock.open("a") as fh:  # held briefly: the push waits, then re-reads and skips
        fcntl.flock(fh, fcntl.LOCK_EX)
        threading.Timer(0.2, fcntl.flock, (fh, fcntl.LOCK_UN)).start()
        assert _push([sid])["results"][0]["action"] == "skipped"
    assert fake_api["calls"] == []


def test_batch_partial_and_all_failed(fake_api) -> None:
    a, b = _add(_s(), _s("2026-11-19"))
    fake_api["fail"][3] = ApiError("rejected", "bad workout", 400)  # b's upload
    out = _push([a, b])
    assert out["done"] == 1 and out["failed"] == 1
    assert out["results"][0]["ok"] and out["results"][1]["error"]["code"] == "rejected"
    assert _session(b)["garmin"] is None
    fake_api["calls"].clear()
    out = _push([a, b])  # a skipped, b retried: no duplicate of a
    assert [r["action"] for r in out["results"]] == ["skipped", "pushed"]
    assert _kinds(fake_api) == ["upload", "schedule"]

    c, d = _add(_s("2026-11-22"), _s("2026-11-23"))
    fake_api["fail"] = {"upload": ApiError("rejected", "bad workout", 400)}
    with pytest.raises(PaceError) as exc:
        _push([c, d])
    assert exc.value.code == "rejected"


def test_cli_envelope(fake_api) -> None:
    (sid,) = _add(_s())
    res = runner.invoke(app, ["plan", "push", str(sid), "--dry-run"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 0 and doc["ok"] and doc["command"] == "plan push"
    assert doc["args"] == {"ids": [sid], "force": False, "dry_run": True}
    assert doc["data"]["dry_run"] is True
    res = runner.invoke(app, ["plan", "push", "999"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 2 and not doc["ok"] and doc["error"]["code"] == "bad_args"
    res = runner.invoke(app, ["plan", "unpush", str(sid)])
    doc = json.loads(res.stdout)
    assert doc["ok"] and doc["data"]["results"][0]["action"] == "noop"
    assert fake_api["calls"] == []


def test_unpush(fake_api) -> None:
    a, b = _add(_s(), _s("2026-11-19"))
    _push([a])
    fake_api["calls"].clear()
    fake_api["fail"]["delete"] = ApiError("not_found", "gone", 404)
    out = _unpush([a, b])
    ra, rb = out["results"]
    assert ra["ok"] and ra["action"] == "unpushed" and [st["step"] for st in ra["steps"]] == ["delete"]
    s = _session(a)
    assert s["status"] == "planned" and s["garmin"] is None and s["pushed_as"] is None and s["stale"] is False
    assert rb["ok"] and rb["action"] == "noop" and rb["steps"] == []
    assert _kinds(fake_api) == ["delete"]


def test_unpush_other_error_keeps_garmin(fake_api) -> None:
    (sid,) = _add(_s())
    _push([sid])
    fake_api["fail"]["delete"] = ApiError("network", "down")
    with pytest.raises(PaceError) as exc:
        _unpush([sid])
    assert exc.value.code == "network"
    assert _session(sid)["garmin"] == {"workout_id": 77, "schedule_id": 9}


def test_refusals(fake_api) -> None:
    ids = _add(_s(sport="strength", workout=None), _s(workout=None), _s(status="skipped"),
               _s("2026-11-16", status="done"), _s("2026-11-16"))
    (good,) = _add(_s())
    out = _push([*ids, 999, good])  # one pushable session keeps the envelope ok
    refused = out["results"][:-1]
    assert [r["error"]["code"] for r in refused] == ["bad_args"] * 6
    assert all(r["steps"] == [] and r["action"] is None for r in refused)
    assert _kinds(fake_api) == ["upload", "schedule"]
    assert out["done"] == 1 and out["failed"] == 6
    fake_api["calls"].clear()
    past = ids[-1]
    with plan.transaction() as data:  # a past session that still has a Garmin workout
        s = next(x for x in data["sessions"] if x["id"] == past)
        s.update(garmin={"workout_id": 5, "schedule_id": 6}, status="pushed",
                 pushed_as={"date": s["date"], "workout": s["workout"]})
    with pytest.raises(PaceError) as exc:
        _unpush([past])
    assert exc.value.code == "bad_args" and fake_api["calls"] == []


def test_refusal_envelope_raises_when_all_refused(fake_api) -> None:
    (sid,) = _add(_s("2026-11-16"))
    with pytest.raises(PaceError) as exc:
        _push([sid])
    assert exc.value.code == "bad_args"


def test_dry_run_takes_no_lock(fake_api, tmp_path) -> None:
    (sid,) = _add(_s())
    assert _push([sid], dry_run=True)["results"][0]["action"] == "pushed"
    assert not (tmp_path / "plan-push.json.lock").exists() and fake_api["calls"] == []


def test_dry_run_no_calls_no_writes(fake_api) -> None:
    a, b = _add(_s(), _s("2026-11-19"))
    _push([a])
    with plan.transaction() as data:
        plan.set_session(data, a, {"date": "2026-11-20"}, today=TODAY)
    fake_api["calls"].clear()
    before = plan.plan_path().read_bytes()
    out = _push([a, b], dry_run=True)
    assert out["dry_run"] is True and fake_api["calls"] == []
    assert plan.plan_path().read_bytes() == before
    ra, rb = out["results"]
    assert [st["step"] for st in ra["steps"]] == ["delete", "upload", "schedule"]
    assert ra["steps"][0]["payload"] == {"path": "/workout-service/workout/77"}
    assert ra["steps"][1]["payload"]["workoutName"] == "pace: Easy"
    assert ra["steps"][2]["payload"]["json"] == {"date": "2026-11-20"}
    assert [st["step"] for st in rb["steps"]] == ["upload", "schedule"]
    assert all(st["id"] is None for st in ra["steps"] + rb["steps"])
    out = _unpush([a], dry_run=True)
    assert out["results"][0]["steps"][0]["payload"] == {"path": "/workout-service/workout/77"}
    assert fake_api["calls"] == [] and plan.plan_path().read_bytes() == before
