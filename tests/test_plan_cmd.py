import json
from datetime import date, timedelta

import pytest
from typer.testing import CliRunner

from pace.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")
    return tmp_path


def _invoke(*args, input=None):
    res = runner.invoke(app, ["plan", *args], input=input)
    assert res.stdout.count("\n") == 1, res.stdout
    return res.exit_code, json.loads(res.stdout)


def _run(*args, input=None):
    code, doc = _invoke(*args, input=input)
    assert code == 0 and doc["ok"], doc
    return doc


def _s(day, title="Easy run", **kw):
    return {"date": day, "sport": "running", "title": title, "intensity": "easy", **kw}


def test_add_show_set_remove_round_trip(tmp_path) -> None:
    f = tmp_path / "s.json"
    f.write_text(json.dumps([_s("2026-11-20"), _s("2026-12-05", "Long")]))
    doc = _run("add", str(f))
    assert [s["id"] for s in doc["data"]["sessions"]] == [1, 2]
    doc = _run("show", "--from", "2026-11-20", "--to", "2026-11-30")
    assert doc["command"] == "plan show"
    assert [s["id"] for s in doc["data"]["sessions"]] == [1]
    assert doc["data"]["blocks"] == []
    doc = _run("set", "1", "--duration", "50m", "--distance", "10km", "--elevation", "120")
    assert doc["data"]["session"]["target"] == {"duration_s": 3000, "distance_m": 10000.0, "elevation_m": 120.0}
    doc = _run("set", "1", "--duration", "none")
    assert doc["data"]["session"]["target"]["duration_s"] is None
    assert doc["data"]["session"]["target"]["distance_m"] == 10000.0
    doc = _run("remove", "1", "2")
    assert [s["id"] for s in doc["data"]["removed"]] == [1, 2]
    assert _run("show", "--from", "2026-01-01", "--to", "2027-01-01")["data"]["sessions"] == []


def test_show_without_file_is_empty(tmp_path) -> None:
    doc = _run("show")
    assert doc["data"] == {"plan": {}, "blocks": [], "sessions": []}
    assert not (tmp_path / "plan.json").exists()


def test_show_default_range_uses_past_and_ahead(tmp_path) -> None:
    today = date.today()
    days = [today + timedelta(days=d) for d in (-8, -7, 28, 29)]
    f = tmp_path / "s.json"
    f.write_text(json.dumps([_s(d.isoformat()) for d in days]))
    _run("add", str(f))
    got = [s["date"] for s in _run("show")["data"]["sessions"]]
    assert got == [days[1].isoformat(), days[2].isoformat()]
    got = [s["date"] for s in _run("show", "--past", "9", "--ahead", "0")["data"]["sessions"]]
    assert got == [days[0].isoformat(), days[1].isoformat()]


def test_show_inverted_range_is_bad_args() -> None:
    code, doc = _invoke("show", "--from", "2026-12-01", "--to", "2026-11-01")
    assert code == 2 and doc["error"]["code"] == "bad_args"


def test_add_from_stdin_list() -> None:
    doc = _run("add", "-", input=json.dumps([_s("2026-11-20"), _s("2026-11-21")]))
    assert len(doc["data"]["sessions"]) == 2


def test_new_archives_returns_path(tmp_path) -> None:
    doc = _run("new", "--name", "Spring", "--goal", "half", "--event-date", "2027-03-01")
    assert doc["data"]["archived"] is None
    assert doc["data"]["plan"]["name"] == "Spring"
    _run("add", "-", input=json.dumps(_s("2026-11-20")))
    doc = _run("new", "--name", "Autumn")
    archived = doc["data"]["archived"]
    assert archived and (tmp_path / "plans").exists()
    assert json.loads(open(archived).read())["plan"]["name"] == "Spring"


def test_meta_and_blocks(tmp_path) -> None:
    _run("new", "--name", "P")
    assert _run("meta", "goal", "sub 1:45")["data"]["plan"]["goal"] == "sub 1:45"
    doc = _run("block", "add", "--name", "Base", "--phase", "base", "--start", "2026-11-01", "--end", "2026-11-30")
    assert doc["data"]["block"]["id"] == "b1"
    doc = _run("block", "set", "b1", "--focus", "aerobic")
    assert doc["data"]["block"]["focus"] == "aerobic"
    _run("add", "-", input=json.dumps(_s("2026-11-20")))
    assert _run("set", "1", "--block", "b1")["data"]["session"]["block"] == "b1"
    assert _run("set", "1", "--block", "none")["data"]["session"]["block"] is None
    assert _run("block", "remove", "b1")["data"]["removed"]["id"] == "b1"


def test_set_workout_file_and_clear(tmp_path) -> None:
    _run("add", "-", input=json.dumps(_s("2026-11-20")))
    f = tmp_path / "w.json"
    f.write_text(json.dumps({"name": "Easy", "sport": "running", "steps": [{"type": "interval", "duration": "30m"}]}))
    doc = _run("set", "1", "--workout", str(f))
    assert doc["data"]["session"]["workout"]["name"] == "Easy"
    assert _run("set", "1", "--workout", "none")["data"]["session"]["workout"] is None


def test_set_status_and_fields() -> None:
    _run("add", "-", input=json.dumps(_s("2026-11-20")))
    doc = _run("set", "1", "--status", "skipped", "--title", "T", "--notes", "n", "--activity", "123")
    s = doc["data"]["session"]
    assert (s["status"], s["title"], s["notes"], s["activity_id"]) == ("skipped", "T", "n", 123)
    assert _run("set", "1", "--activity", "none")["data"]["session"]["activity_id"] is None
    code, doc = _invoke("set", "1", "--status", "pushed")
    assert code == 2


def test_set_without_changes_is_bad_args() -> None:
    _run("add", "-", input=json.dumps(_s("2026-11-20")))
    code, doc = _invoke("set", "1")
    assert code == 2 and doc["error"]["code"] == "bad_args"


def test_bad_session_is_bad_args_exit_2() -> None:
    code, doc = _invoke("add", "-", input=json.dumps({"date": "2026-11-20", "sport": "running"}))
    assert code == 2 and doc["error"]["code"] == "bad_args"
    code, doc = _invoke("add", "-", input="not json")
    assert code == 2 and doc["error"]["code"] == "bad_args"
