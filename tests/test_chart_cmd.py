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


@pytest.fixture
def api(monkeypatch):
    calls = []
    today = date.today().isoformat()

    async def safe_call(method, *args, **kwargs):
        calls.append((method, args))
        if method != "get_activities_by_date":
            raise AssertionError(method)
        return True, [{"activityId": 1, "startTimeLocal": f"{today} 07:00:00", "activityType":
                       {"typeKey": "running"}, "distance": 10000, "movingDuration": 3600}], None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    return calls


def _run(*args):
    res = runner.invoke(app, ["chart", *args])
    assert res.stdout.count("\n") == 1, res.stdout
    return res.exit_code, json.loads(res.stdout)


def test_volume_out_path(api, tmp_path) -> None:
    out = tmp_path / "sub" / "v.png"
    code, doc = _run("volume", "--weeks", "2", "--weeks-ahead", "1", "--out", str(out))
    assert code == 0 and doc["command"] == "chart"
    data = doc["data"]
    assert data["path"] == str(out) and out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert data["kind"] == "volume" and set(data["range"]) == {"from", "to"}
    assert data["summary"]["metric"] == "km" and data["summary"]["empty"] is False
    assert [c[0] for c in api] == ["get_activities_by_date"]


def test_plan_chart_one_call_no_effort(api, tmp_path) -> None:
    code, doc = _run("plan", "--out", str(tmp_path / "p.png"))
    assert code == 0
    today = date.today()
    assert doc["data"]["range"] == {"from": (today - timedelta(days=14)).isoformat(),
                                    "to": (today + timedelta(days=14)).isoformat()}
    assert [c[0] for c in api] == ["get_activities_by_date"]
    assert doc["data"]["summary"] == {"counts": {}, "unplanned": 1, "empty": False}


def test_default_names_differ_per_metric(api, tmp_path) -> None:
    _, km = _run("volume", "--weeks", "2", "--weeks-ahead", "0", "--metric", "km")
    _, hours = _run("volume", "--weeks", "2", "--weeks-ahead", "0", "--metric", "hours", "--sport", "all")
    paths = [km["data"]["path"], hours["data"]["path"]]
    assert paths[0] != paths[1]
    for p in paths:
        assert p.startswith(str(tmp_path / "charts") + "/") and p.endswith(".png")
    r = km["data"]["range"]
    assert paths[0].endswith(f"volume-km-{r['from']}-{r['to']}.png")
    assert paths[1].endswith(f"volume-hours-all-{r['from']}-{r['to']}.png")


@pytest.mark.parametrize("args", [
    ["volume", "--out", "x.jpg"],
    ["bogus"],
    ["plan", "--metric", "km"],
    ["plan", "--weeks", "3"],
    ["plan", "--sport", "all"],
    ["volume", "--past", "3"],
    ["volume", "5"],
    ["volume", "--metric", "pace"],
    ["volume", "--sport", "cycling"],
    ["volume", "--weeks", "0"],
    ["plan", "--from", "2026-11-20", "--to", "2026-11-10"],
])
def test_bad_args(api, args) -> None:
    code, doc = _run(*args)
    assert code == 2 and doc["error"]["code"] == "bad_args" and api == []


def test_sleep_and_recovery_charts(monkeypatch, tmp_path) -> None:
    calls = []

    async def safe_call(method, *args, **kwargs):
        calls.append(args[0])
        a, b = args[0].rsplit("/", 2)[-2:]
        day, items = date.fromisoformat(a), []
        while day <= date.fromisoformat(b):
            items.append({"calendarDate": day.isoformat(), "values": {
                "totalSleepTimeInSeconds": 30540, "deepTime": 2760, "lightTime": 18480, "remTime": 9300,
                "awakeTime": 180, "restingHeartRate": 45, "avgHeartRate": 52, "avgOvernightHrv": None,
                "hrv7dAverage": None, "hrvStatus": None, "sleepScore": None}})
            day += timedelta(days=1)
        return True, {"individualStats": items, "overallStats": {}}, None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    today = date.today()
    code, doc = _run("sleep", "--past", "10", "--out", str(tmp_path / "s.png"))
    assert code == 0 and doc["data"]["kind"] == "sleep"
    assert doc["data"]["range"] == {"from": (today - timedelta(days=10)).isoformat(), "to": today.isoformat()}
    assert len(doc["data"]["summary"]["nights"]) == 11 and (tmp_path / "s.png").exists()
    code, doc = _run("recovery", "--past", "10")
    assert code == 0 and doc["data"]["summary"]["hrv_available"] is False
    assert doc["data"]["path"].endswith(f"recovery-{today - timedelta(days=10)}-{today}.png")
    assert all(c.startswith("/sleep-service/stats/sleep/daily/") for c in calls)
    code, doc = _run("recovery", "--weeks", "2")
    assert code == 2 and doc["error"]["code"] == "bad_args"
