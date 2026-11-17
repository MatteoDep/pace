import json

import pytest
from typer.testing import CliRunner

from pace.api import ApiError
from pace.commands.activity import DETAIL_METRICS, app, details_table

runner = CliRunner()
MS = 1000


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    # PACE_HOME too: Cache() calls ensure_home(), which would otherwise mkdir the
    # developer's real ~/.pace during a test run.
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")


@pytest.fixture
def fake_api(monkeypatch):
    state = {"calls": [], "payload": lambda m, a, kw: {"m": m}}

    async def safe_call(method_name, *args, **kwargs):
        state["calls"].append((method_name, args, kwargs))
        p = state["payload"](method_name, args, kwargs)
        if isinstance(p, ApiError):
            return False, None, p
        return True, p, None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    monkeypatch.setattr("pace.commands.activity.safe_call", safe_call)
    monkeypatch.setattr("pace.api.safe_call", safe_call)
    monkeypatch.setattr("pace.writes.safe_call", safe_call)
    return state


def _acts(n):
    return [{"activityId": i, "activityName": f"run {i}", "activityType": {"typeKey": "running"},
             "distance": 5000.0, "duration": 1500.0, "averageHR": 150, "junk": None} for i in range(n)]


def test_list_default_is_recent_paginated(fake_api) -> None:
    fake_api["payload"] = lambda m, a, kw: _acts(20)
    res = runner.invoke(app, ["list"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 0 and doc["command"] == "activity list"
    assert fake_api["calls"][0][0] == "get_activities"
    assert doc["data"][0]["activityType"] == "running" and "junk" not in doc["data"][0]
    assert doc["truncated"] is False


def test_list_range_uses_by_date_and_truncates(fake_api) -> None:
    fake_api["payload"] = lambda m, a, kw: _acts(30)
    res = runner.invoke(app, ["list", "--to", "2026-01-31", "--days", "31", "--limit", "5", "--type", "running"])
    doc = json.loads(res.stdout)
    m, a, kw = fake_api["calls"][0]
    assert m == "get_activities_by_date" and a[:2] == ("2026-01-01", "2026-01-31") and "running" in a
    assert len(doc["data"]) == 5 and doc["truncated"] is True
    assert doc["args"]["from"] == "2026-01-01"


def test_list_default_api_error_keeps_code(fake_api) -> None:
    fake_api["payload"] = lambda m, a, kw: ApiError("auth_required", "no tokens", 401)
    res = runner.invoke(app, ["list"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 3
    assert doc["error"]["code"] == "auth_required" and doc["error"]["http"] == 401


def test_show_and_splits_pass_id(fake_api) -> None:
    fake_api["payload"] = lambda m, a, kw: {"activityId": a[0], "x": None}
    doc = json.loads(runner.invoke(app, ["show", "123"]).stdout)
    assert doc["data"] == {"activityId": 123}
    doc = json.loads(runner.invoke(app, ["splits", "123"]).stdout)
    assert fake_api["calls"][-1][0] == "get_activity_splits"


def _details_payload(n: int = 600) -> dict:
    keys = ["directTimestamp", "directHeartRate", "directSpeed", "sumDistance", "directElevation"]
    return {
        "activityId": 1,
        "metricDescriptors": [{"metricsIndex": i, "key": k} for i, k in enumerate(keys)],
        "activityDetailMetrics": [{"metrics": [i * MS, 140 + (i % 5), 3.0, float(i * 3), 100.0]} for i in range(n)],
    }


def test_details_table_buckets_all_metrics() -> None:
    table, used = details_table(_details_payload(600), "auto")   # 10 minutes of 1 Hz samples
    assert used == "1m" and len(table["rows"]) == 10
    assert table["cols"] == ["t", "hr", "speed_mps", "dist_m", "elev_m"]
    assert table["rows"][0][3] == 177.0   # dist_m is the last sample in the bucket (59 * 3)
    assert table["rows"][0][1] == 142.0   # hr avg of 140..144 pattern


def test_details_table_rounds_last_value_columns() -> None:
    """dist_m is carried through as the bucket's last sample; Garmin's float noise
    (13 decimals) must be rounded like every averaged column."""
    keys = ["directTimestamp", "sumDistance"]
    payload = {
        "metricDescriptors": [{"metricsIndex": i, "key": k} for i, k in enumerate(keys)],
        "activityDetailMetrics": [{"metrics": [i * MS, i * 3.3333333333333]} for i in range(200)],
    }
    table, used = details_table(payload, "1m")
    assert table["cols"] == ["t", "dist_m"]
    assert table["rows"][0][1] == 196.7


def test_details_table_handles_duplicate_timestamps_with_none_metric() -> None:
    keys = ["directTimestamp", "directHeartRate", "directSpeed", "sumDistance", "directElevation"]
    payload = {
        "activityId": 1,
        "metricDescriptors": [{"metricsIndex": i, "key": k} for i, k in enumerate(keys)],
        "activityDetailMetrics": [
            {"metrics": [0, 140, 3.0, 0.0, 100.0]},
            {"metrics": [0, None, 3.0, 1.0, 100.0]},
        ],
    }
    table, used = details_table(payload, "none")
    assert used == "none" and len(table["rows"]) == 2


def test_details_missing_timestamp_index_is_not_found(fake_api) -> None:
    fake_api["payload"] = lambda m, a, kw: {
        "activityId": 1,
        "metricDescriptors": [{"key": "directTimestamp"}, {"metricsIndex": 1, "key": "directHeartRate"}],
        "activityDetailMetrics": [{"metrics": [0, 140]}],
    }
    res = runner.invoke(app, ["details", "1"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 1 and doc["error"]["code"] == "not_found"


def test_details_command(fake_api) -> None:
    fake_api["payload"] = lambda m, a, kw: _details_payload(600)
    doc = json.loads(runner.invoke(app, ["details", "1"]).stdout)
    assert doc["ok"] and doc["data"]["table"]["cols"][0] == "t"
    assert doc["shape"]["bucket_used"] == "1m" and doc["shape"]["reduced_series"] == ["table"]
    assert fake_api["calls"][0][0] == "get_activity_details"


def test_details_bucket_none_keeps_every_sample(fake_api) -> None:
    fake_api["payload"] = lambda m, a, kw: _details_payload(600)
    doc = json.loads(runner.invoke(app, ["details", "1", "--bucket", "none"]).stdout)
    assert len(doc["data"]["table"]["rows"]) == 600


def test_export_writes_file_and_reports(fake_api, tmp_path) -> None:
    fake_api["payload"] = lambda m, a, kw: b"<gpx/>"
    out = tmp_path / "a.gpx"
    doc = json.loads(runner.invoke(app, ["export", "5", "--out", str(out)]).stdout)
    assert doc["ok"] and doc["data"]["files"] == [{"activityId": 5, "path": str(out), "bytes": 6}]
    assert out.read_bytes() == b"<gpx/>"


def test_export_range(fake_api, tmp_path) -> None:
    fake_api["payload"] = lambda m, a, kw: (
        [{"activityId": 1}, {"activityId": 2}] if m == "get_activities_by_date" else b"x")
    res = runner.invoke(app, ["export-range", "--from", "2026-01-01", "--to", "2026-01-02", "--out-dir", str(tmp_path)])
    doc = json.loads(res.stdout)
    assert res.exit_code == 0 and len(doc["data"]["files"]) == 2
    assert (tmp_path / "1.gpx").exists()


def test_api_error_becomes_envelope(fake_api) -> None:
    fake_api["payload"] = lambda m, a, kw: ApiError("not_found", "no", 404)
    res = runner.invoke(app, ["show", "9"])
    assert res.exit_code == 1 and json.loads(res.stdout)["error"]["code"] == "not_found"


def test_detail_metrics_map_is_stable() -> None:
    assert DETAIL_METRICS["directHeartRate"] == "hr"


def _get_activity_calls(fake_api) -> int:
    return [c[0] for c in fake_api["calls"]].count("get_activity")


def test_show_adds_effort_keys(fake_api) -> None:
    fake_api["payload"] = lambda m, a, kw: {"activityId": 5, "summaryDTO": {"directWorkoutRpe": 40, "directWorkoutFeel": 75}}
    doc = json.loads(runner.invoke(app, ["show", "5"]).stdout)
    assert doc["data"]["rpe"] == 4 and doc["data"]["feel"] == "strong"
    assert doc["data"]["summaryDTO"]["directWorkoutRpe"] == 40
    sel = json.loads(runner.invoke(app, ["show", "5", "--fields", "rpe,feel", "--no-cache"]).stdout)
    assert sel["data"] == {"rpe": 4, "feel": "strong"}
    raw = json.loads(runner.invoke(app, ["show", "5", "--raw", "--no-cache"]).stdout)
    assert "rpe" not in raw["data"]


def test_show_without_summary_omits_effort(fake_api) -> None:
    fake_api["payload"] = lambda m, a, kw: {"activityId": 6}
    doc = json.loads(runner.invoke(app, ["show", "6"]).stdout)
    assert doc["ok"] and "rpe" not in doc["data"] and "feel" not in doc["data"]


def test_activity_gear_curated(fake_api) -> None:
    fake_api["payload"] = lambda m, a, kw: [{"uuid": "a" * 32, "customMakeModel": "Altra", "gearTypeName": "Shoes",
                                             "gearStatusName": "active", "gearPk": 1, "imageNameLarge": None}]
    doc = json.loads(runner.invoke(app, ["gear", "7"]).stdout)
    assert doc["command"] == "activity gear"
    assert doc["data"] == [{"uuid": "a" * 32, "customMakeModel": "Altra", "gearTypeName": "Shoes",
                            "gearStatusName": "active"}]
    assert fake_api["calls"][0][:2] == ("get_activity_gear", (7,))


def test_effort_dry_run_sends_only_given_keys(fake_api) -> None:
    doc = json.loads(runner.invoke(app, ["effort", "9", "--rpe", "6", "--dry-run"]).stdout)
    assert doc["args"] == {"activity_id": 9, "rpe": 6, "feel": None, "dry_run": True}
    assert doc["data"] == {"dry_run": True, "payload": {"path": "/activity-service/activity/9",
                           "json": {"activityId": 9, "summaryDTO": {"directWorkoutRpe": 60}}}}
    assert fake_api["calls"] == []


def test_effort_real_run_result_and_invalidation(fake_api) -> None:
    fake_api["payload"] = lambda m, a, kw: {"activityId": 9}
    runner.invoke(app, ["show", "9"])
    runner.invoke(app, ["show", "9"])
    assert _get_activity_calls(fake_api) == 1  # second show was a cache hit
    fake_api["payload"] = lambda m, a, kw: None
    doc = json.loads(runner.invoke(app, ["effort", "9", "--feel", "Very_Strong"]).stdout)
    assert doc["data"] == {"action": "update", "resource": "activity_effort", "id": None,
                           "echo": {"activity_id": 9, "feel": "very-strong"}}
    method, args, kw = fake_api["calls"][-1]
    assert method == "connectapi" and args == ("/activity-service/activity/9",)
    assert kw == {"method": "PUT", "json": {"activityId": 9, "summaryDTO": {"directWorkoutFeel": 100}}}
    fake_api["payload"] = lambda m, a, kw: {"activityId": 9}
    runner.invoke(app, ["show", "9"])
    assert _get_activity_calls(fake_api) == 2  # cached row was invalidated


def test_effort_invalidates_the_zones_activity_key(fake_api) -> None:
    import asyncio

    from pace.cache import Cache
    from pace.commands._helpers import cached_call

    async def read():
        cache = Cache()
        try:
            await cached_call("get_activity", 9, cache=cache, ttl=3600, key_prefix="zones")
        finally:
            cache.close()

    fake_api["payload"] = lambda m, a, kw: {"activityId": 9}
    asyncio.run(read())
    asyncio.run(read())
    assert _get_activity_calls(fake_api) == 1
    fake_api["payload"] = lambda m, a, kw: None
    runner.invoke(app, ["effort", "9", "--rpe", "6"])
    fake_api["payload"] = lambda m, a, kw: {"activityId": 9}
    asyncio.run(read())
    assert _get_activity_calls(fake_api) == 2  # the zones key was invalidated too


def test_effort_both_flags(fake_api) -> None:
    doc = json.loads(runner.invoke(app, ["effort", "9", "--rpe", "10", "--feel", "weak", "--dry-run"]).stdout)
    assert doc["data"]["payload"]["json"]["summaryDTO"] == {"directWorkoutRpe": 100, "directWorkoutFeel": 25}


@pytest.mark.parametrize("argv", [["effort", "9"], ["effort", "9", "--rpe", "0"], ["effort", "9", "--rpe", "11"],
                                  ["effort", "9", "--feel", "tired"], ["effort", "0", "--rpe", "5"]])
def test_effort_bad_args(fake_api, argv) -> None:
    res = runner.invoke(app, argv)
    assert res.exit_code == 2 and json.loads(res.stdout)["error"]["code"] == "bad_args"
    assert fake_api["calls"] == []
