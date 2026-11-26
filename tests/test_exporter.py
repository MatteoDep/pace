"""`pace export`: the recipe runner, its fallbacks and the command, against a fake Garmin client.

The fake sits behind the real safe_call, limiter, budget and cache, so every dataset runs the way
`pace <command> <argv>` would in a shell."""

import copy
import io
import json
import stat
import sys
from datetime import date, timedelta
from typing import Any

import click
import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
from typer.testing import CliRunner

import pace.auth
from pace import ratelimit
from pace.cache import Cache
from pace.cli import app
from pace.commands.schema import build_schema
from pace.envelope import PaceError
from pace.exporter import extract, invoke, run_recipe
from pace.ratelimit import RateLimiter
from pace.recipe import DEFAULT_RECIPE, Dataset, bundle_path, parse_recipe, recipe_path
from tests.conftest import REAL_GET_API

runner = CliRunner()
COMMANDS = {c["name"]: c for c in build_schema(None)["commands"]}

U1, U2, U3 = (f"0123456789abcdef0123456789abcd0{i}" for i in (1, 2, 3))
GEAR = [{"uuid": u, "customMakeModel": f"Shoe {i}", "gearTypeName": "Shoes", "gearStatusName": "active"}
        for i, u in enumerate((U1, U2, U3), 1)]
GEAR_ACTS = {U1: 12345678901, U2: 12345678902, U3: 12345678903}
NOT_FOUND = GarminConnectConnectionError("API client error (404): Not Found")
GENERATED = "2026-11-20T10:00:00+01:00"

SHOES = {"id": "shoes", "command": "gear list", "columns": {"uuid": "uuid", "name": "customMakeModel"}}
SHOE_ACTS = {"id": "shoe_acts", "command": "gear activities", "argv": ["{uuid}"],
             "foreach": {"dataset": "shoes", "limit": 2},
             "columns": {"activity_id": "activityId", "start": "startTimeLocal"}}
ACTS = {"id": "acts", "command": "activity list", "argv": ["--limit", "5"],
        "columns": {"id": "activityId", "name": "activityName"}}


class FakeGarmin:
    """Per-method payloads (a value, or a function of the call's args), per-call failures, a call log."""

    def __init__(self) -> None:
        self.payloads: dict[str, Any] = {
            "get_device_last_used": {"userProfileNumber": 12345},
            "get_gear": GEAR,
            "get_gear_activities": lambda uuid: [{"activityId": GEAR_ACTS[uuid],
                                                  "startTimeLocal": "2026-11-20 07:00:00"}],
            "get_fitnessage_data": lambda iso: {"fitnessAge": 40, "chronologicalAge": 45},
            "get_activities": lambda start, limit: [] if start else [
                {"activityId": 12345678901, "activityName": "Run", "activityType": {"typeKey": "running"},
                 "startTimeLocal": "2026-11-25 07:00:00", "distance": 10000.0, "duration": 3600.0}],
            "get_activities_by_date": [],
        }
        self.failures: dict[tuple[str, tuple | None], BaseException] = {}
        self.calls: list[tuple[str, tuple]] = []

    def fail(self, method: str, exc: BaseException, args: tuple | None = None) -> None:
        """Make `method` raise `exc`, for the given positional args only, or for every call."""
        self.failures[(method, args)] = exc

    def called(self, method: str) -> list[tuple]:
        return [a for m, a in self.calls if m == method]

    def __getattr__(self, name: str):
        if name.startswith("_") or name not in self.payloads:
            raise AttributeError(name)

        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args))
            exc = self.failures.get((name, args)) or self.failures.get((name, None))
            if exc is not None:
                raise exc
            payload = self.payloads[name]
            return copy.deepcopy(payload(*args) if callable(payload) else payload)

        return call


@pytest.fixture
def garmin(tmp_path, monkeypatch) -> FakeGarmin:
    client = FakeGarmin()
    monkeypatch.setattr("pace.api.get_api", lambda: client)
    monkeypatch.setattr("pace.api.limiter",
                        RateLimiter(min_interval=0.01, per_minute=1000, per_hour=1000, per_day=1000))
    monkeypatch.setattr("pace.auth.auth_status", lambda: {"authenticated": True})
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path / ".pace")
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / ".pace" / "cache.db")
    return client


def _parse(lines: list[dict]) -> list[Dataset]:
    return parse_recipe({"version": 1, "datasets": lines}, COMMANDS)


def _fa(id_: str, days: int = 3, to: str = "today") -> dict:
    return {"id": id_, "command": "training fitness-age", "argv": ["--days", str(days), "--to", to],
            "columns": {"date": "date", "age": "fitnessAge"}}


def _dataset(rows: str | None = None, columns: dict | None = None) -> Dataset:
    return Dataset("d", "gear list", (), rows, columns or {"x": "x"}, None)


def _days(n: int) -> list[str]:
    today = date.today()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def _section(columns: list[str], rows: list[dict]) -> dict:
    return {"rows": rows, "meta": {"columns": columns, "generated": GENERATED}}


def _prev(**sections: dict) -> dict:
    return {"meta": [], "datasets": sections}


def _run(lines: list[dict], previous: dict | None = None, max_calls: int = 60) -> tuple[dict, dict]:
    return run_recipe(_parse(lines), previous=previous, max_calls=max_calls, max_seconds=60)


def _write_recipe(path, lines: list[dict]):
    path.write_text(json.dumps({"version": 1, "datasets": lines}))
    return path


def _cli(*args: str) -> tuple[int, dict]:
    res = runner.invoke(app, ["export", *args])
    assert res.stdout.count("\n") == 1, res.stdout
    return res.exit_code, json.loads(res.stdout)


def test_two_fanout_datasets_in_one_process(garmin) -> None:
    # 7-day fan-outs contend for the semaphore and the limiter lock; the second dataset used to crash.
    # fa2 covers the 7 days before fa1's, so the cache cannot serve it and it fans out too.
    b, s = run_recipe(_parse([_fa("fa1", days=7), _fa("fa2", days=7, to="-7d")]),
                      previous=None, max_calls=60, max_seconds=60)
    assert len(b["datasets"]["fa1"]["rows"]) == 7 and len(b["datasets"]["fa2"]["rows"]) == 7
    assert s["calls"] == 14 and len(set(garmin.called("get_fitnessage_data"))) == 14
    assert not any(d["stale"] or d["day_errors"] or d["error"] for d in s["datasets"].values())


def test_extract_paths() -> None:
    ds = _dataset(rows="a.list", columns={"x": "k.v", "y": "missing"})
    assert extract({"a": {"list": [{"k": {"v": 1}}, 5]}}, ds)[0] == [{"x": 1, "y": None}, {"x": None, "y": None}]
    assert extract({"a": {"list": None}}, ds) == ([], [])
    assert extract({"b": 1}, ds) == ([], [])                                             # pruned empty list
    assert extract({"k": {"v": 2}}, _dataset(columns={"x": "k.v"}))[0] == [{"x": 2}]     # object -> one row
    assert extract([{"date": "2026-01-02", "error": {"code": "network"}}], _dataset(columns={"d": "date"})) == ([], ["2026-01-02"])
    assert extract([3, "a"], _dataset(columns={"v": "value"})) == ([{"v": 3}, {"v": "a"}], [])
    assert extract(None, _dataset()) == ([], [])


def test_failed_dataset_keeps_previous_rows_stale(garmin) -> None:
    garmin.fail("get_gear", NOT_FOUND)
    old = [{"uuid": U1, "name": "Old shoe"}]
    b, s = _run([SHOES], previous=_prev(shoes=_section(["uuid", "name"], old)))
    sec = b["datasets"]["shoes"]
    assert sec["rows"] == old
    assert {k: sec["meta"][k] for k in ("stale", "generated", "reason", "error", "rows", "command")} == {
        "stale": True, "generated": GENERATED, "reason": "error", "error": {"code": "not_found"}, "rows": 1,
        "command": "gear list"}
    assert s["datasets"]["shoes"] == {"rows": 1, "calls": 2, "stale": True, "reason": "error",
                                      "error": {"code": "not_found", "message": "Endpoint not found (404)"},
                                      "day_errors": 0, "errors": []}


def test_previous_rows_not_reused_after_a_column_change(garmin) -> None:
    garmin.fail("get_gear", NOT_FOUND)
    b, _ = _run([SHOES], previous=_prev(shoes=_section(["uuid"], [{"uuid": U1}])))
    sec = b["datasets"]["shoes"]
    assert sec["rows"] == [] and sec["meta"]["stale"] is True and sec["meta"]["error"] == {"code": "not_found"}
    assert sec["meta"]["columns"] == ["uuid", "name"] and sec["meta"]["generated"] != GENERATED


def test_failed_days_merge_from_previous_bundle(garmin) -> None:
    days = _days(3)
    garmin.fail("get_fitnessage_data", NOT_FOUND, args=(days[1],))
    prev = _prev(fa=_section(["date", "age"], [{"date": d, "age": 50 + i} for i, d in enumerate(days)]))
    b, s = _run([_fa("fa")], previous=prev)
    sec = b["datasets"]["fa"]
    assert sec["rows"] == [{"date": days[0], "age": 40}, {"date": days[1], "age": 51}, {"date": days[2], "age": 40}]
    assert (sec["meta"]["day_errors"], sec["meta"]["stale"], sec["meta"]["reason"], sec["meta"]["error"]) == (
        1, False, None, None)
    assert sec["meta"]["calls"] == 3 and s["datasets"]["fa"]["day_errors"] == 1
    # without a previous row for that date the day is dropped, still counted
    b, _ = _run([_fa("fa")])
    assert b["datasets"]["fa"]["rows"] == [{"date": days[0], "age": 40}, {"date": days[2], "age": 40}]
    assert b["datasets"]["fa"]["meta"]["day_errors"] == 1


def test_deferred_days_merge_from_previous_bundle(garmin) -> None:
    days = _days(3)
    prev = _prev(fa=_section(["date", "age"], [{"date": d, "age": 50 + i} for i, d in enumerate(days)]))
    b, s = _run([_fa("fa")], previous=prev, max_calls=2)
    fetched = {a[0] for a in garmin.called("get_fitnessage_data")}
    assert len(fetched) == 2 and s["calls"] == 2
    sec = b["datasets"]["fa"]
    assert sec["rows"] == [{"date": d, "age": 40 if d in fetched else 50 + i} for i, d in enumerate(days)]
    assert (sec["meta"]["day_errors"], sec["meta"]["stale"], sec["meta"]["error"]) == (1, False, None)


def test_after_the_budget_ends_cached_datasets_still_refresh(garmin) -> None:
    _run([ACTS])  # warms the cache for ACTS
    garmin.calls.clear()
    ten = (date.today() - timedelta(days=10)).isoformat()
    b, s = _run([_fa("a0", days=1, to="-10d"), _fa("a", days=1, to="-11d"), ACTS], max_calls=1)
    assert garmin.calls == [("get_fitnessage_data", (ten,))]
    fresh = {"stale": False, "reason": None, "error": None, "day_errors": 0, "errors": []}
    assert s["datasets"]["a0"] == {"rows": 1, "calls": 1, **fresh}
    assert s["datasets"]["a"] == {"rows": 0, "calls": 0, "stale": True, "reason": "deferred",
                                  "error": {"code": "deferred", "message": "Not sent: call budget of 1 spent"},
                                  "day_errors": 0, "errors": []}
    assert s["datasets"]["acts"] == {"rows": 1, "calls": 0, **fresh}
    assert b["datasets"]["acts"]["rows"] == [{"id": 12345678901, "name": "Run"}]
    assert isinstance(b["datasets"]["acts"]["meta"]["cache_oldest_age_s"], int)
    assert b["datasets"]["a"]["meta"]["error"] == {"code": "deferred"}


def test_rate_limited_defers_the_rest(garmin) -> None:
    garmin.fail("get_activities", GarminConnectTooManyRequestsError("429"))
    b, s = _run([ACTS, _fa("fa", days=1)])
    assert garmin.calls == [("get_activities", (0, 6))]
    acts, fa = b["datasets"]["acts"]["meta"], b["datasets"]["fa"]["meta"]
    assert (acts["stale"], acts["reason"], acts["error"]) == (True, "error", {"code": "rate_limited"})
    assert (fa["stale"], fa["reason"], fa["error"], fa["calls"]) == (True, "deferred", {"code": "deferred"}, 0)
    assert b["datasets"]["fa"]["rows"] == []
    assert s["datasets"]["fa"]["error"] == {"code": "deferred", "message": "Not sent: stopped after rate_limited"}


def test_foreach_fill_limit_parent_columns_and_one_child_failing(garmin) -> None:
    garmin.fail("get_gear_activities", NOT_FOUND, args=(U2,))
    kept = {"activity_id": 12345678912, "start": "2026-11-01 07:00:00", "parent.uuid": U2}
    prev = _prev(shoe_acts=_section(["activity_id", "start"], [
        {"activity_id": 12345678911, "start": "2026-10-01 07:00:00", "parent.uuid": U1}, kept,
        {"activity_id": 12345678913, "start": "2026-10-02 07:00:00", "parent.uuid": U3}]))
    b, s = _run([SHOES, SHOE_ACTS], previous=prev)
    assert garmin.called("get_gear_activities") == [(U1,), (U2,)]
    sec = b["datasets"]["shoe_acts"]
    assert sec["rows"] == [{"activity_id": 12345678901, "start": "2026-11-20 07:00:00", "parent.uuid": U1}, kept]
    assert sec["meta"]["errors"] == [{"parent": {"uuid": U2}, "code": "not_found"}]
    assert (sec["meta"]["stale"], sec["meta"]["command"], sec["meta"]["calls"]) == (
        False, "gear activities {uuid}", 2)
    assert s["datasets"]["shoe_acts"]["errors"] == [
        {"parent": {"uuid": U2}, "code": "not_found", "message": "Endpoint not found (404)"}]


def test_foreach_refuses_flag_like_placeholder_values(garmin) -> None:
    garmin.payloads["get_gear"] = [{"uuid": "--no-cache", "customMakeModel": "A"},
                                   {"customMakeModel": "No uuid"}, GEAR[0]]
    b, s = _run([SHOES, {**SHOE_ACTS, "foreach": {"dataset": "shoes", "limit": 3}}])
    assert garmin.called("get_gear_activities") == [(U1,)]
    sec = b["datasets"]["shoe_acts"]
    assert sec["meta"]["errors"] == [{"parent": {"uuid": "--no-cache"}, "code": "bad_args"},
                                     {"parent": {"uuid": None}, "code": "bad_args"}]
    assert sec["rows"] == [{"activity_id": 12345678901, "start": "2026-11-20 07:00:00", "parent.uuid": U1}]
    # refused before click sees it (click would also refuse `gear activities --no-cache`, for another reason)
    assert [e["message"] for e in s["datasets"]["shoe_acts"]["errors"]] == [
        "A placeholder value is null or starts with '-'; not run."] * 2


@pytest.mark.parametrize("value, argv", [("--export", ["{x}", "OUT"]), ("--export=OUT", ["{x}"])])
def test_foreach_cannot_smuggle_a_writing_flag(garmin, tmp_path, value, argv) -> None:
    out = tmp_path / "out.json"
    value = value.replace("OUT", str(out))
    garmin.payloads["get_gear"] = [{"uuid": U1, "customMakeModel": value}]
    b, _ = _run([{"id": "names", "command": "gear list", "columns": {"x": "customMakeModel"}},
                 {"id": "loads", "command": "load", "argv": [a.replace("OUT", str(out)) for a in argv],
                  "foreach": {"dataset": "names", "limit": 1}, "columns": {"table": "table"}}])
    assert not out.exists() and garmin.called("get_activities_by_date") == []
    assert b["datasets"]["loads"]["meta"]["errors"] == [{"parent": {"x": value}, "code": "bad_args"}]


def test_foreach_runs_each_parent_value_once(garmin) -> None:
    # duplicate parent values (a foreach over a foreach section, `gear defaults`) must not multiply
    # the kept rows on every failing run
    garmin.payloads["get_gear"] = [GEAR[0], GEAR[0]]
    row = {"activity_id": 12345678901, "start": "2026-11-20 07:00:00", "parent.uuid": U1}
    b, _ = _run([SHOES, SHOE_ACTS])
    assert garmin.called("get_gear_activities") == [(U1,)] and b["datasets"]["shoe_acts"]["rows"] == [row]
    garmin.fail("get_gear_activities", NOT_FOUND)
    for _ in range(2):  # two failing runs in a row, each on the previous run's bundle
        c = Cache()
        c.clear()  # the child must reach Garmin (and fail), not the cache
        c.close()
        b, _ = _run([SHOES, SHOE_ACTS], previous=b)
        assert b["datasets"]["shoe_acts"]["rows"] == [row]
        assert b["datasets"]["shoe_acts"]["meta"]["errors"] == [{"parent": {"uuid": U1}, "code": "not_found"}]


def test_foreach_keeps_previous_rows_when_its_parent_has_none(garmin) -> None:
    garmin.fail("get_gear", NOT_FOUND)  # no previous shoes section: the parent is stale with no rows
    old = [{"activity_id": 12345678911, "start": "2026-10-01 07:00:00", "parent.uuid": U1}]
    b, s = _run([SHOES, SHOE_ACTS], previous=_prev(shoe_acts=_section(["activity_id", "start"], old)))
    sec = b["datasets"]["shoe_acts"]
    assert sec["rows"] == old and garmin.called("get_gear_activities") == []
    assert {k: sec["meta"][k] for k in ("stale", "reason", "error", "generated", "calls", "errors")} == {
        "stale": True, "reason": "error", "error": {"code": "not_found"}, "generated": GENERATED, "calls": 0,
        "errors": []}
    err = s["datasets"]["shoe_acts"]["error"]
    assert err["code"] == "not_found" and "shoes" in err["message"] and "Endpoint not found (404)" in err["message"]
    # without a usable previous section it is stale with no rows
    b, _ = _run([SHOES, SHOE_ACTS])
    assert b["datasets"]["shoe_acts"]["rows"] == [] and b["datasets"]["shoe_acts"]["meta"]["stale"] is True


def test_foreach_runs_on_a_stale_parents_previous_rows(garmin) -> None:
    garmin.fail("get_gear", NOT_FOUND)
    prev = _prev(shoes=_section(["uuid", "name"], [{"uuid": U1, "name": "Shoe 1"}, {"uuid": U3, "name": "Shoe 3"}]))
    b, _ = _run([SHOES, SHOE_ACTS], previous=prev)
    assert b["datasets"]["shoes"]["meta"]["stale"] is True
    assert garmin.called("get_gear_activities") == [(U1,), (U3,)]
    sec = b["datasets"]["shoe_acts"]
    assert sec["meta"]["stale"] is False and sec["meta"]["error"] is None
    assert [r["parent.uuid"] for r in sec["rows"]] == [U1, U3]


def test_invoke_wraps_usage_errors_and_silent_commands(garmin, monkeypatch) -> None:
    env = invoke("gear list", ["--bogus"])
    assert env["ok"] is False and env["error"]["code"] == "bad_args" and "--bogus" in env["error"]["message"]
    monkeypatch.setattr("pace.exporter.click_command", lambda name: click.Command("x", callback=lambda: None))
    env = invoke("gear list", [])
    assert env["ok"] is False and env["error"]["code"] == "internal"
    assert garmin.calls == []


def test_invoke_gives_the_command_an_empty_stdin(garmin, monkeypatch) -> None:
    # a credentials prompt reading stdin must hit EOF at once, never wait for (or eat) the caller's input
    monkeypatch.setattr("sys.stdin", io.StringIO("typed answer\n"))
    garmin.payloads["get_fitnessage_data"] = lambda iso: {"fitnessAge": sys.stdin.read() or "empty"}
    env = invoke("training fitness-age", [])
    assert env["ok"] is True and env["data"][0]["fitnessAge"] == "empty"
    assert sys.stdin.read() == "typed answer\n"


def test_auth_required_stops_and_writes_nothing(garmin, tmp_path, monkeypatch) -> None:
    recipe = _write_recipe(tmp_path / "r.json", [SHOES])
    bundle = bundle_path()
    bundle.parent.mkdir(parents=True)
    bundle.write_text('{"old": true}')
    monkeypatch.setattr("pace.auth.auth_status", lambda: {"authenticated": False})
    code, doc = _cli("--recipe", str(recipe))
    assert code == 3 and doc["error"]["code"] == "auth_required"
    assert bundle.read_text() == '{"old": true}' and garmin.calls == []


def test_auth_required_mid_run_stops_the_export(garmin, tmp_path) -> None:
    recipe = _write_recipe(tmp_path / "r.json", [SHOES, _fa("fa", days=1)])
    garmin.fail("get_gear", GarminConnectAuthenticationError("token expired"))
    code, doc = _cli("--recipe", str(recipe))
    assert code == 3 and doc["error"]["code"] == "auth_required"
    assert not bundle_path().exists() and garmin.called("get_fitnessage_data") == []
    with pytest.raises(PaceError) as e:
        _run([SHOES])
    assert e.value.code == "auth_required" and ratelimit.active_budget() is None


def test_rejected_tokens_end_the_export_without_prompting(garmin, tmp_path, monkeypatch) -> None:
    # tokens exist (auth_status says so) but Garmin rejects them: no prompt, one login, auth_required
    logins, prompts = [], []

    class RejectingGarmin:
        def login(self, tokenstore=None):
            logins.append(tokenstore)
            raise GarminConnectAuthenticationError("Authentication failed: 401 Unauthorized")

    def prompt(*args, **kwargs):
        prompts.append(args)
        raise EOFError

    monkeypatch.setattr("pace.api.get_api", REAL_GET_API)
    monkeypatch.setattr("pace.api._api", None)
    monkeypatch.setattr("pace.auth.Garmin", RejectingGarmin)
    for target in ("pace.auth.get_credentials", "pace.auth.getpass", "builtins.input"):
        monkeypatch.setattr(target, prompt)
    recipe = _write_recipe(tmp_path / "r.json", [_fa("fa", days=3), SHOES])  # a gathered fan-out first
    code, doc = _cli("--recipe", str(recipe))
    assert code == 3 and doc["error"]["code"] == "auth_required"
    assert not bundle_path().exists() and len(logins) == 1 and prompts == [] and garmin.calls == []
    # outside the export, a failed token login still falls through to the interactive prompt
    with pytest.raises(EOFError):
        pace.auth.init_api()
    assert len(prompts) == 1


def test_ctrl_c_aborts_without_a_bundle(garmin, tmp_path) -> None:
    recipe = _write_recipe(tmp_path / "r.json", [SHOES, _fa("fa", days=1)])
    garmin.fail("get_gear", KeyboardInterrupt())
    code, doc = _cli("--recipe", str(recipe))
    assert code == 130 and doc["error"]["code"] == "internal" and doc["error"]["message"] == "Aborted by user."
    assert not bundle_path().exists() and garmin.called("get_fitnessage_data") == []


def test_previous_bundle_missing_or_corrupt(garmin, tmp_path) -> None:
    recipe = _write_recipe(tmp_path / "r.json", [SHOES, _fa("fa", days=1)])
    garmin.fail("get_gear", NOT_FOUND)  # exercises the fallback on each kind of previous bundle
    bundle = bundle_path()
    other_ids = json.dumps({"datasets": {"gone": _section(["x"], [{"x": 1}]), "shoes": {"rows": "x", "meta": []}}})
    for previous in (None, "{not json", "[1, 2]", '{"datasets": []}', other_ids):
        if previous is not None:
            bundle.parent.mkdir(parents=True, exist_ok=True)
            bundle.write_text(previous)
        code, doc = _cli("--recipe", str(recipe))
        assert code == 0, (previous, doc)
        written = json.loads(bundle.read_text())["datasets"]
        assert set(written) == {"shoes", "fa"} and set(doc["data"]["datasets"]) == {"shoes", "fa"}
        assert written["shoes"]["rows"] == [] and written["shoes"]["meta"]["stale"] is True
        assert len(written["fa"]["rows"]) == 1


def test_cli_check_init_and_run(garmin, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    ids = [d["id"] for d in DEFAULT_RECIPE["datasets"]]
    code, doc = _cli("--check")  # no recipe file yet: the built-in default
    assert code == 0 and doc["data"] == {"recipe": "default", "datasets": ids, "valid": True}

    code, doc = _cli("--init")
    assert code == 0 and doc["data"] == {"path": str(recipe_path()), "datasets": len(ids)}
    written = json.loads(recipe_path().read_text())
    assert written == DEFAULT_RECIPE and all(next(iter(d)) == "id" for d in written["datasets"])
    code, doc = _cli("--init")
    assert code == 2 and doc["error"]["code"] == "bad_args"
    code, doc = _cli("--check")
    assert code == 0 and doc["data"]["recipe"] == "~/.pace/dashboard/recipe.json"

    bad = _write_recipe(tmp_path / "bad.json", [{**_fa("fa"), "argv": ["--days", "x"]}])
    code, doc = _cli("--check", "--recipe", str(bad))
    assert code == 2 and doc["error"]["code"] == "bad_args"
    assert "fa" in doc["error"]["message"] and "--days" in doc["error"]["message"]
    code, doc = _cli("--recipe", str(tmp_path / "missing.json"))
    assert code == 2 and doc["error"]["code"] == "bad_args"
    (tmp_path / "broken.json").write_text("{")
    code, doc = _cli("--check", "--recipe", str(tmp_path / "broken.json"))
    assert code == 2 and doc["error"]["code"] == "bad_args"
    small = _write_recipe(tmp_path / "small.json", [SHOES])
    code, doc = _cli("--recipe", str(small), "--max-calls", "0")
    assert code == 2 and doc["error"]["code"] == "bad_args"
    assert garmin.calls == [] and not bundle_path().exists()

    code, doc = _cli("--recipe", str(small))
    bundle = bundle_path()
    assert code == 0 and stat.S_IMODE(bundle.stat().st_mode) == 0o600
    text = bundle.read_text()
    written = json.loads(text)
    assert [m["label"] for m in written["meta"]] == ["Generated", "Recipe"]
    assert written["meta"][1]["value"] == "~/small.json" and str(tmp_path) not in text
    assert list(written["datasets"]["shoes"]["rows"][0]) == ["uuid", "name"]
    data = doc["data"]
    assert list(data) == ["path", "recipe", "bytes", "calls", "seconds", "datasets"]
    assert (data["path"], data["recipe"], data["bytes"], data["calls"]) == (
        str(bundle), "~/small.json", bundle.stat().st_size, 2)
    assert data["datasets"]["shoes"] == {"rows": 3, "calls": 2, "stale": False, "reason": None, "error": None,
                                         "day_errors": 0, "errors": []}
    assert doc["args"] == {"recipe": str(small), "out": str(bundle), "max_calls": 60, "max_seconds": 480,
                           "check": False, "init": False}
