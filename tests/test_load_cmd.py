import json

import pytest
from typer.testing import CliRunner

from pace.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")


@pytest.fixture
def api(monkeypatch):
    calls = []

    async def safe_call(method, *args, **kwargs):
        calls.append((method, args))
        return True, [{"activityId": 1, "startTimeLocal": "2026-11-16 07:00:00", "activityType":
                       {"typeKey": "running"}, "distance": 10000, "movingDuration": 3600}], None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    return calls


def _run(*args):
    res = runner.invoke(app, ["load", *args])
    assert res.stdout.count("\n") == 1, res.stdout
    return res.exit_code, json.loads(res.stdout)


def test_one_activities_call_clipped_to_today(api) -> None:
    from datetime import date, timedelta
    code, doc = _run("--weeks", "2", "--weeks-ahead", "1")
    assert code == 0 and doc["command"] == "load"
    start = date.fromisoformat(doc["data"]["range"]["from"])
    assert api == [("get_activities_by_date", ((start - timedelta(days=6)).isoformat(), date.today().isoformat(), None))]
    assert doc["data"]["sport"] == "running" and doc["data"]["weeks"]


@pytest.mark.parametrize("args", [["--sport", "bogus"], ["--weeks", "0"], ["--weeks-ahead", "-1"]])
def test_bad_args(api, args) -> None:
    code, doc = _run(*args)
    assert code == 2 and doc["error"]["code"] == "bad_args" and api == []


def _plan(sessions):
    from pace import plan as plan_store
    from pace.store import save_json
    save_json(plan_store.plan_path(), {**plan_store.EMPTY, "sessions": sessions})


def _sess(i, d, **kw):
    return {"id": i, "date": d, "title": f"S{i}", "sport": "running", "intensity": "easy", "status": "planned",
            "target": {"duration_s": 3600, "distance_m": 10000}, "garmin": None, "activity_id": None, **kw}


def test_export_shape_and_remaining(api, tmp_path) -> None:
    from datetime import date, timedelta
    today = date.today()
    mon = today - timedelta(days=today.weekday())
    _plan([_sess(1, (mon + timedelta(days=14)).isoformat())])
    out = tmp_path / "x" / "dash.json"
    code, doc = _run("--weeks", "2", "--export", str(out))
    assert code == 0 and doc["data"]["path"] == str(out)
    f = json.loads(out.read_text())
    assert f["rows"] and doc["data"]["rows"] == len(f["rows"])
    assert [m["label"] for m in f["meta"]] == ["Generated", "Command"]
    assert f["meta"][1]["value"] == "pace load --export"
    weeks = [r for r in f["rows"] if r["table"] == "week"]
    assert {r["sport"] for r in weeks} == {"running", "all"}
    run = [r for r in weeks if r["sport"] == "running"]
    assert run[0]["remaining_km"] == 0 and run[0]["planned_km"] is None
    cur = next(r for r in run if r["start"] == mon.isoformat())
    assert cur["remaining_km"] == round(cur["projected_km"] - cur["actual_km"], 2)
    fut = run[-1]
    assert fut["start"] == (mon + timedelta(days=14)).isoformat()
    assert fut["remaining_km"] == fut["projected_km"] == 10.0 and fut["actual_km"] is None
    assert fut["missing_km"] == 0
    days = [r for r in f["rows"] if r["table"] == "day" and r["sport"] == "running"]
    assert all(r["projected"] == (r["date"] > today.isoformat()) for r in days)
    assert [r for r in f["rows"] if r["table"] == "next"][0]["title"] == "S1"


def test_export_next_skips_rest_skipped_and_done_today(api, tmp_path) -> None:
    from datetime import date, timedelta
    today = date.today()
    nxt = lambda: [r for r in json.loads(out.read_text())["rows"] if r["table"] == "next"]  # noqa: E731
    out = tmp_path / "d.json"
    tomorrow = (today + timedelta(days=1)).isoformat()
    _plan([_sess(1, today.isoformat(), intensity="rest"), _sess(2, today.isoformat(), status="skipped"),
           _sess(3, today.isoformat(), activity_id=1), _sess(4, tomorrow, title="Tomorrow")])
    _run("--weeks", "1", "--export", str(out))
    got = nxt()
    assert len(got) == 1 and got[0]["title"] == "Tomorrow"
    _plan([_sess(1, today.isoformat(), intensity="rest")])
    _run("--weeks", "1", "--export", str(out))
    assert nxt() == []


def test_export_done_today_by_activity(monkeypatch, tmp_path) -> None:
    from datetime import date, timedelta
    today = date.today()

    async def safe_call(method, *args, **kwargs):
        return True, [{"activityId": 7, "startTimeLocal": f"{today.isoformat()} 07:00:00",
                       "activityType": {"typeKey": "running"}, "distance": 10000, "movingDuration": 3600}], None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    _plan([_sess(1, today.isoformat(), title="Done"), _sess(2, (today + timedelta(days=1)).isoformat(), title="Next")])
    out = tmp_path / "e.json"
    _run("--weeks", "1", "--export", str(out))
    nxt = [r for r in json.loads(out.read_text())["rows"] if r["table"] == "next"]
    assert [r["title"] for r in nxt] == ["Next"]


def test_export_rejects_sport(api, tmp_path) -> None:
    code, doc = _run("--export", str(tmp_path / "a.json"), "--sport", "all")
    assert code == 2 and doc["error"]["code"] == "bad_args" and api == []
    assert not (tmp_path / "a.json").exists()
