import json

import pytest
from typer.testing import CliRunner

from pace.api import ApiError
from pace.commands.activity import DETAIL_METRICS, app, details_table

runner = CliRunner()
MS = 1000


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")


@pytest.fixture
def fake_api(monkeypatch):
    state = {"calls": [], "payload": lambda m, a, kw: {"m": m}}

    async def safe_call(method, *args, **kwargs):
        state["calls"].append((method, args, kwargs))
        p = state["payload"](method, args, kwargs)
        if isinstance(p, ApiError):
            return False, None, p
        return True, p, None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    monkeypatch.setattr("pace.commands.activity.safe_call", safe_call)
    monkeypatch.setattr("pace.api.safe_call", safe_call)
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
