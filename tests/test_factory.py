"""Factory-built commands: flags, fan-out, shaping, envelope."""

import json
from datetime import date, timedelta

import pytest
import typer
from typer.testing import CliRunner

from pace.api import ApiError
from pace.cache import Cache
from pace.dates import sample_days
from pace.factory import build_command, build_group
from pace.shape import SeriesSpec
from pace.spec import GROUPS, EndpointSpec, specs_for

runner = CliRunner()
MS = 1000


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")
    monkeypatch.setattr("pace.config.CONFIG_FILE", tmp_path / "config.toml")


@pytest.fixture
def _clear_config_cache():
    """load_config() is a bare functools.cache -- clear it around tests that write
    config.toml so overrides don't leak into (or out of) other tests."""
    from pace.config import load_config

    load_config.cache_clear()
    yield
    load_config.cache_clear()


@pytest.fixture
def fake_api(monkeypatch):
    """Replace safe_call; `calls` records (method, args); `fail_on` maps a date string to an ApiError."""
    state = {"calls": [], "fail_on": {}, "payload": lambda method, args: {"m": method, "a": list(args)}}

    async def safe_call(method, *args, **kwargs):
        state["calls"].append((method, args))
        for a in args:
            if a in state["fail_on"]:
                return False, None, state["fail_on"][a]
        return True, state["payload"](method, args), None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    return state


def _app(spec: EndpointSpec) -> typer.Typer:
    app = typer.Typer()
    build_command(app, spec)
    return app


def _invoke(app, *argv):
    res = runner.invoke(app, list(argv))
    return res.exit_code, json.loads(res.stdout), res.stderr


DATED = EndpointSpec("wellness", "hr", "h", "get_heart_rates", 300, dated="single",
                     series=(SeriesSpec("heartRateValues", "pairs"),))


def test_dated_range_ascending_and_args_echo(fake_api) -> None:
    code, doc, err = _invoke(_app(DATED), "--to", "2026-01-03", "--days", "3")
    assert code == 0 and err == ""
    assert doc["ok"] is True and doc["command"] == "wellness hr"
    assert [d["date"] for d in doc["data"]] == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert doc["args"] == {"from": "2026-01-01", "to": "2026-01-03", "every": 1, "fields": "default", "bucket": "auto", "raw": False}
    assert doc["cache"] == {"hit": False, "oldest_age_s": 0}
    assert [a for _, a in fake_api["calls"]] == [("2026-01-01",), ("2026-01-02",), ("2026-01-03",)]


def test_single_day_is_list_of_one_and_second_call_hits_cache(fake_api) -> None:
    app = _app(DATED)
    _, doc, _ = _invoke(app, "--to", "2026-01-03")
    assert len(doc["data"]) == 1 and doc["data"][0]["date"] == "2026-01-03"
    _, doc2, _ = _invoke(app, "--to", "2026-01-03")
    assert doc2["cache"]["hit"] is True
    assert len(fake_api["calls"]) == 1


def test_partial_failure_keeps_ok(fake_api) -> None:
    fake_api["fail_on"]["2026-01-02"] = ApiError("not_found", "gone", 404)
    code, doc, _ = _invoke(_app(DATED), "--to", "2026-01-03", "--days", "3")
    assert code == 0 and doc["ok"] is True
    assert doc["data"][1] == {"date": "2026-01-02", "error": {"code": "not_found", "message": "gone"}}
    assert "m" in doc["data"][0]


def test_all_fail_is_error_envelope(fake_api) -> None:
    fake_api["fail_on"]["2026-01-03"] = ApiError("auth_required", "nope", 401)
    code, doc, _ = _invoke(_app(DATED), "--to", "2026-01-03")
    assert code == 3 and doc["ok"] is False and doc["error"]["code"] == "auth_required"


def test_series_bucketed_and_reported(fake_api) -> None:
    fake_api["payload"] = lambda m, a: {"heartRateValues": [[i * 60 * MS, 60] for i in range(1440)]}
    _, doc, _ = _invoke(_app(DATED), "--to", "2026-01-03")
    hr = doc["data"][0]["heartRateValues"]
    assert hr["cols"][0] == "t" and len(hr["rows"]) == 96
    assert doc["shape"] == {"bucket_used": "15m", "reduced_series": ["heartRateValues"], "fields": "default"}


def test_auto_bucket_is_scored_against_the_whole_range(fake_api) -> None:
    """3 days x 1440 samples: `auto` must pick one grid for the range (1h), not per day (15m)."""
    fake_api["payload"] = lambda m, a: {"heartRateValues": [[i * 60 * MS, 60] for i in range(1440)]}
    _, doc, _ = _invoke(_app(DATED), "--to", "2026-01-03", "--days", "3")
    assert doc["shape"]["bucket_used"] == "1h"
    assert all(len(d["heartRateValues"]["rows"]) == 24 for d in doc["data"])


def test_upn_resolution_uses_the_device_ttl(fake_api, monkeypatch) -> None:
    """resolve_upn shares `device last-used`'s cache key, so it must share its TTL."""
    ttl_by_key: dict[str, int] = {}
    original = Cache.get_or_fetch

    async def spy(self, key, fetch, ttl, refresh=False):
        ttl_by_key[key] = ttl
        return await original(self, key, fetch, ttl, refresh=refresh)

    monkeypatch.setattr(Cache, "get_or_fetch", spy)
    spec = EndpointSpec("gear", "list", "h", "get_gear", 86400, resolver="upn")
    fake_api["payload"] = lambda m, a: {"userProfileNumber": 42} if m == "get_device_last_used" else [{"x": 1}]
    code, _, _ = _invoke(_app(spec))
    assert code == 0
    assert ttl_by_key["get_device_last_used"] == 86400


def test_range_method_gets_start_and_end(fake_api) -> None:
    spec = EndpointSpec("wellness", "body-battery", "h", "get_body_battery", 300, dated="range", unwrap="first")
    fake_api["payload"] = lambda m, a: [{"charged": 40}]
    _, doc, _ = _invoke(_app(spec), "--to", "2026-01-03")
    assert fake_api["calls"] == [("get_body_battery", ("2026-01-03", "2026-01-03"))]
    assert doc["data"] == [{"date": "2026-01-03", "charged": 40}]


def test_id_arg_and_upn_resolver(fake_api) -> None:
    spec = EndpointSpec("gear", "stats", "h", "get_gear_stats", 300, id_arg="GEAR_UUID", id_type=str, resolver="upn")
    fake_api["payload"] = lambda m, a: {"userProfileNumber": 42} if m == "get_device_last_used" else {"km": 1}
    code, doc, _ = _invoke(_app(spec), "abc-uuid")
    assert code == 0 and doc["data"] == {"km": 1}
    assert fake_api["calls"][-1] == ("get_gear_stats", ("42", "abc-uuid"))


def test_no_cache_bypasses_read_but_writes(fake_api) -> None:
    app = _app(DATED)
    _, doc1, _ = _invoke(app, "--to", "2026-01-03", "--no-cache")
    assert len(fake_api["calls"]) == 1
    assert doc1["cache"]["hit"] is False
    _, doc2, _ = _invoke(app, "--to", "2026-01-03")
    assert doc2["cache"]["hit"] is True
    assert len(fake_api["calls"]) == 1


def test_bad_bucket_and_days_are_bad_args(fake_api) -> None:
    code, doc, _ = _invoke(_app(DATED), "--bucket", "7m")
    assert code == 2 and doc["error"]["code"] == "bad_args"
    code, doc, _ = _invoke(_app(DATED), "--days", "0")
    assert code == 2 and doc["error"]["code"] == "bad_args"
    code, doc, _ = _invoke(_app(DATED), "--days", "400")
    assert code == 2


def test_raw_flag(fake_api) -> None:
    fake_api["payload"] = lambda m, a: {"x": None, "y": 1}
    _, doc, _ = _invoke(_app(DATED), "--to", "2026-01-03", "--raw")
    assert doc["data"][0] == {"date": "2026-01-03", "x": None, "y": 1} and doc["shape"] == {"raw": True}


def test_verbose_logs_to_stderr(fake_api) -> None:
    from pace import envelope
    envelope.set_verbose(True)
    try:
        res = runner.invoke(_app(DATED), ["--to", "2026-01-03"])
    finally:
        envelope.set_verbose(False)
    assert "miss get_heart_rates:2026-01-03" in res.stderr


def test_payload_date_field_does_not_override_requested_date(fake_api) -> None:
    fake_api["payload"] = lambda m, a: {"date": "1999-01-01", "x": 1}
    _, doc, _ = _invoke(_app(DATED), "--to", "2026-01-03")
    assert doc["data"][0]["date"] == "2026-01-03"
    assert doc["data"][0]["x"] == 1


def test_ttl_by_day_age_and_config_override(fake_api, tmp_path, monkeypatch, _clear_config_cache) -> None:
    from pace import factory
    from pace.config import load_config

    original_ttl_for_day = factory.ttl_for_day
    ttl_calls: list[tuple[date, int, int]] = []

    def spy_ttl_for_day(day, today_ttl):
        result = original_ttl_for_day(day, today_ttl)
        ttl_calls.append((day, today_ttl, result))
        return result

    original_get_or_fetch = Cache.get_or_fetch
    ttl_by_key: dict[str, int] = {}

    async def spy_get_or_fetch(self, key, fetch, ttl, refresh=False):
        ttl_by_key[key] = ttl
        return await original_get_or_fetch(self, key, fetch, ttl, refresh=refresh)

    monkeypatch.setattr(factory, "ttl_for_day", spy_ttl_for_day)
    monkeypatch.setattr(Cache, "get_or_fetch", spy_get_or_fetch)
    (tmp_path / "config.toml").write_text('[cache_ttl_overrides]\n"wellness.hr" = 42\n')
    load_config.cache_clear()

    # Captured before the run: a midnight rollover between here and the assertions would
    # otherwise compare the fan-out's days against a different "today".
    today = date.today()
    yesterday = today - timedelta(days=1)
    two_days_ago = today - timedelta(days=2)

    code, doc, _ = _invoke(_app(DATED), "--days", "3")

    assert code == 0 and doc["ok"] is True

    by_day = {d: (arg_ttl, ret_ttl) for d, arg_ttl, ret_ttl in ttl_calls}
    assert set(by_day) == {today, yesterday, two_days_ago}
    assert all(arg_ttl == 42 for arg_ttl, _ in by_day.values()), "config override must reach ttl_for_day"

    assert ttl_by_key[f"get_heart_rates:{today.isoformat()}"] == 42
    assert ttl_by_key[f"get_heart_rates:{yesterday.isoformat()}"] == 86400
    assert ttl_by_key[f"get_heart_rates:{two_days_ago.isoformat()}"] == 604800


@pytest.mark.parametrize("group", sorted(GROUPS))
def test_every_registry_group_builds_and_renders_help(group) -> None:
    app = build_group(group)
    specs = specs_for(group)
    assert specs, f"group {group!r} has no specs"
    for spec in specs:
        res = runner.invoke(app, [spec.name, "--help"])
        assert res.exit_code == 0, res.stdout
        assert "--fields" in res.stdout
        if spec.dated is not None:
            assert "--from" in res.stdout
        if spec.id_arg is not None:
            assert spec.id_arg in res.stdout


def test_every_calls_only_grid_days_and_the_last(fake_api) -> None:
    code, doc, _ = _invoke(_app(DATED), "--to", "2026-01-15", "--days", "15", "--every", "7")
    called = [a[0] for _, a in fake_api["calls"]]
    assert code == 0 and called == [d.isoformat() for d in sample_days(date(2026, 1, 1), date(2026, 1, 15), 7)]
    assert doc["args"]["every"] == 7


def test_every_zero_is_bad_args(fake_api) -> None:
    code, doc, _ = _invoke(_app(DATED), "--every", "0")
    assert code == 2 and doc["error"]["code"] == "bad_args" and fake_api["calls"] == []


SPAN = EndpointSpec("training", "race-predictions", "h", "get_race_predictions", 3600, dated="span",
                    span_args=("daily",))


def test_span_without_range_calls_with_no_dates(fake_api) -> None:
    code, doc, _ = _invoke(_app(SPAN))
    assert code == 0 and fake_api["calls"] == [("get_race_predictions", ())]


def test_span_range_is_one_call(fake_api) -> None:
    fake_api["payload"] = lambda m, a: [{"calendarDate": "2026-01-01"}, {"calendarDate": "2026-01-02"}]
    code, doc, _ = _invoke(_app(SPAN), "--to", "2026-01-02", "--days", "2")
    assert code == 0 and fake_api["calls"] == [("get_race_predictions", ("2026-01-01", "2026-01-02", "daily"))]
    assert [r["calendarDate"] for r in doc["data"]] == ["2026-01-01", "2026-01-02"]


def test_span_range_too_long_is_bad_args(fake_api) -> None:
    code, doc, _ = _invoke(_app(SPAN), "--days", "400")
    assert code == 2 and fake_api["calls"] == []
