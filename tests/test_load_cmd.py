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
