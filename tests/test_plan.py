"""Plan model: sessions, blocks, validation, stale, archive, corruption."""

import json
from datetime import date

import pytest

from pace import plan
from pace.envelope import PaceError

TODAY = date(2026, 11, 17)
WK = {"name": "Easy", "sport": "running", "steps": [{"type": "interval", "duration": "30m"}]}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    return tmp_path


def _s(**kw):
    return {"date": "2026-11-25", "sport": "running", "title": "Easy", "intensity": "easy", **kw}


def _fresh():
    return plan.load()


def test_add_session_defaults_and_ids() -> None:
    d = _fresh()
    s = plan.add_sessions(d, _s())[0]
    assert s["id"] == 1 and s["status"] == "planned" and s["stale"] is False
    assert s["garmin"] is None and s["pushed_as"] is None and s["activity_id"] is None
    assert s["target"] == {"duration_s": None, "distance_m": None, "elevation_m": None}
    assert s["block"] is None and s["workout"] is None and s["notes"] == "" and s["purpose"] == ""
    two = plan.add_sessions(d, [_s(), _s()])
    assert [x["id"] for x in two] == [2, 3] and d["next_id"] == 4


def test_batch_add_is_all_or_nothing() -> None:
    d = _fresh()
    with pytest.raises(PaceError) as exc:
        plan.add_sessions(d, [_s(), _s(sport="rowing")])
    assert exc.value.code == "bad_args" and "1" in str(exc.value)
    assert d["sessions"] == [] and d["next_id"] == 1


def test_workout_validation() -> None:
    d = _fresh()
    assert plan.add_sessions(d, _s(workout=WK))[0]["workout"] == WK
    with pytest.raises(PaceError):
        plan.add_sessions(d, _s(sport="strength", workout={**WK, "sport": "running"}))
    with pytest.raises(PaceError):
        plan.add_sessions(d, _s(workout={**WK, "sport": "cycling"}))
    with pytest.raises(PaceError) as exc:
        plan.add_sessions(d, _s(workout={"name": "x", "sport": "running", "steps": [{"type": "bogus"}]}))
    assert "steps" in str(exc.value)
    sid = d["sessions"][0]["id"]
    with pytest.raises(PaceError):
        plan.set_session(d, sid, {"sport": "strength"}, today=TODAY)
    assert plan.set_session(d, sid, {"sport": "strength", "workout": None}, today=TODAY)["sport"] == "strength"
    with pytest.raises(PaceError):
        plan.add_sessions(d, _s(target={"duration_s": True}))


def test_target_validation() -> None:
    d = _fresh()
    for bad in ({"duration_s": 0}, {"distance_m": -1}, {"elevation_m": "5"}, {"bogus": 1}):
        with pytest.raises(PaceError):
            plan.add_sessions(d, _s(target=bad))
    s = plan.add_sessions(d, _s(target={"duration_s": 1800, "distance_m": 5000.5}))[0]
    assert s["target"] == {"duration_s": 1800, "distance_m": 5000.5, "elevation_m": None}


def test_stale_follows_snapshot() -> None:
    d = _fresh()
    s = plan.add_sessions(d, _s(workout=WK))[0]
    s["garmin"] = {"workout_id": 111, "schedule_id": 222}
    s["pushed_as"] = {"date": "2026-11-25", "workout": WK}
    s["status"] = "pushed"
    assert plan.set_session(d, s["id"], {"notes": "x"}, today=TODAY)["stale"] is False
    assert plan.set_session(d, s["id"], {"date": "2026-11-26"}, today=TODAY)["stale"] is True
    assert plan.set_session(d, s["id"], {"date": "2026-11-25"}, today=TODAY)["stale"] is False
    assert plan.set_session(d, s["id"], {"workout": {**WK, "name": "Other"}}, today=TODAY)["stale"] is True


def test_status_rules() -> None:
    d = _fresh()
    s = plan.add_sessions(d, _s())[0]
    with pytest.raises(PaceError):
        plan.set_session(d, s["id"], {"status": "pushed"}, today=TODAY)
    with pytest.raises(PaceError) as exc:
        plan.set_session(d, s["id"], {"status": "done"}, today=TODAY)
    assert exc.value.code == "bad_args"
    assert plan.set_session(d, s["id"], {"status": "skipped"}, today=TODAY)["status"] == "skipped"
    s["garmin"] = {"workout_id": 1, "schedule_id": None}
    s["pushed_as"] = {"date": s["date"], "workout": None}
    with pytest.raises(PaceError) as exc:
        plan.set_session(d, s["id"], {"status": "planned"}, today=TODAY)
    assert "unpush" in (exc.value.hint or "")
    s["garmin"] = None
    s["date"] = "2026-11-10"
    assert plan.set_session(d, s["id"], {"status": "done"}, today=TODAY)["status"] == "done"


def test_never_settable_fields() -> None:
    d = _fresh()
    s = plan.add_sessions(d, _s())[0]
    for key in ("id", "garmin", "stale", "pushed_as"):
        with pytest.raises(PaceError):
            plan.set_session(d, s["id"], {key: 5}, today=TODAY)


def test_remove_refused_while_on_garmin() -> None:
    d = _fresh()
    a, b = plan.add_sessions(d, [_s(), _s()])
    b["garmin"] = {"workout_id": 1, "schedule_id": None}
    with pytest.raises(PaceError) as exc:
        plan.remove_sessions(d, [a["id"], b["id"]])
    assert exc.value.code == "bad_args"
    assert len(d["sessions"]) == 2
    b["garmin"] = None
    assert [x["id"] for x in plan.remove_sessions(d, [a["id"], b["id"]])] == [a["id"], b["id"]]
    with pytest.raises(PaceError) as exc:
        plan.remove_sessions(d, [99])
    assert exc.value.code == "not_found" and "plan show" in exc.value.hint


def test_new_plan(tmp_path) -> None:
    d = _fresh()
    assert plan.new_plan(d, name="First", goal=None, event_date=None, today=TODAY) is None
    s = plan.add_sessions(d, _s(date="2026-11-30"))[0]
    s["garmin"] = {"workout_id": 1, "schedule_id": None}
    with pytest.raises(PaceError) as exc:
        plan.new_plan(d, name="x", goal=None, event_date=None, today=TODAY)
    assert exc.value.code == "bad_args" and "unpush" in exc.value.hint
    s["date"] = "2026-11-10"
    path = plan.new_plan(d, name="../Spring HM!", goal="sub 1:40", event_date="2027-03-21", today=date(2026, 11, 20))
    assert path == str(tmp_path / "plans" / "2026-11-17-first.json")
    assert json.loads((tmp_path / "plans" / "2026-11-17-first.json").read_text())["sessions"]
    assert d["sessions"] == [] and d["next_id"] == 2
    assert d["plan"] == {"name": "../Spring HM!", "goal": "sub 1:40", "event_date": "2027-03-21",
                         "created": "2026-11-20", "updated": "2026-11-20"}
    plan.add_sessions(d, _s())
    p2 = plan.new_plan(d, name="../Spring HM!", goal=None, event_date=None, today=date(2026, 11, 20))
    assert p2 == str(tmp_path / "plans" / "2026-11-20-spring-hm.json")
    plan.add_sessions(d, _s())
    p3 = plan.new_plan(d, name="Spring HM", goal=None, event_date=None, today=date(2026, 11, 20))
    assert p3 == str(tmp_path / "plans" / "2026-11-20-spring-hm-2.json")


def test_new_plan_archive_slug_fallback(tmp_path) -> None:
    d = _fresh()
    d["plan"] = {"name": "!!!"}
    d["sessions"] = []
    d["blocks"] = [{"id": "b1"}]
    path = plan.new_plan(d, name="n", goal=None, event_date=None, today=TODAY)
    assert path == str(tmp_path / "plans" / "2026-11-17-plan.json")


def test_blocks() -> None:
    d = _fresh()
    good = {"name": "Base", "phase": "base", "start": "2026-11-17", "end": "2026-12-14", "focus": "volume"}
    for bad in ({**good, "phase": "x"}, {**good, "start": "2026-12-20"}, {**good, "name": ""}):
        with pytest.raises(PaceError):
            plan.add_block(d, bad)
    assert d["next_block_id"] == 1
    b1 = plan.add_block(d, good)
    b2 = plan.add_block(d, good)
    assert (b1["id"], b2["id"]) == ("b1", "b2")
    plan.remove_block(d, "b2")
    assert plan.add_block(d, good)["id"] == "b3"
    plan.add_sessions(d, _s(block="b1"))
    with pytest.raises(PaceError):
        plan.remove_block(d, "b1")
    with pytest.raises(PaceError):
        plan.add_sessions(d, _s(block="b9"))
    assert plan.set_block(d, "b1", {"focus": "x"})["focus"] == "x"
    with pytest.raises(PaceError):
        plan.set_block(d, "b1", {"end": "2026-01-01"})
    with pytest.raises(PaceError) as exc:
        plan.set_block(d, "b9", {})
    assert exc.value.code == "not_found" and "plan show" in exc.value.hint


def test_set_meta() -> None:
    d = _fresh()
    plan.set_meta(d, "goal", "x")
    plan.set_meta(d, "event_date", "2027-03-21")
    assert d["plan"]["goal"] == "x" and d["plan"]["event_date"] == "2027-03-21" and "updated" in d["plan"]
    with pytest.raises(PaceError):
        plan.set_meta(d, "bogus", "x")


def test_corrupt_and_backfill(tmp_path) -> None:
    p = tmp_path / "plan.json"
    p.write_text("{nope")
    with pytest.raises(PaceError) as exc:
        plan.load()
    assert exc.value.code == "internal" and str(p) in exc.value.hint
    assert p.read_text() == "{nope"
    p.write_text(json.dumps({"plan": {}, "blocks": "x", "sessions": [], "next_id": 1}))
    with pytest.raises(PaceError) as exc:
        plan.load()
    assert exc.value.code == "internal"
    p.unlink()
    with plan.transaction() as d:
        plan.add_sessions(d, [_s(), _s()])
    raw = json.loads(p.read_text())
    raw["next_id"] = 1
    raw["next_block_id"] = 1
    raw["blocks"] = [{"id": "b4"}]
    p.write_text(json.dumps(raw))
    d = plan.load()
    assert d["next_id"] == 3 and d["next_block_id"] == 5


def test_sessions_in_orders_by_date_then_id() -> None:
    d = _fresh()
    plan.add_sessions(d, [_s(date="2026-11-27"), _s(date="2026-11-25"), _s(date="2026-11-25"), _s(date="2026-12-30")])
    out = plan.sessions_in(d, date(2026, 11, 25), date(2026, 11, 27))
    assert [s["id"] for s in out] == [2, 3, 1]


def test_past_session_with_garmin_can_be_skipped_or_done() -> None:
    d = _fresh()
    s = plan.add_sessions(d, _s(date="2026-11-16"))[0]
    s["garmin"] = {"workout_id": 1, "schedule_id": 2}
    s["status"] = "pushed"
    s["pushed_as"] = {"date": "2026-11-16", "workout": None}
    out = plan.set_session(d, s["id"], {"status": "skipped"}, today=TODAY)
    assert out["status"] == "skipped" and out["garmin"] == {"workout_id": 1, "schedule_id": 2}
    assert plan.set_session(d, s["id"], {"status": "done"}, today=TODAY)["status"] == "done"
    with pytest.raises(PaceError) as exc:  # back to planned would hide the Garmin workout
        plan.set_session(d, s["id"], {"status": "planned"}, today=TODAY)
    assert "unpush" in (exc.value.hint or "")
    f = plan.add_sessions(d, _s(date="2026-11-17"))[0]  # today is not past
    f["garmin"] = {"workout_id": 3, "schedule_id": 4}
    f["pushed_as"] = {"date": "2026-11-17", "workout": None}
    with pytest.raises(PaceError):
        plan.set_session(d, f["id"], {"status": "skipped"}, today=TODAY)


def test_new_plan_blocks_on_pushed_date_still_ahead() -> None:
    d = _fresh()
    s = plan.add_sessions(d, _s(date="2026-11-16"))[0]  # moved into the past after the push
    s["garmin"] = {"workout_id": 1, "schedule_id": 2}
    s["pushed_as"] = {"date": "2026-11-20", "workout": None}
    with pytest.raises(PaceError) as exc:
        plan.new_plan(d, name="x", goal=None, event_date=None, today=TODAY)
    assert exc.value.code == "bad_args" and str(s["id"]) in exc.value.message
    s["pushed_as"]["date"] = "2026-11-10"
    assert plan.new_plan(d, name="x", goal=None, event_date=None, today=TODAY)


def test_archive_name_ignores_invalid_created(tmp_path) -> None:
    d = _fresh()
    d["plan"] = {"name": "Old", "created": "../x"}
    path = plan.new_plan(d, name="n", goal=None, event_date=None, today=TODAY)
    assert path == str(tmp_path / "plans" / "2026-11-17-old.json")
    d["plan"]["created"] = "2026-13-45"
    path = plan.new_plan(d, name="n", goal=None, event_date=None, today=TODAY)
    assert path == str(tmp_path / "plans" / "2026-11-17-n.json")
