import asyncio
import json

import pytest
from typer.testing import CliRunner

from pace.api import ApiError
from pace.cache import Cache
from pace.commands.workout import app
from pace.ratelimit import RateLimiter

runner = CliRunner()


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")


@pytest.fixture
def fake_api(monkeypatch):
    state = {"calls": [], "resp": {"workoutId": 77, "workoutScheduleId": 9}, "err": None}

    async def safe_call(method_name, *args, **kwargs):
        state["calls"].append((method_name, args, kwargs))
        if state["err"] is not None:
            return False, None, state["err"]
        return True, state["resp"], None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    monkeypatch.setattr("pace.writes.safe_call", safe_call)
    return state


DSL = {"name": "Easy", "sport": "running", "steps": [{"type": "warmup", "duration": "10m"}]}


def _doc(res):
    assert res.stdout.count("\n") == 1, res.stdout
    return json.loads(res.stdout)


def test_list_default_fields_and_truncation(fake_api) -> None:
    fake_api["resp"] = [{"workoutId": i, "workoutName": f"w{i}", "sportType": {"sportTypeKey": "running", "sportTypeId": 1},
                         "estimatedDurationInSecs": 0, "ownerId": 5, "updatedDate": "2026-09-01"} for i in range(2)]
    res = runner.invoke(app, ["list", "--limit", "2"])
    doc = _doc(res)
    assert res.exit_code == 0 and doc["command"] == "workout list" and doc["truncated"] is True
    assert doc["data"][0] == {"workoutId": 0, "workoutName": "w0", "sportType": {"sportTypeKey": "running"},
                              "estimatedDurationInSecs": 0, "updatedDate": "2026-09-01"}
    assert fake_api["calls"][0] == ("get_workouts", (0, 2), {})


def test_show_passes_id(fake_api) -> None:
    fake_api["resp"] = {"workoutId": 5, "x": None}
    doc = _doc(runner.invoke(app, ["show", "5"]))
    assert doc["data"] == {"workoutId": 5} and fake_api["calls"][0][0] == "get_workout_by_id"


def test_push_dry_run_from_file(fake_api, tmp_path) -> None:
    f = tmp_path / "w.json"
    f.write_text(json.dumps(DSL))
    doc = _doc(runner.invoke(app, ["push", str(f), "--dry-run"]))
    assert doc["ok"] and doc["data"]["dry_run"] is True
    assert doc["data"]["payload"]["workoutName"] == "Easy"
    assert doc["args"] == {"file": str(f), "dry_run": True}
    assert fake_api["calls"] == []


def test_push_from_stdin_uploads_and_returns_id(fake_api) -> None:
    res = runner.invoke(app, ["push", "-"], input=json.dumps(DSL))
    doc = _doc(res)
    assert res.exit_code == 0
    assert doc["data"] == {"action": "create", "resource": "workout", "id": 77, "echo": DSL}
    method, args, kw = fake_api["calls"][0]
    assert method == "upload_workout" and args[0]["workoutName"] == "Easy" and kw == {}


def test_push_invalid_dsl_is_bad_args_with_path(fake_api) -> None:
    res = runner.invoke(app, ["push", "-"], input=json.dumps({"name": "x", "sport": "running", "steps": [{"type": "warmup"}]}))
    doc = _doc(res)
    assert res.exit_code == 2 and doc["error"]["code"] == "bad_args" and doc["error"]["message"].startswith("steps[0]")
    res = runner.invoke(app, ["push", "-"], input="not json")
    assert res.exit_code == 2 and _doc(res)["error"]["code"] == "bad_args"


def test_schedule_posts_date(fake_api) -> None:
    doc = _doc(runner.invoke(app, ["schedule", "77", "2026-09-10"]))
    assert doc["data"] == {"action": "create", "resource": "workout", "id": 9, "echo": {"workout_id": 77, "date": "2026-09-10"}}
    assert fake_api["calls"][0] == ("connectapi", ("/workout-service/schedule/77",), {"method": "POST", "json": {"date": "2026-09-10"}})


def test_delete_uses_connectapi_delete(fake_api) -> None:
    fake_api["resp"] = None
    doc = _doc(runner.invoke(app, ["delete", "77"]))
    assert doc["data"] == {"action": "delete", "resource": "workout", "id": None, "echo": {"workout_id": 77}}
    assert fake_api["calls"][0] == ("connectapi", ("/workout-service/workout/77",), {"method": "DELETE"})


def test_write_error_maps_code(fake_api) -> None:
    fake_api["err"] = ApiError("auth_required", "nope", 401)
    res = runner.invoke(app, ["delete", "77"])
    assert res.exit_code == 3 and _doc(res)["error"]["code"] == "auth_required"


def test_library_validation_error_exits_2(monkeypatch) -> None:
    """`--limit 0` fails garminconnect's own `_validate_positive_integer` with a
    ValueError; `safe_call` classifies that as bad_args, so the CLI exits 2, not 1."""
    class FakeApi:
        def get_workouts(self, start, limit):
            raise ValueError("limit must be a positive integer")

    monkeypatch.setattr("pace.api.get_api", lambda: FakeApi())
    monkeypatch.setattr("pace.api.limiter", RateLimiter(min_interval=0.0))
    res = runner.invoke(app, ["list", "--limit", "0"])
    doc = _doc(res)
    assert res.exit_code == 2 and doc["error"]["code"] == "bad_args"
    assert "limit" in doc["error"]["message"]


def test_schedule_and_delete_dry_run_make_no_call(fake_api) -> None:
    doc = _doc(runner.invoke(app, ["schedule", "77", "2026-09-10", "--dry-run"]))
    assert doc["data"] == {"dry_run": True,
                           "payload": {"path": "/workout-service/schedule/77", "json": {"date": "2026-09-10"}}}
    doc = _doc(runner.invoke(app, ["delete", "77", "--dry-run"]))
    assert doc["data"] == {"dry_run": True, "payload": {"path": "/workout-service/workout/77"}}
    assert fake_api["calls"] == []


def test_push_ignores_a_warm_cache_and_then_drops_it(fake_api) -> None:
    """A write must always reach Garmin: a warm `workout list` entry may neither
    short-circuit the push nor survive it (a following list would be stale)."""
    async def stale():
        return [{"workoutId": 1}]

    cache = Cache()
    asyncio.run(cache.get_or_fetch("get_workouts:0:50", stale, ttl=600))
    assert cache.stats()["entries"] == 1
    cache.close()

    doc = _doc(runner.invoke(app, ["push", "-"], input=json.dumps(DSL)))
    assert doc["data"]["id"] == 77
    assert [c[0] for c in fake_api["calls"]] == ["upload_workout"]
    cache = Cache()
    assert cache.stats()["entries"] == 0
    cache.close()
