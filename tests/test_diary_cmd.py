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


def _doc(res):
    assert res.stdout.count("\n") == 1, res.stdout
    return json.loads(res.stdout)


def _run(*args):
    return _doc(runner.invoke(app, ["diary", *args]))


def test_show_empty(tmp_path) -> None:
    doc = _run("show")
    assert doc["ok"] and doc["command"] == "diary show"
    assert doc["data"] == {"profile": {}, "active": [], "due": [], "entries": []}
    assert doc["cache"] is None and doc["shape"] is None and doc["truncated"] is False
    assert not (tmp_path / "diary.json").exists()


def test_set_add_remove_round_trip(tmp_path) -> None:
    doc = _run("set", "goal", "sub-1:45 half")
    assert doc["data"]["profile"]["goal"] == "sub-1:45 half"
    doc = _run("add", "--kind", "constraint", "--date", "2026-09-01", "--tag", "schedule",
               "no", "long", "runs", "on", "Mondays")
    assert doc["data"]["entry"] == {"id": 1, "date": "2026-09-01", "kind": "constraint",
                                    "text": "no long runs on Mondays", "tags": ["schedule"],
                                    "refs": [], "due": None, "resolved": None}
    assert _run("add", "--kind", "recommendation", "Easy", "week")["data"]["entry"]["id"] == 2
    doc = _run("show", "--kind", "recommendation")
    assert [e["id"] for e in doc["data"]["entries"]] == [2]
    assert [e["id"] for e in doc["data"]["active"]] == [1]
    assert _run("remove", "2")["data"]["removed"]["id"] == 2
    res = runner.invoke(app, ["diary", "remove", "2"])
    assert res.exit_code == 1 and _doc(res)["error"]["code"] == "not_found"
    assert json.loads((tmp_path / "diary.json").read_text())["next_id"] == 3


def test_unset(tmp_path) -> None:
    _run("set", "goal", "x")
    doc = _run("unset", "goal")
    assert "goal" not in doc["data"]["profile"]
    res = runner.invoke(app, ["diary", "unset", "weight"])
    assert res.exit_code == 2 and _doc(res)["error"]["code"] == "bad_args"


def test_bad_kind_and_key_are_bad_args() -> None:
    res = runner.invoke(app, ["diary", "add", "--kind", "rant", "x"])
    assert res.exit_code == 2 and _doc(res)["error"]["code"] == "bad_args"
    res = runner.invoke(app, ["diary", "set", "weight", "70"])
    assert res.exit_code == 2


def test_corrupt_diary_is_internal_with_hint(tmp_path) -> None:
    (tmp_path / "diary.json").write_text("{oops")
    res = runner.invoke(app, ["diary", "show"])
    doc = _doc(res)
    assert res.exit_code == 1 and doc["error"]["code"] == "internal" and "diary.json" in doc["error"]["hint"]
    assert (tmp_path / "diary.json").read_text() == "{oops"


def test_add_with_refs_due_and_relative_dates() -> None:
    res = runner.invoke(app, ["diary", "add", "--kind", "feedback", "--date", "-1d", "--ref", "activity:5",
                              "--ref", "session:12", "--due", "+7d", "felt", "heavy"])
    assert res.exit_code == 0, res.stdout
    entry = _doc(res)["data"]["entry"]
    assert entry["date"] == (date.today() - timedelta(days=1)).isoformat()
    assert entry["due"] == (date.today() + timedelta(days=7)).isoformat()
    assert entry["refs"] == ["activity:5", "session:12"]
    assert entry["text"] == "felt heavy"


def test_set_due_sets_and_clears() -> None:
    _run("add", "--kind", "decision", "chose to race")
    assert _run("set-due", "1", "--date", "2026-12-01")["data"]["entry"]["due"] == "2026-12-01"
    assert _run("set-due", "1", "--clear")["data"]["entry"]["due"] is None
    for extra in (["--date", "2026-12-01", "--clear"], []):
        res = runner.invoke(app, ["diary", "set-due", "1", *extra])
        assert res.exit_code == 2 and _doc(res)["error"]["code"] == "bad_args"
    res = runner.invoke(app, ["diary", "set-due", "99", "--clear"])
    assert res.exit_code == 1 and _doc(res)["error"]["code"] == "not_found"


def test_resolve_command() -> None:
    _run("add", "--kind", "injury", "--date", "2026-04-01", "sore knee")
    entry = _run("resolve", "1", "--date", "2026-04-05")["data"]["entry"]
    assert entry["resolved"] == "2026-04-05"
    assert _run("show")["data"]["active"] == []
    assert _run("resolve", "1", "--reopen")["data"]["entry"]["resolved"] is None
    assert [e["id"] for e in _run("show")["data"]["active"]] == [1]
    res = runner.invoke(app, ["diary", "resolve", "1", "--date", "2026-04-05", "--reopen"])
    assert res.exit_code == 2


def test_text_starting_with_dash() -> None:
    doc = _run("add", "--kind", "feedback", "--", "-5", "kg", "on", "the", "scale")
    assert doc["data"]["entry"]["text"] == "-5 kg on the scale"


def test_show_truncated() -> None:
    for i in range(3):
        _run("add", "--kind", "note", f"n{i}")
    doc = _run("show", "--limit", "2")
    assert doc["truncated"] is True and len(doc["data"]["entries"]) == 2


def test_show_ref_filter() -> None:
    _run("add", "--kind", "feedback", "--ref", "activity:5", "a")
    _run("add", "--kind", "feedback", "b")
    doc = _run("show", "--ref", "activity:5")
    assert [e["text"] for e in doc["data"]["entries"]] == ["a"]
    res = runner.invoke(app, ["diary", "show", "--ref", "bogus"])
    assert res.exit_code == 2 and _doc(res)["error"]["code"] == "bad_args"
