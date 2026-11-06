"""gear link / unlink writes and the gear activities read."""

import asyncio
import json

import pytest
from typer.testing import CliRunner

from pace.cache import Cache
from pace.commands.gear import app

runner = CliRunner()
U = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")


@pytest.fixture
def fake_api(monkeypatch):
    state = {"calls": [], "resp": {"uuid": U}, "err": None}

    async def safe_call(method_name, *args, **kwargs):
        state["calls"].append((method_name, args, kwargs))
        if state["err"] is not None:
            return False, None, state["err"]
        return True, state["resp"], None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    monkeypatch.setattr("pace.writes.safe_call", safe_call)
    return state


def _doc(res):
    assert res.stdout.count("\n") == 1, res.stdout
    return json.loads(res.stdout)


async def _ret_one():
    return 1


def test_link_dry_run(fake_api) -> None:
    doc = _doc(runner.invoke(app, ["link", "9", U, "--dry-run"]))
    assert doc["command"] == "gear link" and doc["args"] == {"activity_id": 9, "gear_uuid": U, "dry_run": True}
    assert doc["data"] == {"dry_run": True, "payload": {"method": "add_gear_to_activity", "gearUUID": U, "activity_id": 9}}
    assert fake_api["calls"] == []


def test_link_and_unlink_real(fake_api) -> None:
    for verb, method in (("link", "add_gear_to_activity"), ("unlink", "remove_gear_from_activity")):
        doc = _doc(runner.invoke(app, [verb, "9", U.upper()]))
        assert doc["data"] == {"action": verb, "resource": "activity_gear", "id": None,
                               "echo": {"activity_id": 9, "gear_uuid": U}}
        assert fake_api["calls"][-1] == (method, (U, 9), {})


def test_link_invalidates_gear_keys(fake_api) -> None:
    cache = Cache()
    for key in ("get_activity_gear:9", f"get_gear_activities:{U.upper()}", f"get_gear_stats:{U}", "get_gear:1"):
        asyncio.run(cache.get_or_fetch(key, _ret_one, ttl=600))
    cache.close()
    _doc(runner.invoke(app, ["link", "9", U]))
    cache = Cache()
    assert cache.invalidate("get_activity_gear:") == 0 and cache.invalidate("get_gear_activities:") == 0
    assert cache.invalidate("get_gear_stats:") == 0 and cache.invalidate("get_gear:1") == 1
    cache.close()


@pytest.mark.parametrize("argv", [["link", "9", "01234567-89ab-cdef-0123-456789abcdef"], ["link", "9", "../x"],
                                  ["unlink", "9", "Altra"], ["link", "0", U], ["link", "9", U + "0"]])
def test_link_bad_args(fake_api, argv) -> None:
    res = runner.invoke(app, argv)
    assert res.exit_code == 2 and _doc(res)["error"]["code"] == "bad_args" and fake_api["calls"] == []


def test_gear_activities_registered(fake_api) -> None:
    fake_api["resp"] = [{"activityId": 1, "activityName": "r", "activityType": {"typeKey": "running", "typeId": 1},
                         "startTimeLocal": "2026-11-03 20:09:31", "distance": 1.0, "duration": 2.0, "calories": 3}]
    doc = _doc(runner.invoke(app, ["activities", U]))
    assert doc["data"] == [{"activityId": 1, "activityName": "r", "activityType": {"typeKey": "running"},
                            "startTimeLocal": "2026-11-03 20:09:31", "distance": 1.0, "duration": 2.0}]
    assert fake_api["calls"][0][:2] == ("get_gear_activities", (U,))
