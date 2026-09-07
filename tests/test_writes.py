"""Write runner: dry-run, call, invalidate, result."""

import sqlite3

import pytest

from pace.api import ApiError
from pace.cache import Cache
from pace.envelope import PaceError
from pace.writes import WriteSpec, run_write


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")


@pytest.fixture
def fake_api(monkeypatch):
    state = {"calls": [], "resp": {"workoutId": 77}, "err": None}

    async def safe_call(method_name, *args, **kwargs):
        state["calls"].append((method_name, args, kwargs))
        if state["err"] is not None:
            return False, None, state["err"]
        return True, state["resp"], None

    monkeypatch.setattr("pace.writes.safe_call", safe_call)
    return state


def _spec(**over):
    base = dict(resource="workout", action="create", method="upload_workout", args=({"workoutName": "x"},),
                payload={"workoutName": "x"}, echo={"name": "x"}, invalidate=("get_workouts:",),
                id_of=lambda r: r.get("workoutId"))
    base.update(over)
    return WriteSpec(**base)


@pytest.mark.asyncio
async def test_dry_run_makes_no_call(fake_api) -> None:
    res = await run_write(_spec(), dry_run=True)
    assert res.data == {"dry_run": True, "payload": {"workoutName": "x"}}
    assert res.cache is None and res.shape is None
    assert fake_api["calls"] == []


@pytest.mark.asyncio
async def test_success_returns_id_and_echo_and_invalidates(fake_api) -> None:
    cache = Cache()
    await cache.get_or_fetch("get_workouts:0:100", lambda: _ret([1]), ttl=600)
    await cache.get_or_fetch("get_user_summary:2026-09-06", lambda: _ret({}), ttl=600)
    cache.close()

    res = await run_write(_spec(), dry_run=False)
    assert res.data == {"action": "create", "resource": "workout", "id": 77, "echo": {"name": "x"}}
    assert fake_api["calls"] == [("upload_workout", ({"workoutName": "x"},), {})]
    cache = Cache()
    assert cache.stats()["entries"] == 1          # only the unrelated key survives
    cache.close()


@pytest.mark.asyncio
async def test_api_error_becomes_pace_error(fake_api) -> None:
    fake_api["err"] = ApiError("rate_limited", "slow", 429)
    with pytest.raises(PaceError) as exc:
        await run_write(_spec(), dry_run=False)
    assert exc.value.code == "rate_limited" and exc.value.http == 429


@pytest.mark.asyncio
async def test_delete_without_id_extractor(fake_api) -> None:
    fake_api["resp"] = None
    res = await run_write(_spec(action="delete", method="connectapi", args=("/workout-service/workout/5",),
                                kwargs={"method": "DELETE"}, payload=None, echo={"workout_id": 5}, id_of=None),
                          dry_run=False)
    assert res.data == {"action": "delete", "resource": "workout", "id": None, "echo": {"workout_id": 5}}
    assert fake_api["calls"][0] == ("connectapi", ("/workout-service/workout/5",), {"method": "DELETE"})


async def _ret(v):
    return v


@pytest.mark.asyncio
async def test_cache_failure_does_not_fail_a_completed_write(fake_api, monkeypatch) -> None:
    """Invalidation runs after Garmin already accepted the write, so a cache error must
    not be reported back as a failed write."""
    def boom(self, prefix):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(Cache, "invalidate", boom)
    res = await run_write(_spec(), dry_run=False)
    assert res.data == {"action": "create", "resource": "workout", "id": 77, "echo": {"name": "x"}}
