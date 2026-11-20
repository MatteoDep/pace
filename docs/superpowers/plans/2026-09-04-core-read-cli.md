# Pace 2.0 Core Read CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `pace` into a machine-first Garmin Connect CLI: one compact JSON envelope per call, pruned and bucketed payloads, ranged date commands driven by a spec registry, and a `pace schema` manifest.

**Architecture:** `EndpointSpec` entries in `pace/spec.py` describe every simple endpoint; `pace/factory.py` turns each into a Typer command with uniform flags, per-day fan-out, caching, shaping (`pace/shape.py`) and envelope emission (`pace/envelope.py`).
Irregular commands (`activity`, `auth`, `cache`, `config`, `schema`) are hand-written but reuse the same `envelope.run` runner and `shape.apply`.
All human-facing rendering (Rich, Textual, sparklines, dashboards) is deleted.

**Tech Stack:** Python 3.14, typer, garminconnect 0.2.38, pydantic v2 (config only), sqlite3, pytest + pytest-asyncio, typer `CliRunner`.

**Spec:** `docs/superpowers/specs/2026-09-04-llm-cli-design.md` (sections 1 to 4, 6; sections 7 and 8 are separate plans).

## Global Constraints

- Python `>=3.14`; dependencies after this plan: `garminconnect>=0.2.8`, `pydantic>=2.12`, `typer>=0.12` only.
- stdout carries exactly one compact JSON document + `\n` per invocation; stderr empty unless `--verbose`.
- Error codes: `bad_args`(2) `auth_required`(3) `rate_limited`(4) `not_found` `endpoint_unavailable` `network` `internal` (1).
- Pruning drops `null`, `""`, `[]`, `{}` recursively unless `--raw`.
- Bucket ladder: `1m 5m 15m 1h 3h 1d`; `auto` targets <= 120 rows per series.
- Date-keyed commands always return `data` as a list of `{"date": "YYYY-MM-DD", ...}` ascending.
- ASCII only in all files; markdown docs put a newline after each period.
- No Claude attribution in commits.
- Run Python via `.venv/bin/python` (or `bash -ic 'update_aws_token && uv run ...'`); plain `uv run` fails on the private index token.
- Tests: `.venv/bin/python -m pytest -q` must pass at every commit.

## File Map

| Path | Responsibility |
|---|---|
| `pace/api.py` | `ApiError`, `safe_call`, `paginate` (structured errors) |
| `pace/envelope.py` | envelope builders, `PaceError`, `Result`, `run()` runner, `emit()` |
| `pace/shape.py` | `prune`, `select_fields`, `parse_fields`, bucket ladder, `bucket_series`, `apply` |
| `pace/dates.py` | `parse_date`, `date_range`, `resolve_range` |
| `pace/cache.py` | `Cache.get_or_fetch -> (value, hit, age_s)`, `CacheTracker`, `ttl_for_day` |
| `pace/spec.py` | `SeriesSpec`, `EndpointSpec`, `REGISTRY`, `specs_for(group)` |
| `pace/factory.py` | `build_group`, `fetch_spec`, shared option declarations |
| `pace/commands/{wellness,training,device,gear,goal,profile}.py` | one-liners: `app = build_group("...")` (+ `profile show` composite) |
| `pace/commands/activity.py` | list/show/splits/details/weather/last/export/export-range |
| `pace/commands/{auth,cache,config,schema}.py` | hand-written, envelope-wrapped |
| `pace/cli.py` | root app, `--verbose`, usage-error interception, `main()` |
| `docs/CONTRACT.md`, `README.md`, `docs/PLUGINS.md` | docs |
| `tests/test_{api_errors,envelope,shape,dates,cache,spec,factory,activity,schema,cli}.py` | tests |
| `tests/fixtures/*.json` | recorded, sanitized payloads |
| `scripts/record_fixtures.py` | fixture recorder |

---

### Task 1: Teardown pass 1 (human UX, legacy files, deps)

**Files:**
- Delete: `pace/tui/`, `pace/progress.py`, `pace/commands/today.py`, `pace/commands/watch.py`, `pace/commands/browse.py`, `pace/commands/_trend_metrics.py`, `pace/render/chart.py`, `pace/render/trend.py`, `pace/render/colors.py`, `pace/models/trend.py`, `docs/man/`, `docs/STAGE1.md`..`docs/STAGE4.md`, `PROPOSAL.md`, `main.py`, `garmin_api.py`, `tests/test_chart.py`, `tests/test_colors.py`
- Move: `demo.py` -> `scripts/garmin_api_reference.py`
- Modify: `pace/cli.py`, `pace/commands/wellness.py`, `pace/commands/activity.py`, `pace/config.py`, `pyproject.toml`

**Interfaces:**
- Produces: a tree where `pace --help` works, no `aionode`/`rich`/`textual`/`readchar` imports remain outside `pace/render/table.py` (deleted in Task 12).

- [ ] **Step 1: Delete and move files**

```bash
git rm -r -q pace/tui pace/progress.py pace/commands/today.py pace/commands/watch.py \
  pace/commands/browse.py pace/commands/_trend_metrics.py pace/render/chart.py \
  pace/render/trend.py pace/render/colors.py pace/models/trend.py docs/man \
  docs/STAGE1.md docs/STAGE2.md docs/STAGE3.md docs/STAGE4.md PROPOSAL.md main.py \
  garmin_api.py tests/test_chart.py tests/test_colors.py
git mv demo.py scripts/garmin_api_reference.py
```

- [ ] **Step 2: Remove the `trend` command and progress UI from `wellness.py`**

Delete the `trend` function and these imports: `asyncio`, `sys`, `from pace.commands._trend_metrics import METRICS`, `from pace.dates import date_range`, `from pace.models.list import ListModel`, `from pace.models.trend import TrendPoint, TrendSeries`, `from pace.progress import named_node, progress_ui`, `from pace.render.trend import render_trend`.
Keep `from pace.render import Format`, `from pace.dates import parse_date`.

- [ ] **Step 3: Remove progress UI from `activity.py`**

Delete `from pace.progress import named_node, progress_ui`.
In `export_range`, replace the `async with progress_ui():` block with:

```python
        await asyncio.gather(*(fetch_one(a["activityId"]) for a in listing))
```

- [ ] **Step 4: Trim `cli.py`**

Remove the imports and calls for `browse_app`, `register_today`, `register_watch`, and the `app.add_typer(browse_app, ...)` line.

- [ ] **Step 5: Trim config**

In `pace/config.py` remove `default_format` and `dashboard_panels` from `PaceConfig`.
In `pace/render/__init__.py` change `default_format()` to:

```python
def default_format() -> Format:
    return Format.JSON
```

- [ ] **Step 6: Trim dependencies**

`pyproject.toml` `dependencies` becomes:

```toml
dependencies = [
    "garminconnect>=0.2.8",
    "pydantic>=2.12",
    "typer>=0.12",
]
```

Remove `click-man` from `dev`.
Bump `version = "2.0.0"` here and `__version__ = "2.0.0"` in `pace/__init__.py`.
Run `bash -ic 'update_aws_token && uv lock && uv sync'`.

- [ ] **Step 7: Verify**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -m pace --help && .venv/bin/python -m pace wellness --help`
Expected: tests pass (20), help prints without `trend`, `today`, `watch`, `browse`.
Run: `grep -rn "aionode\|textual\|readchar\|progress_ui" pace tests` -> no output.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: strip human-facing UI, legacy scripts and unused deps"
```

---

### Task 2: Structured API errors

**Files:**
- Modify: `pace/api.py`
- Test: `tests/test_api_errors.py`

**Interfaces:**
- Produces:
  - `ApiError(code: str, message: str, http: int | None = None)` frozen dataclass in `pace.api`.
  - `async safe_call(method_name, *args, **kwargs) -> tuple[bool, Any, ApiError | None]`.
  - `classify_http(code: int | None, msg: str) -> ApiError`.
  - `paginate(...)` raises `PaceError` (from Task 3) -- for this task it raises `RuntimeError(err.message)`; Task 3 switches it.

- [ ] **Step 1: Replace tests**

Overwrite `tests/test_api_errors.py`:

```python
"""Tests for HTTP error classification in the API wrapper."""

from pace.api import ApiError, _status_from_text, classify_http


def test_status_from_parenthesised_code() -> None:
    assert _status_from_text("API client error (404): not found") == 404


def test_status_from_bare_code() -> None:
    assert _status_from_text("Error in request: 503 Service Unavailable") == 503


def test_status_none_when_absent() -> None:
    assert _status_from_text("Connection error: name resolution failed") is None
    assert _status_from_text("retrieved 200 items") is None


def test_classify_codes() -> None:
    assert classify_http(401, "x").code == "auth_required"
    assert classify_http(429, "x").code == "rate_limited"
    assert classify_http(404, "x").code == "not_found"
    assert classify_http(400, "x").code == "endpoint_unavailable"
    assert classify_http(403, "x").code == "endpoint_unavailable"
    assert classify_http(500, "x").code == "network"
    assert classify_http(503, "x").code == "network"
    assert classify_http(418, "teapot") == ApiError("internal", "HTTP error: teapot", 418)
    assert classify_http(None, "boom").code == "internal"


def test_classify_keeps_http_code() -> None:
    err = classify_http(404, "API client error (404): gone")
    assert err.http == 404
    assert "404" in err.message
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_api_errors.py -q`
Expected: ImportError on `ApiError`.

- [ ] **Step 3: Implement**

In `pace/api.py` replace `_HTTP_LABELS`, `_classify_http_error`, and the body of `safe_call` with:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ApiError:
    code: str
    message: str
    http: int | None = None


_HTTP_CODES: dict[int, tuple[str, str]] = {
    400: ("endpoint_unavailable", "Endpoint not available (400) -- not enabled for your account"),
    401: ("auth_required", "Auth required (401) -- re-authenticate"),
    403: ("endpoint_unavailable", "Access denied (403)"),
    404: ("not_found", "Endpoint not found (404)"),
    429: ("rate_limited", "Rate limit exceeded (429)"),
}


def classify_http(code: int | None, msg: str) -> ApiError:
    if code in _HTTP_CODES:
        kind, label = _HTTP_CODES[code]
        return ApiError(kind, label, code)
    if code is not None and 500 <= code < 600:
        return ApiError("network", f"Server error ({code})", code)
    return ApiError("internal", f"HTTP error: {msg}", code)


async def safe_call(
    method_name: str, *args: Any, **kwargs: Any
) -> tuple[bool, Any, ApiError | None]:
    """Rate-limited async wrapper. Returns (success, result, error)."""
    await limiter.acquire()
    async with concurrency:
        try:
            method = getattr(get_api(), method_name)
            result = await asyncio.to_thread(method, *args, **kwargs)
            return True, result, None
        except GarthHTTPError as err:
            code = getattr(getattr(err, "response", None), "status_code", None)
            return False, None, classify_http(code, str(err))
        except FileNotFoundError:
            return False, None, ApiError("auth_required", "No valid tokens. Run `pace auth login`.")
        except GarminConnectAuthenticationError as err:
            return False, None, ApiError("auth_required", f"Auth issue: {err}")
        except GarminConnectConnectionError as err:
            code = _status_from_text(str(err))
            if code is not None:
                return False, None, classify_http(code, str(err))
            return False, None, ApiError("network", f"Connection issue: {err}")
        except GarminConnectTooManyRequestsError as err:
            return False, None, ApiError("rate_limited", f"Rate limit: {err}")
        except Exception as err:
            return False, None, ApiError("internal", f"Unexpected error: {err}")
```

In `paginate`, change `raise RuntimeError(err)` to `raise RuntimeError(err.message)`.
In `pace/commands/_helpers.py` `cached_call`, change `raise RuntimeError(err)` to `raise RuntimeError(err.message)`.
In `pace/commands/activity.py` `_download_one` and `export_range`, same change (`err.message`).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pace/api.py pace/commands/_helpers.py pace/commands/activity.py tests/test_api_errors.py
git commit -m "refactor: structured ApiError from safe_call"
```

---

### Task 3: Envelope module and runner

**Files:**
- Create: `pace/envelope.py`
- Test: `tests/test_envelope.py`

**Interfaces:**
- Produces (all in `pace.envelope`):
  - `EXIT_CODES: dict[str, int]` = `{"bad_args": 2, "auth_required": 3, "rate_limited": 4}`; `exit_code(code) -> int` returns 1 for any other code.
  - `HINTS: dict[str, str]` per error code.
  - `class PaceError(Exception)`: `code: str`, `message: str`, `http: int | None`, `hint: str | None`; constructor `PaceError(code, message, http=None, hint=None)`; `PaceError.from_api(err: ApiError)`.
  - `@dataclass class Result`: `data: Any`, `cache: dict | None = None`, `shape: dict | None = None`, `truncated: bool = False`.
  - `ok(command: str, args: dict, result: Result) -> dict`.
  - `error(command: str, err: PaceError) -> dict`.
  - `emit(doc: dict) -> None` writes compact JSON + `\n` to stdout.
  - `run(command: str, args: dict, work: Callable[[], Awaitable[Result]]) -> None`: `asyncio.run(work())`, emits ok or error envelope, raises `typer.Exit(exit_code)` on error.
  - `log(msg: str)`: writes to stderr only when `VERBOSE` module flag is true; `set_verbose(bool)`.

- [ ] **Step 1: Write failing tests**

`tests/test_envelope.py`:

```python
"""Envelope shape, exit codes, runner behaviour."""

import json

import pytest
import typer

from pace.api import ApiError
from pace.envelope import PaceError, Result, emit, error, exit_code, ok, run


def test_ok_shape() -> None:
    doc = ok("wellness sleep", {"from": "2026-01-01"}, Result(data=[1], cache={"hit": True, "oldest_age_s": 5}))
    assert doc == {
        "ok": True,
        "command": "wellness sleep",
        "args": {"from": "2026-01-01"},
        "cache": {"hit": True, "oldest_age_s": 5},
        "shape": None,
        "truncated": False,
        "data": [1],
    }


def test_error_shape_and_hint() -> None:
    doc = error("x", PaceError.from_api(ApiError("auth_required", "nope", 401)))
    assert doc["ok"] is False
    assert doc["command"] == "x"
    assert doc["error"]["code"] == "auth_required"
    assert doc["error"]["http"] == 401
    assert "pace auth login" in doc["error"]["hint"]


def test_exit_codes() -> None:
    assert exit_code("bad_args") == 2
    assert exit_code("auth_required") == 3
    assert exit_code("rate_limited") == 4
    assert exit_code("not_found") == 1
    assert exit_code("internal") == 1


def test_emit_is_compact_single_line(capsys) -> None:
    emit({"a": [1, 2], "b": {"c": None}})
    out = capsys.readouterr().out
    assert out == '{"a":[1,2],"b":{"c":null}}\n'


def test_run_success(capsys) -> None:
    async def work() -> Result:
        return Result(data={"v": 1})

    run("cmd", {}, work)
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is True and doc["data"] == {"v": 1}


def test_run_error_exits_with_code(capsys) -> None:
    async def work() -> Result:
        raise PaceError("rate_limited", "slow down", 429)

    with pytest.raises(typer.Exit) as exc:
        run("cmd", {}, work)
    assert exc.value.exit_code == 4
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["error"]["code"] == "rate_limited"
    assert capsys.readouterr().err == ""


def test_run_unexpected_exception_becomes_internal(capsys) -> None:
    async def work() -> Result:
        raise ValueError("boom")

    with pytest.raises(typer.Exit) as exc:
        run("cmd", {}, work)
    assert exc.value.exit_code == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["error"]["code"] == "internal" and "boom" in doc["error"]["message"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_envelope.py -q`
Expected: ModuleNotFoundError `pace.envelope`.

- [ ] **Step 3: Implement `pace/envelope.py`**

```python
"""JSON envelope: the only thing pace ever writes to stdout."""

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import typer

from pace.api import ApiError

EXIT_CODES: dict[str, int] = {"bad_args": 2, "auth_required": 3, "rate_limited": 4}

HINTS: dict[str, str] = {
    "bad_args": "Check `pace schema` for accepted arguments.",
    "auth_required": "Ask the user to run `pace auth login` (interactive MFA). Do not run it yourself.",
    "rate_limited": "Wait 60 s and retry once; then stop and tell the user.",
    "not_found": "The id or date does not exist on Garmin Connect.",
    "endpoint_unavailable": "Feature not enabled for this account or device; do not retry.",
    "network": "Transient. Retry once after a few seconds.",
    "internal": "Unexpected. Re-run with --verbose and report the stderr output.",
}

VERBOSE = False


def set_verbose(flag: bool) -> None:
    global VERBOSE
    VERBOSE = flag


def log(msg: str) -> None:
    if VERBOSE:
        print(msg, file=sys.stderr)


class PaceError(Exception):
    def __init__(self, code: str, message: str, http: int | None = None, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http = http
        self.hint = hint or HINTS.get(code)

    @classmethod
    def from_api(cls, err: ApiError) -> "PaceError":
        return cls(err.code, err.message, err.http)


@dataclass
class Result:
    data: Any
    cache: dict[str, Any] | None = None
    shape: dict[str, Any] | None = None
    truncated: bool = False


def exit_code(code: str) -> int:
    return EXIT_CODES.get(code, 1)


def ok(command: str, args: dict[str, Any], result: Result) -> dict[str, Any]:
    return {
        "ok": True,
        "command": command,
        "args": args,
        "cache": result.cache,
        "shape": result.shape,
        "truncated": result.truncated,
        "data": result.data,
    }


def error(command: str, err: PaceError) -> dict[str, Any]:
    return {
        "ok": False,
        "command": command,
        "error": {"code": err.code, "http": err.http, "message": err.message, "hint": err.hint},
    }


def emit(doc: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(doc, separators=(",", ":"), default=str))
    sys.stdout.write("\n")


def run(command: str, args: dict[str, Any], work: Callable[[], Awaitable[Result]]) -> None:
    try:
        result = asyncio.run(work())
    except PaceError as err:
        emit(error(command, err))
        raise typer.Exit(exit_code(err.code)) from None
    except Exception as exc:  # noqa: BLE001 -- every failure must still produce JSON
        err = PaceError("internal", f"{type(exc).__name__}: {exc}")
        emit(error(command, err))
        raise typer.Exit(1) from None
    emit(ok(command, args, result))
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_envelope.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add pace/envelope.py tests/test_envelope.py
git commit -m "feat: JSON envelope module and command runner"
```

---

### Task 4: Shaping (prune, fields, buckets)

**Files:**
- Create: `pace/shape.py`
- Test: `tests/test_shape.py`

**Interfaces:**
- Consumes: nothing from other tasks (SeriesSpec is defined here to avoid a cycle; `pace/spec.py` re-exports it).
- Produces (all in `pace.shape`):
  - `@dataclass(frozen=True) SeriesSpec(path: str, kind: Literal["pairs", "records"], t_key: str = "0", v_key: str = "1")`.
  - `BUCKETS: dict[str, int]` = `{"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "3h": 10800, "1d": 86400}`; `MAX_ROWS = 120`.
  - `prune(obj) -> obj`.
  - `parse_fields(value: str) -> str | list[str]` ("default", "all", or list).
  - `select_fields(obj: dict, paths: list[str]) -> dict`.
  - `choose_bucket(span_s: int, n_samples: int) -> str` ("none" or a ladder key).
  - `extract_points(seq: list, spec: SeriesSpec) -> list[tuple[int, float]]` (epoch ms, value).
  - `bucket_series(points, bucket_s: int, tz_offset_s: int) -> dict` = `{"cols": ["t","avg","min","max","n"], "rows": [...]}`.
  - `tz_offset_s(data: dict) -> int`: from any `<X>GMT`/`<X>Local` ISO pair at top level, else system local offset.
  - `apply(data, *, series: tuple[SeriesSpec, ...], default_fields: tuple[str, ...], fields: str | list[str], bucket: str, raw: bool, span_s: int) -> tuple[Any, dict]`; the dict is the envelope `shape` block: `{"bucket_used": str, "reduced_series": [paths], "fields": "default"|"all"|[...]}` or `{"raw": True}`.
  - `parse_bucket(value: str) -> str` validating `auto|none|<ladder>`; raises `PaceError("bad_args", ...)`.

- [ ] **Step 1: Write failing tests**

`tests/test_shape.py`:

```python
"""Pruning, field selection, and time-series bucketing."""

import pytest

from pace.envelope import PaceError
from pace.shape import (
    SeriesSpec,
    apply,
    bucket_series,
    choose_bucket,
    extract_points,
    parse_bucket,
    parse_fields,
    prune,
    select_fields,
    tz_offset_s,
)

MS = 1000
HOUR = 3600 * MS


def test_prune_removes_empties_recursively() -> None:
    src = {"a": None, "b": "", "c": [], "d": {}, "e": {"f": None, "g": 0}, "h": [None, 1, {}], "i": False}
    assert prune(src) == {"e": {"g": 0}, "h": [1], "i": False}


def test_parse_fields() -> None:
    assert parse_fields("default") == "default"
    assert parse_fields("all") == "all"
    assert parse_fields("a, b.c") == ["a", "b.c"]


def test_select_fields_dotted_and_missing() -> None:
    src = {"a": 1, "b": {"c": 2, "d": 3}, "e": 4}
    assert select_fields(src, ["a", "b.c", "zzz", "b.nope"]) == {"a": 1, "b": {"c": 2}}


def test_choose_bucket_ladder() -> None:
    assert choose_bucket(span_s=3600, n_samples=50) == "none"
    assert choose_bucket(span_s=86400, n_samples=1440) == "15m"   # 96 rows
    assert choose_bucket(span_s=7 * 86400, n_samples=10000) == "3h"  # 56 rows
    assert choose_bucket(span_s=30 * 86400, n_samples=10000) == "1d"  # 30 rows; beyond ladder stays 1d


def test_extract_points_pairs_and_records() -> None:
    pairs = [[HOUR, 60], [HOUR + MS, None], [HOUR + 2 * MS, 62]]
    assert extract_points(pairs, SeriesSpec("x", "pairs")) == [(HOUR, 60.0), (HOUR + 2 * MS, 62.0)]
    recs = [{"startGMT": "1970-01-01T01:00:00.0", "value": 60}, {"startGMT": "1970-01-01T01:00:01.0", "value": 61}]
    got = extract_points(recs, SeriesSpec("x", "records", t_key="startGMT", v_key="value"))
    assert got == [(HOUR, 60.0), (HOUR + MS, 61.0)]


def test_bucket_series_math_and_local_time() -> None:
    pts = [(HOUR, 60.0), (HOUR + 60 * MS, 70.0), (HOUR + 15 * 60 * MS, 50.0)]
    out = bucket_series(pts, bucket_s=900, tz_offset_s=3600)
    assert out["cols"] == ["t", "avg", "min", "max", "n"]
    assert out["rows"] == [["1970-01-01T02:00", 65.0, 60.0, 70.0, 2], ["1970-01-01T02:15", 50.0, 50.0, 50.0, 1]]


def test_tz_offset_from_gmt_local_pair() -> None:
    d = {"startTimeGMT": "2026-06-01T05:00:00.0", "startTimeLocal": "2026-06-01T07:00:00.0"}
    assert tz_offset_s(d) == 7200
    d2 = {"sleepStartTimestampGMT": 1_700_000_000_000, "sleepStartTimestampLocal": 1_700_003_600_000}
    assert tz_offset_s(d2) == 3600


def test_parse_bucket() -> None:
    assert parse_bucket("auto") == "auto"
    assert parse_bucket("1h") == "1h"
    with pytest.raises(PaceError) as exc:
        parse_bucket("7m")
    assert exc.value.code == "bad_args"


def _hr_day() -> dict:
    values = [[i * 60 * MS, 60 + (i % 10)] for i in range(1440)]
    return {"calendarDate": "1970-01-01", "restingHeartRate": 50, "junk": None, "heartRateValues": values}


def test_apply_default_fields_and_auto_bucket() -> None:
    data, shape = apply(
        _hr_day(),
        series=(SeriesSpec("heartRateValues", "pairs"),),
        default_fields=("calendarDate", "restingHeartRate", "heartRateValues"),
        fields="default", bucket="auto", raw=False, span_s=86400,
    )
    assert set(data) == {"calendarDate", "restingHeartRate", "heartRateValues"}
    assert data["heartRateValues"]["cols"][0] == "t"
    assert len(data["heartRateValues"]["rows"]) == 96
    assert shape == {"bucket_used": "15m", "reduced_series": ["heartRateValues"], "fields": "default"}


def test_apply_bucket_none_keeps_full_series() -> None:
    data, shape = apply(_hr_day(), series=(SeriesSpec("heartRateValues", "pairs"),),
                        default_fields=(), fields="all", bucket="none", raw=False, span_s=86400)
    assert len(data["heartRateValues"]) == 1440
    assert shape["bucket_used"] == "none" and shape["reduced_series"] == []
    assert "junk" not in data


def test_apply_raw_bypasses_everything() -> None:
    src = _hr_day()
    data, shape = apply(src, series=(SeriesSpec("heartRateValues", "pairs"),), default_fields=("x",),
                        fields="default", bucket="auto", raw=True, span_s=86400)
    assert data is src and shape == {"raw": True}


def test_apply_on_list_payload_shapes_each_item() -> None:
    items = [{"a": 1, "b": None}, {"a": 2, "c": 3}]
    data, _ = apply(items, series=(), default_fields=("a",), fields="default", bucket="auto", raw=False, span_s=0)
    assert data == [{"a": 1}, {"a": 2}]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_shape.py -q`
Expected: ModuleNotFoundError `pace.shape`.

- [ ] **Step 3: Implement `pace/shape.py`**

```python
"""Payload shaping: prune empties, select fields, bucket time series."""

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from pace.envelope import PaceError

BUCKETS: dict[str, int] = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "3h": 10800, "1d": 86400}
MAX_ROWS = 120
COLS = ["t", "avg", "min", "max", "n"]


@dataclass(frozen=True)
class SeriesSpec:
    path: str
    kind: Literal["pairs", "records"]
    t_key: str = "0"
    v_key: str = "1"


def prune(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            pv = prune(v)
            if pv is None or pv == "" or pv == [] or pv == {}:
                continue
            out[k] = pv
        return out
    if isinstance(obj, list):
        out_l = []
        for v in obj:
            pv = prune(v)
            if pv is None or pv == "" or pv == [] or pv == {}:
                continue
            out_l.append(pv)
        return out_l
    return obj


def parse_fields(value: str) -> str | list[str]:
    value = value.strip()
    if value in ("default", "all"):
        return value
    return [p.strip() for p in value.split(",") if p.strip()]


def parse_bucket(value: str) -> str:
    if value in ("auto", "none") or value in BUCKETS:
        return value
    raise PaceError("bad_args", f"Invalid --bucket {value!r}. Use auto, none, or one of {', '.join(BUCKETS)}.")


def _get_path(obj: Any, path: str) -> tuple[bool, Any]:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def _set_path(obj: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def select_fields(obj: dict, paths: list[str]) -> dict:
    out: dict = {}
    for p in paths:
        found, v = _get_path(obj, p)
        if found:
            _set_path(out, p, v)
    return out


def choose_bucket(span_s: int, n_samples: int) -> str:
    if n_samples <= MAX_ROWS:
        return "none"
    for name, secs in BUCKETS.items():
        if span_s / secs <= MAX_ROWS:
            return name
    return "1d"


def _to_ms(t: Any) -> int | None:
    if isinstance(t, bool):
        return None
    if isinstance(t, (int, float)):
        return int(t)
    if isinstance(t, str):
        try:
            dt = datetime.fromisoformat(t.replace(".0", "") if t.endswith(".0") else t)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    return None


def extract_points(seq: list, spec: SeriesSpec) -> list[tuple[int, float]]:
    pts: list[tuple[int, float]] = []
    for item in seq:
        if spec.kind == "pairs":
            if not isinstance(item, (list, tuple)):
                continue
            ti, vi = int(spec.t_key), int(spec.v_key)
            if len(item) <= max(ti, vi):
                continue
            t, v = item[ti], item[vi]
        else:
            if not isinstance(item, dict):
                continue
            t, v = item.get(spec.t_key), item.get(spec.v_key)
        ms = _to_ms(t)
        if ms is None or not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        pts.append((ms, float(v)))
    return pts


def bucket_series(points: list[tuple[int, float]], bucket_s: int, tz_offset_s: int) -> dict:
    groups: dict[int, list[float]] = {}
    for ms, v in points:
        start = (ms // 1000 // bucket_s) * bucket_s
        groups.setdefault(start, []).append(v)
    rows = []
    for start in sorted(groups):
        vals = groups[start]
        local = datetime.fromtimestamp(start + tz_offset_s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M")
        rows.append([local, round(sum(vals) / len(vals), 1), min(vals), max(vals), len(vals)])
    return {"cols": COLS, "rows": rows}


def tz_offset_s(data: Any) -> int:
    if isinstance(data, dict):
        for key, gmt in data.items():
            if not key.endswith("GMT"):
                continue
            local = data.get(key[:-3] + "Local")
            g, l = _to_ms(gmt), _to_ms(local)
            if g is not None and l is not None:
                return int((l - g) // 1000)
    return -time.timezone if not time.localtime().tm_isdst else -time.altzone


def _shape_one(item: Any, *, series, default_fields, fields, bucket, span_s, report) -> Any:
    if not isinstance(item, dict):
        return prune(item)
    offset = tz_offset_s(item)
    out = dict(item)
    for s in series:
        found, seq = _get_path(out, s.path)
        if not found or not isinstance(seq, list):
            continue
        pts = extract_points(seq, s)
        chosen = choose_bucket(span_s, len(pts)) if bucket == "auto" else bucket
        if chosen == "none":
            continue
        _set_path(out, s.path, bucket_series(pts, BUCKETS[chosen], offset))
        report["bucket_used"] = chosen
        if s.path not in report["reduced_series"]:
            report["reduced_series"].append(s.path)
    if fields == "default" and default_fields:
        out = select_fields(out, list(default_fields))
    elif isinstance(fields, list):
        out = select_fields(out, fields)
    return prune(out)


def apply(data: Any, *, series: tuple[SeriesSpec, ...], default_fields: tuple[str, ...],
          fields: str | list[str], bucket: str, raw: bool, span_s: int) -> tuple[Any, dict]:
    if raw:
        return data, {"raw": True}
    report: dict[str, Any] = {"bucket_used": "none", "reduced_series": [], "fields": fields}
    kw = dict(series=series, default_fields=default_fields, fields=fields, bucket=bucket, span_s=span_s, report=report)
    if isinstance(data, list):
        return [_shape_one(i, **kw) for i in data], report
    return _shape_one(data, **kw), report
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_shape.py -q`
Expected: 12 passed.
If `test_bucket_series_math_and_local_time` fails on `avg` rounding, check `round(sum/len, 1)` gives `65.0`.

- [ ] **Step 5: Commit**

```bash
git add pace/shape.py tests/test_shape.py
git commit -m "feat: payload shaping (prune, field select, series bucketing)"
```

---

### Task 5: Date ranges and cache accounting

**Files:**
- Modify: `pace/dates.py`, `pace/cache.py`, `pace/commands/_helpers.py`
- Test: `tests/test_dates.py` (new), `tests/test_cache.py`

**Interfaces:**
- Produces:
  - `pace.dates.resolve_range(from_: str | None, to: str, days: int) -> tuple[date, date]`; `--from` wins over `--days`; swaps if start > end; raises `PaceError("bad_args")` on bad input (wraps `typer.BadParameter`).
  - `pace.dates.parse_date` now raises `PaceError("bad_args", ...)` instead of `typer.BadParameter`.
  - `pace.cache.Cache.get_or_fetch(key, fetch, ttl) -> tuple[Any, bool, int]` = `(value, hit, age_s)`.
  - `pace.cache.CacheTracker`: `.add(hit: bool, age_s: int)`, `.report() -> dict | None` = `{"hit": True|False|"partial", "oldest_age_s": int}`; `None` when nothing recorded.
  - `pace.cache.ttl_for_day(day: date, today_ttl: int, today: date | None = None) -> int`: today -> `today_ttl`, yesterday -> 86400, older -> 604800.
  - `pace.commands._helpers.cached_call(method, *args, cache, ttl, tracker=None, **kw) -> Any`: raises `PaceError.from_api(err)` on failure and records into `tracker`.

- [ ] **Step 1: Write failing tests**

`tests/test_dates.py`:

```python
from datetime import date, timedelta

import pytest

from pace.dates import parse_date, resolve_range
from pace.envelope import PaceError


def test_days_ending_today() -> None:
    start, end = resolve_range(None, "today", 7)
    assert end == date.today() and start == end - timedelta(days=6)


def test_from_overrides_days_and_swaps() -> None:
    start, end = resolve_range("2026-01-10", "2026-01-01", 99)
    assert (start, end) == (date(2026, 1, 1), date(2026, 1, 10))


def test_single_day_default() -> None:
    start, end = resolve_range(None, "2026-03-03", 1)
    assert start == end == date(2026, 3, 3)


def test_bad_date_is_bad_args() -> None:
    with pytest.raises(PaceError) as exc:
        parse_date("garbage")
    assert exc.value.code == "bad_args"
    with pytest.raises(PaceError):
        resolve_range(None, "today", 0)
```

Append to `tests/test_cache.py`:

```python
from datetime import date, timedelta

from pace.cache import CacheTracker, ttl_for_day


@pytest.mark.asyncio
async def test_get_or_fetch_reports_hit_and_age(tmp_path) -> None:
    cache = Cache(tmp_path / "c.db")
    v, hit, age = await cache.get_or_fetch("k", lambda: _ret(1), ttl=60)
    assert (v, hit, age) == (1, False, 0)
    v, hit, age = await cache.get_or_fetch("k", lambda: _ret(2), ttl=60)
    assert v == 1 and hit is True and 0 <= age < 5
    cache.close()


def test_tracker_report() -> None:
    t = CacheTracker()
    assert t.report() is None
    t.add(True, 30)
    assert t.report() == {"hit": True, "oldest_age_s": 30}
    t.add(False, 0)
    assert t.report() == {"hit": "partial", "oldest_age_s": 30}
    t2 = CacheTracker()
    t2.add(False, 0)
    assert t2.report() == {"hit": False, "oldest_age_s": 0}


def test_ttl_for_day() -> None:
    today = date(2026, 9, 4)
    assert ttl_for_day(today, 300, today) == 300
    assert ttl_for_day(today - timedelta(days=1), 300, today) == 86400
    assert ttl_for_day(today - timedelta(days=2), 300, today) == 604800
```

Update the existing `test_miss_then_hit` and `test_ttl_expiry` to unpack the tuple: `assert (await cache.get_or_fetch("k", fetch, ttl=60))[0] == {"x": 1}`.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_dates.py tests/test_cache.py -q`
Expected: ImportError `resolve_range`, `CacheTracker`.

- [ ] **Step 3: Implement dates**

`pace/dates.py` (full file):

```python
"""Date parsing and range resolution."""

import re
from collections.abc import Iterator
from datetime import date, timedelta

from pace.envelope import PaceError

_RELATIVE = re.compile(r"^([+-]?\d+)d$")


def parse_date(value: str | None) -> date:
    """Parse YYYY-MM-DD, 'today', 'yesterday', or '-Nd' / '+Nd' (relative to today)."""
    if value is None or value == "today":
        return date.today()
    if value == "yesterday":
        return date.today() - timedelta(days=1)
    m = _RELATIVE.match(value)
    if m:
        return date.today() + timedelta(days=int(m.group(1)))
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise PaceError("bad_args", f"Invalid date {value!r}. Use YYYY-MM-DD, today, yesterday, or -Nd.") from None


def date_range(start: date, end: date) -> Iterator[date]:
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def resolve_range(from_: str | None, to: str, days: int) -> tuple[date, date]:
    """Resolve --from/--to/--days into an inclusive (start, end). --from wins over --days."""
    if days < 1:
        raise PaceError("bad_args", "--days must be >= 1.")
    end = parse_date(to)
    start = parse_date(from_) if from_ else end - timedelta(days=days - 1)
    if start > end:
        start, end = end, start
    return start, end
```

- [ ] **Step 4: Implement cache changes**

In `pace/cache.py`, change `_get` to return `(value, age_s)` or `None`, `get_or_fetch` to return the tuple, and add the helpers:

```python
    def _get(self, key: str) -> tuple[Any, int] | None:
        cur = self._conn.execute("SELECT value, fetched_at, ttl FROM cache WHERE key = ?", (key,))
        row = cur.fetchone()
        if not row:
            return None
        value, fetched_at, ttl = row
        age = int(time.time() - fetched_at)
        if age >= ttl:
            return None
        return json.loads(value), age

    async def get_or_fetch(self, key: str, fetch: Callable[[], Awaitable[Any]], ttl: int) -> tuple[Any, bool, int]:
        """Return (value, hit, age_s)."""
        async with self._lock:
            cached = self._get(key)
            if cached is not None:
                return cached[0], True, cached[1]
        value = await fetch()
        async with self._lock:
            self._put(key, value, ttl)
        return value, False, 0


class CacheTracker:
    """Accumulates per-call hit/age so a fan-out can report one cache block."""

    def __init__(self) -> None:
        self._hits: list[bool] = []
        self._oldest = 0

    def add(self, hit: bool, age_s: int) -> None:
        self._hits.append(hit)
        self._oldest = max(self._oldest, age_s)

    def report(self) -> dict[str, Any] | None:
        if not self._hits:
            return None
        hit: bool | str = True if all(self._hits) else False if not any(self._hits) else "partial"
        return {"hit": hit, "oldest_age_s": self._oldest}


def ttl_for_day(day: date, today_ttl: int, today: date | None = None) -> int:
    today = today or date.today()
    if day >= today:
        return today_ttl
    if day == today - timedelta(days=1):
        return 86400
    return 604800
```

Add `from datetime import date, timedelta` at the top.

- [ ] **Step 5: Update `_helpers.cached_call`**

Replace the body of `cached_call` in `pace/commands/_helpers.py`:

```python
async def cached_call(method: str, *args: Any, cache: Cache | None, ttl: int,
                      tracker: CacheTracker | None = None, **kwargs: Any) -> Any:
    """Call a Garmin method through `safe_call`, optionally via the cache. Raises PaceError."""
    async def fetch() -> Any:
        ok, data, err = await safe_call(method, *args, **kwargs)
        if not ok:
            assert err is not None
            raise PaceError.from_api(err)
        return data

    if cache is None:
        value = await fetch()
        if tracker is not None:
            tracker.add(False, 0)
        return value

    key_parts = [method, *(str(a) for a in args)]
    if kwargs:
        key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
    value, hit, age = await cache.get_or_fetch(":".join(key_parts), fetch, ttl=ttl)
    log(f"{'hit ' if hit else 'miss'} {':'.join(key_parts)} age={age}s")
    if tracker is not None:
        tracker.add(hit, age)
    return value
```

Imports: `from pace.cache import Cache, CacheTracker`, `from pace.envelope import PaceError, log`.
The old `run()` in `_helpers` catches `RuntimeError`; add `except PaceError as err: typer.secho(...); raise typer.Exit(1)` so legacy commands keep working until Task 8 replaces them.
In `activity.py` `list_` the direct `cache.get_or_fetch(key, fetch_page, ...)` call must unpack `[0]`.

- [ ] **Step 6: Run all tests**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add pace/dates.py pace/cache.py pace/commands/_helpers.py pace/commands/activity.py tests/test_dates.py tests/test_cache.py
git commit -m "feat: range resolution, cache hit accounting, PaceError in cached_call"
```

---

### Task 6: Endpoint spec registry

**Files:**
- Create: `pace/spec.py`
- Test: `tests/test_spec.py`

**Interfaces:**
- Consumes: `pace.shape.SeriesSpec`.
- Produces (`pace.spec`):

```python
@dataclass(frozen=True)
class EndpointSpec:
    group: str
    name: str
    help: str
    method: str
    ttl: int
    dated: Literal[None, "single", "range"] = None   # single: method(cdate); range: method(start, end) called per day as (d, d)
    id_arg: str | None = None                        # positional name, e.g. "DEVICE_ID"
    id_type: type = int
    resolver: Literal[None, "upn"] = None            # prepend userProfileNumber
    default_fields: tuple[str, ...] = ()
    series: tuple[SeriesSpec, ...] = ()
    unwrap: Literal[None, "first"] = None

    @property
    def command(self) -> str: return f"{self.group} {self.name}"

REGISTRY: tuple[EndpointSpec, ...]
def specs_for(group: str) -> list[EndpointSpec]
GROUPS: dict[str, str]   # group -> help text
```

- [ ] **Step 1: Write failing tests**

`tests/test_spec.py`:

```python
import garminconnect

from pace.spec import GROUPS, REGISTRY, specs_for


def test_every_method_exists_on_garmin_client() -> None:
    missing = [s.command for s in REGISTRY if not hasattr(garminconnect.Garmin, s.method)]
    assert missing == []


def test_commands_unique_and_groups_known() -> None:
    names = [s.command for s in REGISTRY]
    assert len(names) == len(set(names))
    assert {s.group for s in REGISTRY} <= set(GROUPS)


def test_specs_for_group() -> None:
    assert [s.name for s in specs_for("wellness")][:2] == ["summary", "sleep"]
    assert specs_for("nope") == []


def test_series_paths_are_dotted_strings() -> None:
    for s in REGISTRY:
        for ser in s.series:
            assert ser.path and " " not in ser.path
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_spec.py -q` -> ModuleNotFoundError.

- [ ] **Step 3: Implement `pace/spec.py`**

`default_fields` start empty (= all fields) and are curated in Task 13 against recorded fixtures.

```python
"""Declarative endpoint registry: one EndpointSpec per simple Garmin read."""

from dataclasses import dataclass
from typing import Literal

from pace.shape import SeriesSpec

__all__ = ["EndpointSpec", "SeriesSpec", "REGISTRY", "GROUPS", "specs_for"]

TTL_LIVE = 300        # today's wellness
TTL_TRAINING = 3600
TTL_STATIC = 86400
TTL_PROFILE = 604800


@dataclass(frozen=True)
class EndpointSpec:
    group: str
    name: str
    help: str
    method: str
    ttl: int
    dated: Literal[None, "single", "range"] = None
    id_arg: str | None = None
    id_type: type = int
    resolver: Literal[None, "upn"] = None
    default_fields: tuple[str, ...] = ()
    series: tuple[SeriesSpec, ...] = ()
    unwrap: Literal[None, "first"] = None

    @property
    def command(self) -> str:
        return f"{self.group} {self.name}"


GROUPS: dict[str, str] = {
    "wellness": "Daily health metrics.",
    "training": "Training load and performance metrics.",
    "device": "Registered Garmin devices.",
    "gear": "Gear inventory and usage.",
    "goal": "User goals and personal records.",
    "profile": "User profile information.",
}

_pairs = lambda path: SeriesSpec(path, "pairs")  # noqa: E731

REGISTRY: tuple[EndpointSpec, ...] = (
    # wellness
    EndpointSpec("wellness", "summary", "Daily summary: steps, calories, distance, floors, resting HR.",
                 "get_user_summary", TTL_LIVE, dated="single"),
    EndpointSpec("wellness", "sleep", "Sleep duration, stages, overnight HR/respiration.",
                 "get_sleep_data", TTL_LIVE, dated="single",
                 series=(SeriesSpec("sleepHeartRate", "records", "startGMT", "value"),
                         SeriesSpec("wellnessEpochRespirationDataDTOList", "records", "startTimeGMT", "respirationValue"),
                         SeriesSpec("sleepStress", "records", "startGMT", "value"),
                         SeriesSpec("sleepBodyBattery", "records", "startGMT", "value"))),
    EndpointSpec("wellness", "hr", "Heart rate through the day.", "get_heart_rates", TTL_LIVE,
                 dated="single", series=(_pairs("heartRateValues"),)),
    EndpointSpec("wellness", "stress", "Stress level and body battery through the day.", "get_stress_data",
                 TTL_LIVE, dated="single", series=(_pairs("stressValuesArray"), _pairs("bodyBatteryValuesArray"))),
    EndpointSpec("wellness", "body-battery", "Body battery (energy) through the day.", "get_body_battery",
                 TTL_LIVE, dated="range", unwrap="first", series=(_pairs("bodyBatteryValuesArray"),)),
    EndpointSpec("wellness", "hrv", "Overnight heart rate variability.", "get_hrv_data", TTL_LIVE,
                 dated="single", series=(SeriesSpec("hrvReadings", "records", "readingTimeGMT", "hrvValue"),)),
    EndpointSpec("wellness", "hydration", "Water intake for the day.", "get_hydration_data", TTL_LIVE, dated="single"),
    EndpointSpec("wellness", "spo2", "Blood oxygen saturation.", "get_spo2_data", TTL_LIVE, dated="single",
                 series=(_pairs("spO2HourlyAverages"),)),
    # training
    EndpointSpec("training", "status", "Training status, load, VO2max.", "get_training_status", TTL_TRAINING, dated="single"),
    EndpointSpec("training", "readiness", "Training readiness score and contributors.", "get_training_readiness",
                 TTL_TRAINING, dated="single", unwrap="first"),
    EndpointSpec("training", "endurance", "Endurance score.", "get_endurance_score", TTL_TRAINING, dated="range"),
    EndpointSpec("training", "fitness-age", "Fitness age.", "get_fitnessage_data", TTL_TRAINING, dated="single"),
    EndpointSpec("training", "hill-score", "Hill score.", "get_hill_score", TTL_TRAINING, dated="range"),
    EndpointSpec("training", "race-predictions", "Predicted race times (5k, 10k, half, marathon).",
                 "get_race_predictions", TTL_TRAINING),
    # device
    EndpointSpec("device", "list", "Registered devices.", "get_devices", TTL_STATIC),
    EndpointSpec("device", "last-used", "Most recently synced device.", "get_device_last_used", TTL_STATIC),
    EndpointSpec("device", "settings", "Settings for one device.", "get_device_settings", TTL_STATIC,
                 id_arg="DEVICE_ID", id_type=str),
    EndpointSpec("device", "alarms", "Configured alarms.", "get_device_alarms", TTL_STATIC),
    EndpointSpec("device", "solar", "Solar charging data for one device.", "get_device_solar_data", TTL_STATIC,
                 dated="range", id_arg="DEVICE_ID", id_type=str),
    # gear
    EndpointSpec("gear", "list", "All gear items.", "get_gear", TTL_STATIC, resolver="upn"),
    EndpointSpec("gear", "stats", "Usage stats for one gear item.", "get_gear_stats", TTL_STATIC,
                 id_arg="GEAR_UUID", id_type=str),
    EndpointSpec("gear", "defaults", "Default gear per activity type.", "get_gear_defaults", TTL_STATIC, resolver="upn"),
    # goal
    EndpointSpec("goal", "list", "Active goals.", "get_goals", TTL_STATIC),
    EndpointSpec("goal", "personal-records", "Personal records.", "get_personal_record", TTL_STATIC),
    # profile
    EndpointSpec("profile", "settings", "User profile settings incl. unit system.", "get_userprofile_settings", TTL_PROFILE),
)


def specs_for(group: str) -> list[EndpointSpec]:
    return [s for s in REGISTRY if s.group == group]
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_spec.py -q` -> 4 passed.
If `test_every_method_exists_on_garmin_client` fails, fix the `method` string; do not remove the test.

- [ ] **Step 5: Commit**

```bash
git add pace/spec.py tests/test_spec.py
git commit -m "feat: endpoint spec registry"
```

---

### Task 7: Command factory

**Files:**
- Create: `pace/factory.py`
- Modify: `pace/envelope.py` (add `fail`), `pace/cache.py` (late-bound DB path)
- Test: `tests/test_factory.py`

**Interfaces:**
- Consumes: `EndpointSpec`, `cached_call`, `CacheTracker`, `ttl_for_day`, `resolve_range`, `date_range`, `shape.apply/parse_fields/parse_bucket`, `envelope.run/Result/PaceError`.
- Produces (`pace.factory`):
  - `FIELDS_OPT`, `BUCKET_OPT`, `RAW_OPT`, `NO_CACHE_OPT`: shared `typer.Option` defaults reused by hand-written commands.
  - `FROM_OPT`, `TO_OPT`, `DAYS_OPT`: shared range options.
  - `MAX_DAYS = 366`.
  - `async resolve_upn(cache, tracker) -> str`.
  - `async fetch_spec(spec, *, ident=None, dates: list[date] | None = None, no_cache=False, fields="default", bucket="auto", raw=False) -> Result`.
  - `build_command(app: typer.Typer, spec: EndpointSpec) -> None`.
  - `build_group(group: str) -> typer.Typer`.
  - `shape_args(fields: str, bucket: str, raw: bool) -> tuple[str | list[str], str, dict]`: parses and returns `(fields, bucket, args_echo)`; raises `PaceError`.
- `pace.envelope.fail(command: str, err: PaceError) -> NoReturn`: emit error envelope, raise `typer.Exit`.
- `pace.cache.Cache(path: Path | None = None)`: `None` resolves `pace.config.CACHE_DB` at call time.

- [ ] **Step 1: Small prerequisite edits**

In `pace/envelope.py` add:

```python
from typing import NoReturn


def fail(command: str, err: PaceError) -> NoReturn:
    emit(error(command, err))
    raise typer.Exit(exit_code(err.code))
```

and make `run` use it in both `except` branches.

In `pace/cache.py`, change `from pace.config import CACHE_DB, ensure_home` to `from pace import config` and:

```python
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.CACHE_DB
        config.ensure_home()
        self._conn = sqlite3.connect(str(self.path))
```

Run `.venv/bin/python -m pytest -q` -> still green.

- [ ] **Step 2: Write failing tests**

`tests/test_factory.py`:

```python
"""Factory-built commands: flags, fan-out, shaping, envelope."""

import json
from datetime import date

import pytest
import typer
from typer.testing import CliRunner

from pace.api import ApiError
from pace.factory import build_command
from pace.shape import SeriesSpec
from pace.spec import EndpointSpec

runner = CliRunner()
MS = 1000


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")
    monkeypatch.setattr("pace.config.CONFIG_FILE", tmp_path / "config.toml")


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
    assert doc["args"] == {"from": "2026-01-01", "to": "2026-01-03", "fields": "default", "bucket": "auto", "raw": False}
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


def test_no_cache_flag_skips_store(fake_api) -> None:
    app = _app(DATED)
    _invoke(app, "--to", "2026-01-03", "--no-cache")
    _invoke(app, "--to", "2026-01-03", "--no-cache")
    assert len(fake_api["calls"]) == 2


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
```

Note: `CliRunner` must separate streams; if `res.stderr` raises, construct `CliRunner(mix_stderr=False)` (typer < 0.13) -- typer 0.25 separates by default.

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_factory.py -q` -> ModuleNotFoundError `pace.factory`.

- [ ] **Step 4: Implement `pace/factory.py`**

```python
"""Build Typer commands from EndpointSpec entries."""

import asyncio
import inspect
from collections.abc import Callable
from datetime import date
from typing import Any

import typer

from pace.cache import Cache, CacheTracker, ttl_for_day
from pace.commands._helpers import cached_call
from pace.config import load_config
from pace.dates import date_range, resolve_range
from pace.envelope import PaceError, Result, fail, run
from pace.shape import apply, parse_bucket, parse_fields
from pace.spec import GROUPS, EndpointSpec, specs_for

MAX_DAYS = 366
DAY_S = 86400

FIELDS_OPT = typer.Option("default", "--fields", help="default | all | comma-separated dotted paths.")
BUCKET_OPT = typer.Option("auto", "--bucket", help="Time-series resolution: auto | none | 1m 5m 15m 1h 3h 1d.")
RAW_OPT = typer.Option(False, "--raw", help="Garmin JSON verbatim (no pruning, fields, bucketing).")
NO_CACHE_OPT = typer.Option(False, "--no-cache", help="Bypass the local cache for this call.")
FROM_OPT = typer.Option(None, "--from", "-F", help="Start date (YYYY-MM-DD | today | yesterday | -Nd). Overrides --days.")
TO_OPT = typer.Option("today", "--to", "-T", help="End date (inclusive).")
DAYS_OPT = typer.Option(1, "--days", "-n", help="Number of days ending at --to.")


def shape_args(fields: str, bucket: str, raw: bool) -> tuple[str | list[str], str, dict[str, Any]]:
    f = parse_fields(fields)
    b = parse_bucket(bucket)
    return f, b, {"fields": f, "bucket": b, "raw": raw}


def today_ttl(spec: EndpointSpec) -> int:
    return load_config().cache_ttl_overrides.get(f"{spec.group}.{spec.name}", spec.ttl)


async def resolve_upn(cache: Cache | None, tracker: CacheTracker) -> str:
    data = await cached_call("get_device_last_used", cache=cache, ttl=604800, tracker=tracker)
    upn = (data or {}).get("userProfileNumber") if isinstance(data, dict) else None
    if upn is None:
        raise PaceError("internal", "Cannot resolve userProfileNumber from the last-used device.")
    return str(upn)


def _unwrap(spec: EndpointSpec, data: Any) -> Any:
    if spec.unwrap == "first" and isinstance(data, list):
        return data[0] if data else {}
    return data


def _shape_kwargs(spec: EndpointSpec, fields, bucket, raw) -> dict[str, Any]:
    return dict(series=spec.series, default_fields=spec.default_fields, fields=fields,
                bucket=bucket, raw=raw, span_s=DAY_S)


async def fetch_spec(spec: EndpointSpec, *, ident: Any = None, dates: list[date] | None = None,
                     no_cache: bool = False, fields: str | list[str] = "default",
                     bucket: str = "auto", raw: bool = False) -> Result:
    cache = None if no_cache else Cache()
    tracker = CacheTracker()
    try:
        prefix: list[Any] = []
        if spec.resolver == "upn":
            prefix.append(await resolve_upn(cache, tracker))
        if spec.id_arg is not None:
            prefix.append(ident)
        ttl = today_ttl(spec)

        if spec.dated is None:
            data = _unwrap(spec, await cached_call(spec.method, *prefix, cache=cache, ttl=ttl, tracker=tracker))
            shaped, report = apply(data, **_shape_kwargs(spec, fields, bucket, raw))
            return Result(shaped, tracker.report(), report)

        assert dates is not None

        async def one(d: date) -> tuple[date, Any, PaceError | None]:
            iso = d.isoformat()
            dargs = (iso,) if spec.dated == "single" else (iso, iso)
            try:
                payload = await cached_call(spec.method, *prefix, *dargs, cache=cache,
                                            ttl=ttl_for_day(d, ttl), tracker=tracker)
            except PaceError as err:
                return d, None, err
            return d, _unwrap(spec, payload), None

        results = await asyncio.gather(*(one(d) for d in dates))
    finally:
        if cache is not None:
            cache.close()

    errors = [e for _, _, e in results if e is not None]
    if errors and len(errors) == len(results):
        raise errors[0]

    good = [p if isinstance(p, dict) else {"value": p} for _, p, e in results if e is None]
    shaped_good, report = apply(good, **_shape_kwargs(spec, fields, bucket, raw))
    shaped_iter = iter(shaped_good)
    items = []
    for d, _, err in results:
        if err is not None:
            items.append({"date": d.isoformat(), "error": {"code": err.code, "message": err.message}})
        else:
            items.append({"date": d.isoformat(), **next(shaped_iter)})
    return Result(items, tracker.report(), report)


def build_command(app: typer.Typer, spec: EndpointSpec) -> None:
    P = inspect.Parameter
    params: list[inspect.Parameter] = []
    if spec.id_arg is not None:
        params.append(P("ident", P.POSITIONAL_OR_KEYWORD, annotation=spec.id_type,
                        default=typer.Argument(..., metavar=spec.id_arg)))
    if spec.dated is not None:
        params += [
            P("from_", P.KEYWORD_ONLY, annotation=str | None, default=FROM_OPT),
            P("to", P.KEYWORD_ONLY, annotation=str, default=TO_OPT),
            P("days", P.KEYWORD_ONLY, annotation=int, default=DAYS_OPT),
        ]
    params += [
        P("fields", P.KEYWORD_ONLY, annotation=str, default=FIELDS_OPT),
        P("bucket", P.KEYWORD_ONLY, annotation=str, default=BUCKET_OPT),
        P("raw", P.KEYWORD_ONLY, annotation=bool, default=RAW_OPT),
        P("no_cache", P.KEYWORD_ONLY, annotation=bool, default=NO_CACHE_OPT),
    ]

    def command(**kw: Any) -> None:
        args: dict[str, Any] = {}
        dates: list[date] | None = None
        try:
            if spec.id_arg is not None:
                args[spec.id_arg.lower()] = kw["ident"]
            if spec.dated is not None:
                start, end = resolve_range(kw["from_"], kw["to"], kw["days"])
                dates = list(date_range(start, end))
                if len(dates) > MAX_DAYS:
                    raise PaceError("bad_args", f"Range too long ({len(dates)} days); max {MAX_DAYS}.")
                args.update(**{"from": start.isoformat(), "to": end.isoformat()})
            fields, bucket, shape_echo = shape_args(kw["fields"], kw["bucket"], kw["raw"])
        except PaceError as err:
            fail(spec.command, err)
        args.update(shape_echo)
        run(spec.command, args, lambda: fetch_spec(
            spec, ident=kw.get("ident"), dates=dates, no_cache=kw["no_cache"],
            fields=fields, bucket=bucket, raw=kw["raw"]))

    command.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    command.__name__ = spec.name.replace("-", "_")
    command.__doc__ = spec.help
    app.command(spec.name, help=spec.help)(command)


def build_group(group: str) -> typer.Typer:
    app = typer.Typer(no_args_is_help=True, help=GROUPS[group])
    for spec in specs_for(group):
        build_command(app, spec)
    return app
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_factory.py -q`
Expected: 11 passed.
Known pitfalls: if Typer rejects `**kw`, replace `command(**kw)` with a wrapper built via `functools.wraps` -- but keep `__signature__`; if `str | None` annotation fails under Typer, use `Optional[str]` from `typing`.

- [ ] **Step 6: Run everything and commit**

Run: `.venv/bin/python -m pytest -q` -> green.

```bash
git add pace/factory.py pace/envelope.py pace/cache.py tests/test_factory.py
git commit -m "feat: spec-driven command factory with range fan-out and envelope"
```

---

### Task 8: Spec-driven groups and `profile show`

**Files:**
- Rewrite: `pace/commands/wellness.py`, `training.py`, `device.py`, `gear.py`, `goal.py`, `profile.py`
- Modify: `pace/cli.py`
- Test: `tests/test_groups.py`

**Interfaces:**
- Consumes: `factory.build_group`, `factory.fetch_spec` pattern, `cached_call`, `Result`, `run`.
- Produces: each module exposes `app: typer.Typer`; `profile show` command (composite of `get_full_name`, `get_user_profile`, `get_unit_system`).

- [ ] **Step 1: Write failing test**

`tests/test_groups.py`:

```python
import json

import pytest
from typer.testing import CliRunner

from pace.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")


@pytest.fixture
def fake_api(monkeypatch):
    calls = []

    async def safe_call(method, *args, **kwargs):
        calls.append((method, args))
        return True, {"method": method, "unitSystem": "metric", "fullName": "A B"}, None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    return calls


@pytest.mark.parametrize("argv", [
    ["wellness", "sleep", "--to", "2026-01-01"],
    ["training", "readiness", "--to", "2026-01-01"],
    ["device", "list"],
    ["goal", "personal-records"],
    ["profile", "settings"],
])
def test_groups_are_wired(fake_api, argv) -> None:
    res = runner.invoke(app, argv)
    assert res.exit_code == 0, res.stdout
    doc = json.loads(res.stdout)
    assert doc["ok"] and doc["command"] == " ".join(argv[:2])


def test_profile_show_is_composite(fake_api) -> None:
    res = runner.invoke(app, ["profile", "show"])
    doc = json.loads(res.stdout)
    assert doc["ok"] and set(doc["data"]) == {"fullName", "profile", "unitSystem"}
    assert {m for m, _ in fake_api} == {"get_full_name", "get_user_profile", "get_unit_system"}


def test_removed_commands_gone() -> None:
    for argv in (["wellness", "trend"], ["wellness", "today"], ["profile", "unit-system"], ["today"], ["browse"]):
        assert runner.invoke(app, argv).exit_code == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_groups.py -q` -> fails (old renderers, `wellness today` still exists).

- [ ] **Step 3: Rewrite the five simple modules**

Each of `wellness.py`, `training.py`, `device.py`, `gear.py`, `goal.py` becomes:

```python
"""<Group> commands, generated from pace.spec.REGISTRY."""

from pace.factory import build_group

app = build_group("<group>")
```

- [ ] **Step 4: Rewrite `profile.py`**

```python
"""Profile commands: registry-driven `settings` plus the composite `show`."""

import asyncio

import typer

from pace.cache import Cache, CacheTracker
from pace.commands._helpers import cached_call
from pace.envelope import PaceError, Result, fail, run
from pace.factory import BUCKET_OPT, FIELDS_OPT, NO_CACHE_OPT, RAW_OPT, build_group, shape_args
from pace.shape import apply

app = build_group("profile")
_TTL = 604800


@app.command("show")
def show(fields: str = FIELDS_OPT, bucket: str = BUCKET_OPT, raw: bool = RAW_OPT,
         no_cache: bool = NO_CACHE_OPT) -> None:
    """Name, profile and unit system in one call."""
    try:
        f, b, echo = shape_args(fields, bucket, raw)
    except PaceError as err:
        fail("profile show", err)

    async def work() -> Result:
        cache = None if no_cache else Cache()
        tracker = CacheTracker()
        try:
            name, profile, units = await asyncio.gather(
                cached_call("get_full_name", cache=cache, ttl=_TTL, tracker=tracker),
                cached_call("get_user_profile", cache=cache, ttl=_TTL, tracker=tracker),
                cached_call("get_unit_system", cache=cache, ttl=_TTL, tracker=tracker),
            )
        finally:
            if cache is not None:
                cache.close()
        data = {"fullName": name, "profile": profile, "unitSystem": units}
        shaped, report = apply(data, series=(), default_fields=(), fields=f, bucket=b, raw=raw, span_s=0)
        return Result(shaped, tracker.report(), report)

    run("profile show", echo, work)
```

- [ ] **Step 5: Wire `cli.py`**

Replace the group imports with `from pace.commands import device, gear, goal, profile, training, wellness` and `app.add_typer(wellness.app, name="wellness")` etc. (help comes from the group Typer).
Keep `activity`, `auth`, `cache`, `config` imports as they are for now.

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest -q` -> green (test_groups 7 passed).

- [ ] **Step 7: Commit**

```bash
git add pace/commands pace/cli.py tests/test_groups.py
git commit -m "feat: registry-driven wellness/training/device/gear/goal/profile groups"
```

---

### Task 9: Activity commands

**Files:**
- Rewrite: `pace/commands/activity.py`
- Test: `tests/test_activity.py`

**Interfaces:**
- Consumes: `factory` options + `shape_args`, `cached_call`, `Cache`, `CacheTracker`, `apply`, `choose_bucket`, `bucket_series`, `extract_points`, `Result`, `run`, `fail`, `safe_call`, `paginate`, `resolve_range`.
- Produces: `pace.commands.activity.app`; commands `list`, `show`, `splits`, `details`, `weather`, `last`, `export`, `export-range`; helper `details_table(payload: dict, bucket: str) -> tuple[dict, str]` (table, bucket_used).
- Constants: `LIST_FIELDS` (curated default fields for list rows), `DETAIL_METRICS: dict[str, str]` mapping Garmin metric key -> column name.

- [ ] **Step 1: Write failing tests**

`tests/test_activity.py`:

```python
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
    assert used == "5m"                                            # 600 s / 120 rows -> 5 s < ladder; smallest with rows<=120 is 1m -> 10 rows; expect "1m"
```

Correct the last assertion before running: 600 s span, 600 samples > 120 -> ladder: `1m` gives 10 rows -> `used == "1m"`, 10 rows.
Replace the line with:

```python
    assert used == "1m" and len(table["rows"]) == 10
    assert table["cols"] == ["t", "hr", "speed_mps", "dist_m", "elev_m"]
    assert table["rows"][0][3] == 177.0   # dist_m is the last sample in the bucket (59 * 3)
    assert table["rows"][0][1] == 142.0   # hr avg of 140..144 pattern


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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_activity.py -q` -> ImportError `details_table`.

- [ ] **Step 3: Rewrite `pace/commands/activity.py`**

```python
"""Activity commands: list, show, splits, details, weather, last, export."""

import asyncio
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

import typer

from garminconnect import Garmin
from pace.api import paginate, safe_call
from pace.cache import Cache, CacheTracker
from pace.commands._helpers import cached_call
from pace.dates import resolve_range
from pace.envelope import PaceError, Result, fail, run
from pace.factory import (BUCKET_OPT, DAYS_OPT, FIELDS_OPT, FROM_OPT, NO_CACHE_OPT, RAW_OPT, TO_OPT,
                          shape_args)
from pace.shape import BUCKETS, COLS, apply, choose_bucket, prune

app = typer.Typer(no_args_is_help=True, help="Workouts and activity history.")

_LIST_TTL = 600
_DETAIL_TTL = 86400

LIST_FIELDS: tuple[str, ...] = (
    "activityId", "activityName", "activityType", "startTimeLocal", "duration", "distance",
    "averageHR", "maxHR", "elevationGain", "averageSpeed", "aerobicTrainingEffect",
    "anaerobicTrainingEffect", "activityTrainingLoad", "calories",
)

# Garmin per-sample metric key -> column name in `activity details`.
DETAIL_METRICS: dict[str, str] = {
    "directHeartRate": "hr",
    "directSpeed": "speed_mps",
    "sumDistance": "dist_m",
    "directElevation": "elev_m",
    "directRunCadence": "cadence",
    "directBikeCadence": "cadence",
    "directPower": "power_w",
}
_LAST_VALUE_COLS = {"dist_m"}


def _flatten_type(a: dict) -> dict:
    out = dict(a)
    if isinstance(a.get("activityType"), dict):
        out["activityType"] = a["activityType"].get("typeKey")
    return out


def _shaped_result(data: Any, tracker: CacheTracker, *, default_fields=(), fields, bucket, raw,
                   truncated=False) -> Result:
    shaped, report = apply(data, series=(), default_fields=default_fields, fields=fields, bucket=bucket,
                           raw=raw, span_s=0)
    return Result(shaped, tracker.report(), report, truncated)


def _open(no_cache: bool) -> Cache | None:
    return None if no_cache else Cache()


@app.command("list")
def list_(
    from_: str | None = FROM_OPT, to: str = TO_OPT, days: int | None = typer.Option(None, "--days", "-n"),
    type_: str | None = typer.Option(None, "--type", "-t", help="running, cycling, swimming, ..."),
    limit: int = typer.Option(20, "--limit", "-l", help="Max activities returned (newest first)."),
    fields: str = FIELDS_OPT, bucket: str = BUCKET_OPT, raw: bool = RAW_OPT, no_cache: bool = NO_CACHE_OPT,
) -> None:
    """Recent activities, or every activity in a date range."""
    cmd = "activity list"
    ranged = from_ is not None or days is not None or to != "today"
    args: dict[str, Any] = {"limit": limit, "type": type_}
    try:
        f, b, echo = shape_args(fields, bucket, raw)
        if ranged:
            start, end = resolve_range(from_, to, days or 1)
            args.update({"from": start.isoformat(), "to": end.isoformat()})
    except PaceError as err:
        fail(cmd, err)
    args.update(echo)

    async def work() -> Result:
        cache = _open(no_cache)
        tracker = CacheTracker()
        try:
            if ranged:
                rows = await cached_call("get_activities_by_date", args["from"], args["to"], type_,
                                         cache=cache, ttl=_LIST_TTL, tracker=tracker)
            else:
                async def fetch_page() -> list:
                    return await paginate("get_activities", page_size=min(limit + 1, 1000),
                                          max_items=limit + 1, **({"activitytype": type_} if type_ else {}))
                if cache is None:
                    rows = await fetch_page()
                    tracker.add(False, 0)
                else:
                    rows, hit, age = await cache.get_or_fetch(f"activities:{limit}:{type_}", fetch_page, ttl=_LIST_TTL)
                    tracker.add(hit, age)
        finally:
            if cache is not None:
                cache.close()
        rows = rows or []
        truncated = len(rows) > limit
        rows = [_flatten_type(a) for a in rows[:limit]]
        return _shaped_result(rows, tracker, default_fields=LIST_FIELDS, fields=f, bucket=b, raw=raw,
                              truncated=truncated)

    run(cmd, args, work)


def _by_id(name: str, method: str, help_: str) -> None:
    def command(activity_id: int = typer.Argument(..., metavar="ACTIVITY_ID"),
                fields: str = FIELDS_OPT, bucket: str = BUCKET_OPT, raw: bool = RAW_OPT,
                no_cache: bool = NO_CACHE_OPT) -> None:
        cmd = f"activity {name}"
        try:
            f, b, echo = shape_args(fields, bucket, raw)
        except PaceError as err:
            fail(cmd, err)

        async def work() -> Result:
            cache = _open(no_cache)
            tracker = CacheTracker()
            try:
                data = await cached_call(method, activity_id, cache=cache, ttl=_DETAIL_TTL, tracker=tracker)
            finally:
                if cache is not None:
                    cache.close()
            return _shaped_result(data, tracker, fields=f, bucket=b, raw=raw)

        run(cmd, {"activity_id": activity_id, **echo}, work)

    command.__name__ = name
    command.__doc__ = help_
    app.command(name, help=help_)(command)


_by_id("show", "get_activity", "One activity: summary metrics.")
_by_id("splits", "get_activity_splits", "Laps / splits of one activity.")
_by_id("weather", "get_activity_weather", "Weather during one activity.")


@app.command("last")
def last(fields: str = FIELDS_OPT, bucket: str = BUCKET_OPT, raw: bool = RAW_OPT,
         no_cache: bool = NO_CACHE_OPT) -> None:
    """Most recent activity."""
    try:
        f, b, echo = shape_args(fields, bucket, raw)
    except PaceError as err:
        fail("activity last", err)

    async def work() -> Result:
        cache = _open(no_cache)
        tracker = CacheTracker()
        try:
            data = await cached_call("get_last_activity", cache=cache, ttl=_LIST_TTL, tracker=tracker)
        finally:
            if cache is not None:
                cache.close()
        return _shaped_result(_flatten_type(data or {}), tracker, fields=f, bucket=b, raw=raw)

    run("activity last", echo, work)


def details_table(payload: dict, bucket: str) -> tuple[dict, str]:
    """Reshape Garmin per-sample metrics into one bucketed table. Returns (table, bucket_used)."""
    from datetime import datetime, timezone

    from pace.shape import tz_offset_s

    index = {d.get("key"): d.get("metricsIndex") for d in payload.get("metricDescriptors", [])}
    if "directTimestamp" not in index:
        raise PaceError("not_found", "Activity has no per-sample metrics.")
    cols_present = [(k, c) for k, c in DETAIL_METRICS.items() if k in index and c not in
                    [c2 for k2, c2 in DETAIL_METRICS.items() if k2 in index and k2 < k and c2 == c]]
    samples = []
    for row in payload.get("activityDetailMetrics", []):
        m = row.get("metrics") or []
        ts = m[index["directTimestamp"]] if index["directTimestamp"] < len(m) else None
        if not isinstance(ts, (int, float)):
            continue
        samples.append((int(ts), [m[index[k]] if index[k] < len(m) else None for k, _ in cols_present]))
    samples.sort()
    span_s = (samples[-1][0] - samples[0][0]) // 1000 if len(samples) > 1 else 0
    used = choose_bucket(span_s, len(samples)) if bucket == "auto" else bucket
    offset = tz_offset_s(payload)
    names = [c for _, c in cols_present]

    def fmt(sec: int) -> str:
        return datetime.fromtimestamp(sec + offset, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    if used == "none":
        rows = [[fmt(ts // 1000), *vals] for ts, vals in samples]
        return {"cols": ["t", *names], "rows": rows}, "none"

    size = BUCKETS[used]
    groups: dict[int, list[list]] = {}
    for ts, vals in samples:
        groups.setdefault((ts // 1000 // size) * size, []).append(vals)
    rows = []
    for start in sorted(groups):
        agg = []
        for i, name in enumerate(names):
            col = [v[i] for v in groups[start] if isinstance(v[i], (int, float))]
            if not col:
                agg.append(None)
            elif name in _LAST_VALUE_COLS:
                agg.append(col[-1])
            else:
                agg.append(round(sum(col) / len(col), 1))
        rows.append([fmt(start), *agg])
    return {"cols": ["t", *names], "rows": rows}, used


@app.command("details")
def details(activity_id: int = typer.Argument(..., metavar="ACTIVITY_ID"),
            bucket: str = BUCKET_OPT, raw: bool = RAW_OPT, no_cache: bool = NO_CACHE_OPT) -> None:
    """Per-sample HR / speed / distance / elevation / cadence / power as one bucketed table."""
    cmd = "activity details"
    try:
        _, b, echo = shape_args("all", bucket, raw)
    except PaceError as err:
        fail(cmd, err)

    async def work() -> Result:
        cache = _open(no_cache)
        tracker = CacheTracker()
        try:
            payload = await cached_call("get_activity_details", activity_id, cache=cache, ttl=_DETAIL_TTL,
                                        tracker=tracker)
        finally:
            if cache is not None:
                cache.close()
        if raw:
            return Result(payload, tracker.report(), {"raw": True})
        table, used = details_table(payload or {}, b)
        summary = prune({k: payload.get(k) for k in ("activityId", "measurementCount", "totalMetricsCount")})
        return Result({**summary, "table": table}, tracker.report(),
                      {"bucket_used": used, "reduced_series": ["table"] if used != "none" else [], "fields": "all"})

    run(cmd, {"activity_id": activity_id, **echo}, work)


class ExportFormat(str, Enum):
    GPX = "gpx"
    TCX = "tcx"
    KML = "kml"
    ORIGINAL = "original"


_DL_FMT_MAP = {
    ExportFormat.GPX: Garmin.ActivityDownloadFormat.GPX,
    ExportFormat.TCX: Garmin.ActivityDownloadFormat.TCX,
    ExportFormat.KML: Garmin.ActivityDownloadFormat.KML,
    ExportFormat.ORIGINAL: Garmin.ActivityDownloadFormat.ORIGINAL,
}


def _ext_for(fmt: ExportFormat) -> str:
    return ".zip" if fmt is ExportFormat.ORIGINAL else f".{fmt.value}"


async def _download(activity_id: int, fmt: ExportFormat, target: Path) -> dict[str, Any]:
    ok, data, err = await safe_call("download_activity", str(activity_id), _DL_FMT_MAP[fmt])
    if not ok:
        assert err is not None
        raise PaceError.from_api(err)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"activityId": activity_id, "path": str(target), "bytes": len(data)}


@app.command("export")
def export(activity_id: int = typer.Argument(..., metavar="ACTIVITY_ID"),
           fmt: ExportFormat = typer.Option(ExportFormat.GPX, "--format", "-f"),
           out: Path | None = typer.Option(None, "--out", "-o", help="Default: <id>.<ext> in cwd.")) -> None:
    """Download one activity file (gpx, tcx, kml, original zip)."""
    target = out or Path(f"{activity_id}{_ext_for(fmt)}")

    async def work() -> Result:
        return Result({"files": [await _download(activity_id, fmt, target)]})

    run("activity export", {"activity_id": activity_id, "format": fmt.value, "out": str(target)}, work)


@app.command("export-range")
def export_range(from_: str = typer.Option(..., "--from", "-F"), to: str = TO_OPT,
                 fmt: ExportFormat = typer.Option(ExportFormat.GPX, "--format", "-f"),
                 out_dir: Path = typer.Option(Path("./activities"), "--out-dir", "-d"),
                 type_: str | None = typer.Option(None, "--type", "-t")) -> None:
    """Download every activity in a date range into --out-dir."""
    cmd = "activity export-range"
    try:
        start, end = resolve_range(from_, to, 1)
    except PaceError as err:
        fail(cmd, err)
    args = {"from": start.isoformat(), "to": end.isoformat(), "format": fmt.value, "out_dir": str(out_dir), "type": type_}

    async def work() -> Result:
        ok, listing, err = await safe_call("get_activities_by_date", args["from"], args["to"], type_)
        if not ok:
            assert err is not None
            raise PaceError.from_api(err)
        files = await asyncio.gather(*(
            _download(a["activityId"], fmt, out_dir / f"{a['activityId']}{_ext_for(fmt)}") for a in listing or []))
        return Result({"files": list(files)})

    run(cmd, args, work)
```

Simplify `cols_present` if the duplicate-column guard reads badly: keep the first metric key that maps to each column name, in `DETAIL_METRICS` order.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_activity.py -q` -> 11 passed.
Then `.venv/bin/python -m pytest -q` -> green.

- [ ] **Step 5: Commit**

```bash
git add pace/commands/activity.py tests/test_activity.py
git commit -m "feat: envelope-based activity commands with bucketed details table"
```

---

### Task 10: auth, cache, config commands and root CLI

**Files:**
- Rewrite: `pace/commands/auth.py`, `pace/commands/cache.py`, `pace/commands/config.py`, `pace/cli.py`
- Modify: `pace/__main__.py`, `pyproject.toml` (`[project.scripts] pace = "pace.cli:main"`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `pace.cli.app`, `pace.cli.main()` -- invokes `app(standalone_mode=False)`, converts `click.UsageError` into a `bad_args` envelope (exit 2), `--verbose/-v` root flag calling `envelope.set_verbose`.
- `auth status` data: `{"logged_in": bool, "token_store": str}`; `auth login` stays interactive (prints human text, exits 0/1) and is documented as human-only; `auth logout` data `{"removed": bool}`.
- `cache stats` data `{"path", "entries", "size_bytes"}`; `cache clear` data `{"removed": int}`; `cache path` data `{"path"}`.
- `config show` data = config dict; `config path` data `{"path"}`.

- [ ] **Step 1: Write failing tests**

`tests/test_cli.py`:

```python
import json

import pytest
from typer.testing import CliRunner

from pace.cli import app, main

runner = CliRunner()


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")
    monkeypatch.setattr("pace.config.CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr("pace.config.TOKEN_STORE", tmp_path / "tokens")
    monkeypatch.setattr("pace.auth.TOKEN_STORE", tmp_path / "tokens")


def test_usage_error_is_bad_args_envelope(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["pace", "wellness", "sleep", "--nope"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["error"]["code"] == "bad_args" and "--nope" in doc["error"]["message"]


def test_unknown_command_is_bad_args(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["pace", "frobnicate"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2 and json.loads(capsys.readouterr().out)["error"]["code"] == "bad_args"


def test_auth_status_not_logged_in() -> None:
    doc = json.loads(runner.invoke(app, ["auth", "status"]).stdout)
    assert doc["ok"] and doc["data"]["logged_in"] is False


def test_auth_logout_reports_removed(tmp_path) -> None:
    (tmp_path / "tokens").mkdir()
    doc = json.loads(runner.invoke(app, ["auth", "logout"]).stdout)
    assert doc["data"] == {"removed": True}
    doc = json.loads(runner.invoke(app, ["auth", "logout"]).stdout)
    assert doc["data"] == {"removed": False}


def test_cache_commands(tmp_path) -> None:
    doc = json.loads(runner.invoke(app, ["cache", "stats"]).stdout)
    assert doc["data"]["entries"] == 0 and doc["data"]["path"] == str(tmp_path / "cache.db")
    doc = json.loads(runner.invoke(app, ["cache", "clear"]).stdout)
    assert doc["data"] == {"removed": 0}
    doc = json.loads(runner.invoke(app, ["cache", "path"]).stdout)
    assert doc["data"] == {"path": str(tmp_path / "cache.db")}


def test_config_show_and_path(tmp_path) -> None:
    (tmp_path / "config.toml").write_text('[cache_ttl_overrides]\n"wellness.sleep" = 5\n')
    from pace.config import load_config
    load_config.cache_clear()
    doc = json.loads(runner.invoke(app, ["config", "show"]).stdout)
    assert doc["data"] == {"cache_ttl_overrides": {"wellness.sleep": 5}}
    doc = json.loads(runner.invoke(app, ["config", "path"]).stdout)
    assert doc["data"]["path"].endswith("config.toml")


def test_version_flag() -> None:
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0 and json.loads(res.stdout)["data"]["version"] == "2.0.0"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q` -> ImportError `main` / render errors.

- [ ] **Step 3: Rewrite `auth.py`**

```python
"""Auth commands: status, login (interactive, human-only), logout."""

import typer

from pace import auth as auth_mod
from pace.envelope import Result, run

app = typer.Typer(no_args_is_help=True, help="Authentication and token management.")


@app.command("status")
def status() -> None:
    """Whether cached Garmin tokens exist."""
    async def work() -> Result:
        s = auth_mod.auth_status()
        return Result({"logged_in": bool(s["authenticated"]), "token_store": str(s["token_path"])})

    run("auth status", {}, work)


@app.command("login")
def login() -> None:
    """Interactive login with MFA. Meant to be run by a human in a terminal, not by an agent."""
    client = auth_mod.init_api()
    if client is None:
        typer.echo("Login failed.", err=True)
        raise typer.Exit(1)
    typer.echo("Logged in. Tokens stored at " + str(auth_mod.TOKEN_STORE))


@app.command("logout")
def logout() -> None:
    """Delete cached tokens."""
    async def work() -> Result:
        return Result({"removed": auth_mod.logout()})

    run("auth logout", {}, work)
```

`pace/auth.py`: `auth_status` and `logout` read `TOKEN_STORE` at module import; change them to read `pace.config.TOKEN_STORE` via `from pace import config` (`config.TOKEN_STORE`) so tests can monkeypatch it; keep the module-level name `TOKEN_STORE` as an alias for `init_api`.

- [ ] **Step 4: Rewrite `cache.py` and `config.py` commands**

```python
"""Cache commands: stats, clear, path."""

import typer

from pace import config
from pace.cache import Cache
from pace.envelope import Result, run

app = typer.Typer(no_args_is_help=True, help="Manage the local response cache.")


@app.command("stats")
def stats() -> None:
    """Entry count and size."""
    async def work() -> Result:
        c = Cache()
        try:
            s = c.stats()
        finally:
            c.close()
        return Result({"path": str(config.CACHE_DB), **s})

    run("cache stats", {}, work)


@app.command("clear")
def clear() -> None:
    """Delete all cached responses."""
    async def work() -> Result:
        c = Cache()
        try:
            return Result({"removed": c.clear()})
        finally:
            c.close()

    run("cache clear", {}, work)


@app.command("path")
def path() -> None:
    """Cache database path."""
    async def work() -> Result:
        return Result({"path": str(config.CACHE_DB)})

    run("cache path", {}, work)
```

```python
"""Config commands: show, path."""

import typer

from pace import config
from pace.envelope import Result, run

app = typer.Typer(no_args_is_help=True, help="Inspect the user configuration.")


@app.command("show")
def show() -> None:
    """Effective configuration."""
    async def work() -> Result:
        return Result(config.load_config().model_dump())

    run("config show", {}, work)


@app.command("path")
def path() -> None:
    """Config file path."""
    async def work() -> Result:
        return Result({"path": str(config.CONFIG_FILE)})

    run("config path", {}, work)
```

In `pace/config.py` set `model_config = ConfigDict(extra="forbid")` on `PaceConfig` is NOT required; keep `extra="allow"` but `model_dump()` must only contain `cache_ttl_overrides` for the test -- so remove the two deleted fields (done in Task 1) and keep `extra="allow"`.

- [ ] **Step 5: Rewrite `cli.py` and entry points**

```python
"""Pace CLI entry point."""

import sys

import click
import typer

from pace import __version__, envelope
from pace.commands import activity, auth, cache, config, device, gear, goal, profile, training, wellness
from pace.plugins import load_plugins

app = typer.Typer(
    name="pace",
    help="Machine-first CLI for Garmin Connect data. Every call prints one JSON envelope.",
    no_args_is_help=True,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
)

for module, name in (
    (activity, "activity"), (auth, "auth"), (cache, "cache"), (config, "config"), (device, "device"),
    (gear, "gear"), (goal, "goal"), (profile, "profile"), (training, "training"), (wellness, "wellness"),
):
    app.add_typer(module.app, name=name)
load_plugins(app)


def _version(value: bool) -> None:
    if value:
        envelope.emit(envelope.ok("version", {}, envelope.Result({"version": __version__})))
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(False, "--version", "-V", callback=_version, is_eager=True),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Log fetch/cache events to stderr."),
) -> None:
    envelope.set_verbose(verbose)


def main() -> None:
    """Console entry point: usage errors become bad_args envelopes."""
    try:
        app(standalone_mode=False)
    except click.UsageError as err:
        cmd = " ".join(sys.argv[1:2])
        envelope.emit(envelope.error(cmd, envelope.PaceError("bad_args", err.format_message())))
        sys.exit(2)
    except click.exceptions.Exit as err:
        sys.exit(err.exit_code)
```

`pace/__main__.py`: `from pace.cli import main; if __name__ == "__main__": main()`.
`pyproject.toml`: `pace = "pace.cli:main"`.
Re-run `bash -ic 'update_aws_token && uv sync'` so the `pace` script points at `main`.

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest -q` -> green.
Run: `.venv/bin/pace --version` -> `{"ok":true,"command":"version",...}`.
Run: `.venv/bin/pace wellness sleep --bogus; echo "exit=$?"` -> bad_args envelope, `exit=2`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: envelope-wrapped auth/cache/config, root CLI with usage-error interception"
```

---

### Task 11: `pace schema`

**Files:**
- Create: `pace/commands/schema.py`
- Modify: `pace/cli.py` (add group)
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `pace.commands.schema.schema` (function registered at root as the `schema` command), `build_schema(group: str | None) -> dict`, `STATIC_COMMANDS: list[dict]` for hand-written commands, `UNITS`, `GLOBAL_FLAGS`.
- Data shape:

```json
{"version": "2.0.0",
 "units": {...}, "exit_codes": {...}, "error_codes": [...], "global_flags": {...},
 "commands": [{"name": "wellness sleep", "help": "...", "keyed": "date"|"id"|"id+date"|"none",
               "args": ["DEVICE_ID"], "flags": ["--from", "--to", "--days", "--fields", "--bucket", "--raw", "--no-cache"],
               "default_fields": [...], "series": [...], "ttl_s": 300}]}
```

- [ ] **Step 1: Write failing tests**

`tests/test_schema.py`:

```python
import json

import click
from typer.main import get_command
from typer.testing import CliRunner

from pace.cli import app
from pace.commands.schema import build_schema

runner = CliRunner()

EXEMPT = {"schema", "auth login"}  # login is human-only; schema describes itself


def _walk(cmd: click.Command, prefix: str = "") -> set[str]:
    if isinstance(cmd, click.Group):
        out = set()
        for name, sub in cmd.commands.items():
            out |= _walk(sub, f"{prefix}{name} ".strip() + " ")
        return out
    return {prefix.strip()}


def test_every_cli_command_is_in_schema() -> None:
    cli_cmds = _walk(get_command(app)) - EXEMPT
    schema_cmds = {c["name"] for c in build_schema(None)["commands"]}
    assert cli_cmds <= schema_cmds, cli_cmds - schema_cmds
    assert schema_cmds <= cli_cmds | EXEMPT, schema_cmds - cli_cmds


def test_schema_command_output_and_group_filter() -> None:
    res = runner.invoke(app, ["schema"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 0 and doc["ok"] and doc["data"]["version"] == "2.0.0"
    assert doc["data"]["units"]["distance"] == "m"
    assert "bad_args" in doc["data"]["error_codes"]
    res = runner.invoke(app, ["schema", "wellness"])
    names = [c["name"] for c in json.loads(res.stdout)["data"]["commands"]]
    assert names and all(n.startswith("wellness ") for n in names)


def test_dated_entries_expose_range_flags() -> None:
    sleep = next(c for c in build_schema(None)["commands"] if c["name"] == "wellness sleep")
    assert sleep["keyed"] == "date" and {"--from", "--to", "--days"} <= set(sleep["flags"])
    solar = next(c for c in build_schema(None)["commands"] if c["name"] == "device solar")
    assert solar["keyed"] == "id+date" and solar["args"] == ["DEVICE_ID"]


def test_unknown_group_is_bad_args() -> None:
    res = runner.invoke(app, ["schema", "nope"])
    assert res.exit_code == 2 and json.loads(res.stdout)["error"]["code"] == "bad_args"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_schema.py -q` -> ModuleNotFoundError.

- [ ] **Step 3: Implement `pace/commands/schema.py`**

```python
"""`pace schema`: machine-readable manifest of every command."""

from typing import Any

import typer

from pace import __version__
from pace.envelope import EXIT_CODES, HINTS, PaceError, Result, fail, run
from pace.spec import GROUPS, REGISTRY, EndpointSpec

UNITS = {
    "distance": "m", "elevation": "m", "duration": "s", "speed": "m/s", "heart_rate": "bpm",
    "timestamps": "epoch ms GMT; fields ending in Local are ISO local time",
    "note": "Garmin returns SI regardless of the user's display preferences.",
}

SHAPE_FLAGS = ["--fields", "--bucket", "--raw", "--no-cache"]
RANGE_FLAGS = ["--from", "--to", "--days"]

GLOBAL_FLAGS = {
    "--fields": "default | all | comma-separated dotted paths",
    "--bucket": "auto | none | 1m 5m 15m 1h 3h 1d (time series only; auto targets <=120 rows)",
    "--raw": "Garmin JSON verbatim inside the envelope",
    "--no-cache": "bypass the local cache for this call",
    "--verbose": "(root) log fetch/cache events to stderr",
    "--from/--to/--days": "date-keyed commands; --from overrides --days; dates: YYYY-MM-DD | today | yesterday | -Nd",
}

STATIC_COMMANDS: list[dict[str, Any]] = [
    {"name": "activity list", "help": "Recent activities, or every activity in a date range.", "keyed": "date",
     "args": [], "flags": RANGE_FLAGS + ["--type", "--limit"] + SHAPE_FLAGS,
     "default_fields": ["activityId", "activityName", "activityType", "startTimeLocal", "duration", "distance",
                        "averageHR", "maxHR", "elevationGain", "averageSpeed", "aerobicTrainingEffect",
                        "anaerobicTrainingEffect", "activityTrainingLoad", "calories"],
     "series": [], "ttl_s": 600},
    {"name": "activity show", "help": "One activity: summary metrics.", "keyed": "id", "args": ["ACTIVITY_ID"],
     "flags": SHAPE_FLAGS, "default_fields": [], "series": [], "ttl_s": 86400},
    {"name": "activity splits", "help": "Laps / splits of one activity.", "keyed": "id", "args": ["ACTIVITY_ID"],
     "flags": SHAPE_FLAGS, "default_fields": [], "series": [], "ttl_s": 86400},
    {"name": "activity details", "help": "Per-sample metrics as one bucketed table (cols: t, hr, speed_mps, dist_m, elev_m, cadence, power_w).",
     "keyed": "id", "args": ["ACTIVITY_ID"], "flags": ["--bucket", "--raw", "--no-cache"], "default_fields": [],
     "series": ["table"], "ttl_s": 86400},
    {"name": "activity weather", "help": "Weather during one activity.", "keyed": "id", "args": ["ACTIVITY_ID"],
     "flags": SHAPE_FLAGS, "default_fields": [], "series": [], "ttl_s": 86400},
    {"name": "activity last", "help": "Most recent activity.", "keyed": "none", "args": [], "flags": SHAPE_FLAGS,
     "default_fields": [], "series": [], "ttl_s": 600},
    {"name": "activity export", "help": "Download one activity file; data.files lists written paths.", "keyed": "id",
     "args": ["ACTIVITY_ID"], "flags": ["--format", "--out"], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "activity export-range", "help": "Download every activity in a range into --out-dir.", "keyed": "date",
     "args": [], "flags": ["--from", "--to", "--format", "--out-dir", "--type"], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "profile show", "help": "Name, profile and unit system.", "keyed": "none", "args": [], "flags": SHAPE_FLAGS,
     "default_fields": [], "series": [], "ttl_s": 604800},
    {"name": "auth status", "help": "Whether tokens exist. If false, the USER must run `pace auth login`.", "keyed": "none",
     "args": [], "flags": [], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "auth logout", "help": "Delete cached tokens.", "keyed": "none", "args": [], "flags": [], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "cache stats", "help": "Cache entry count and size.", "keyed": "none", "args": [], "flags": [], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "cache clear", "help": "Delete all cached responses.", "keyed": "none", "args": [], "flags": [], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "cache path", "help": "Cache database path.", "keyed": "none", "args": [], "flags": [], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "config show", "help": "Effective configuration.", "keyed": "none", "args": [], "flags": [], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "config path", "help": "Config file path.", "keyed": "none", "args": [], "flags": [], "default_fields": [], "series": [], "ttl_s": 0},
]


def _keyed(spec: EndpointSpec) -> str:
    if spec.id_arg and spec.dated:
        return "id+date"
    if spec.id_arg:
        return "id"
    if spec.dated:
        return "date"
    return "none"


def _entry(spec: EndpointSpec) -> dict[str, Any]:
    return {
        "name": spec.command, "help": spec.help, "keyed": _keyed(spec),
        "args": [spec.id_arg] if spec.id_arg else [],
        "flags": (RANGE_FLAGS if spec.dated else []) + SHAPE_FLAGS,
        "default_fields": list(spec.default_fields),
        "series": [s.path for s in spec.series],
        "ttl_s": spec.ttl,
    }


def build_schema(group: str | None) -> dict[str, Any]:
    commands = [_entry(s) for s in REGISTRY] + STATIC_COMMANDS
    if group is not None:
        commands = [c for c in commands if c["name"].split(" ")[0] == group]
    commands.sort(key=lambda c: c["name"])
    return {
        "version": __version__,
        "units": UNITS,
        "exit_codes": {"0": "ok", "1": "api or internal error", **{str(v): k for k, v in EXIT_CODES.items()}},
        "error_codes": list(HINTS),
        "global_flags": GLOBAL_FLAGS,
        "groups": {**GROUPS, "activity": "Workouts and activity history.", "auth": "Authentication.",
                   "cache": "Local cache.", "config": "User configuration."},
        "commands": commands,
    }


def schema(group: str | None = typer.Argument(None, help="Restrict to one group.")) -> None:
    """Machine-readable manifest of every command, flag, unit and error code."""
    known = set(GROUPS) | {"activity", "auth", "cache", "config"}
    if group is not None and group not in known:
        fail("schema", PaceError("bad_args", f"Unknown group {group!r}. Groups: {', '.join(sorted(known))}."))

    async def work() -> Result:
        return Result(build_schema(group))

    run("schema", {"group": group}, work)
```

In `cli.py`: `from pace.commands import schema` and `app.command("schema", help="Machine-readable command manifest.")(schema.schema)`.
Register it as a plain root command (not `add_typer`): a sub-Typer with only a callback would treat `pace schema nope` as a missing subcommand and print click's usage text instead of the `bad_args` envelope.
Drop the `app = typer.Typer()` line and the `@app.callback(...)` decorator from `schema.py`; `schema` is a plain function with the `group` Typer argument.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_schema.py -q` -> 4 passed.
`test_every_cli_command_is_in_schema` is the drift guard: when it fails, fix `STATIC_COMMANDS` or `REGISTRY`, never the test.

- [ ] **Step 5: Commit**

```bash
git add pace/commands/schema.py pace/cli.py tests/test_schema.py
git commit -m "feat: pace schema manifest with drift test"
```

---

### Task 12: Teardown pass 2 (render, models, helpers)

**Files:**
- Delete: `pace/render/`, `pace/models/`, `tests/test_flatten.py`
- Modify: `pace/commands/_helpers.py` (remove `run`, `cache_for`, render imports), `docs/PLUGINS.md`

- [ ] **Step 1: Delete**

```bash
git rm -r -q pace/render pace/models tests/test_flatten.py
grep -rn "pace.render\|pace.models\|cache_for\|_helpers import run" pace tests
```

Fix every hit: `_helpers.py` keeps only `cached_call` (imports: `Any`, `safe_call`, `Cache`, `CacheTracker`, `PaceError`, `log`).

- [ ] **Step 2: Update `docs/PLUGINS.md`**

Replace the "Reuse pace's infrastructure" list with:

```markdown
   - `pace.api.safe_call(method, ...)` -- rate-limited Garmin client; returns `(ok, data, ApiError | None)`.
   - `pace.commands._helpers.cached_call(method, *args, cache=, ttl=, tracker=)` -- cached call raising `PaceError`.
   - `pace.envelope.run(command, args, work)` -- run an async `work() -> Result` and print the envelope.
   - `pace.shape.apply(...)` -- prune, field selection, series bucketing.
   - `pace.factory.FIELDS_OPT / BUCKET_OPT / RAW_OPT / NO_CACHE_OPT / FROM_OPT / TO_OPT / DAYS_OPT` -- the standard flags.
   - `pace.factory.build_command(app, EndpointSpec(...))` -- declare an endpoint instead of writing a command.
```

Add one sentence: plugins must print nothing to stdout except through `pace.envelope`.

- [ ] **Step 3: Verify and commit**

Run: `.venv/bin/python -m pytest -q` -> green. `grep -rn "rich\|Format\b" pace` -> no hits (Typer's own rich usage excluded since `rich_markup_mode=None`).

```bash
git add -A
git commit -m "refactor: remove renderers and pydantic response models"
```

---

### Task 13: Record fixtures and curate default fields

**Files:**
- Create: `scripts/record_fixtures.py`, `tests/fixtures/<group>_<name>.json`, `tests/test_fixtures.py`
- Modify: `pace/spec.py` (`default_fields`), `pace/commands/activity.py` (`LIST_FIELDS` if needed), `pace/commands/schema.py` (`STATIC_COMMANDS` default_fields for `activity list` must equal `LIST_FIELDS` -- import it instead of duplicating)

**Human step required:** the operator runs `.venv/bin/pace auth login` in their own terminal first (interactive MFA).
If tokens are absent, stop this task and ask; do not attempt login from the agent.

- [ ] **Step 1: Write the recorder**

`scripts/record_fixtures.py`:

```python
"""Record one --raw payload per read command into tests/fixtures/, sanitized.

Usage: .venv/bin/python scripts/record_fixtures.py [--date YYYY-MM-DD] [--activity-id N]
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
PACE = [sys.executable, "-m", "pace"]

REDACT_KEYS = {"userProfileNumber", "userProfilePk", "userProfileId", "displayName", "fullName", "firstName",
               "lastName", "emailAddress", "profileImageUrlLarge", "profileImageUrlMedium", "profileImageUrlSmall",
               "userName", "ownerDisplayName", "ownerFullName", "ownerProfileImageUrlLarge", "ownerProfileImageUrlMedium",
               "ownerProfileImageUrlSmall", "deviceId", "unitId", "serialNumber", "uuid", "gearUuid", "locationName",
               "startLatitude", "startLongitude", "endLatitude", "endLongitude"}


def sanitize(obj):
    if isinstance(obj, dict):
        return {k: ("REDACTED" if k in REDACT_KEYS and v is not None else sanitize(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, str) and re.match(r"^[\w.+-]+@[\w-]+\.[\w.]+$", obj):
        return "REDACTED"
    return obj


def record(name: str, argv: list[str]) -> None:
    out = subprocess.run([*PACE, *argv, "--raw", "--no-cache"], capture_output=True, text=True)
    doc = json.loads(out.stdout)
    if not doc["ok"]:
        print(f"skip {name}: {doc['error']['code']} {doc['error']['message']}")
        return
    FIXTURES.mkdir(exist_ok=True)
    (FIXTURES / f"{name}.json").write_text(json.dumps(sanitize(doc["data"]), indent=1, sort_keys=True) + "\n")
    print(f"ok   {name}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="yesterday")
    ap.add_argument("--activity-id", default=None)
    ns = ap.parse_args()
    d = ["--to", ns.date]
    for name in ("summary", "sleep", "hr", "stress", "body-battery", "hrv", "hydration", "spo2"):
        record(f"wellness_{name}", ["wellness", name, *d])
    for name in ("status", "readiness", "endurance", "fitness-age", "hill-score"):
        record(f"training_{name}", ["training", name, *d])
    record("training_race-predictions", ["training", "race-predictions"])
    for grp, name in (("device", "list"), ("device", "last-used"), ("device", "alarms"), ("gear", "list"),
                      ("gear", "defaults"), ("goal", "list"), ("goal", "personal-records"), ("profile", "settings"),
                      ("profile", "show"), ("activity", "last")):
        record(f"{grp}_{name}", [grp, name])
    record("activity_list", ["activity", "list", "--limit", "5"])
    aid = ns.activity_id
    if aid is None:
        listing = json.loads((FIXTURES / "activity_list.json").read_text())
        aid = str(listing[0]["activityId"]) if listing else None
    if aid:
        for name in ("show", "splits", "weather", "details"):
            record(f"activity_{name}", ["activity", name, aid])


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Record**

Run: `.venv/bin/python scripts/record_fixtures.py`
Expected: `ok` lines; `skip` lines are acceptable for endpoints the account lacks (note them in the commit message).
Inspect: `grep -rl "@" tests/fixtures` -> none; `grep -c REDACTED tests/fixtures/*.json`.

- [ ] **Step 3: Write the fixture-driven shaping test**

`tests/test_fixtures.py`:

```python
"""Real (sanitized) payloads must shape without error and stay within the token budget."""

import json
from pathlib import Path

import pytest

from pace.shape import apply
from pace.spec import REGISTRY

FIX = Path(__file__).parent / "fixtures"


def _load(spec):
    p = FIX / f"{spec.group}_{spec.name}.json"
    if not p.exists():
        pytest.skip(f"no fixture for {spec.command}")
    return json.loads(p.read_text())


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.command)
def test_default_shape_is_compact(spec) -> None:
    data = _load(spec)
    shaped, report = apply(data, series=spec.series, default_fields=spec.default_fields, fields="default",
                           bucket="auto", raw=False, span_s=86400)
    size = len(json.dumps(shaped, separators=(",", ":")))
    assert size <= 12_000, f"{spec.command} default payload is {size} bytes; curate default_fields or add a SeriesSpec"
    for s in spec.series:
        if s.path in report["reduced_series"]:
            assert len(_dig(shaped, s.path)["rows"]) <= 120


@pytest.mark.parametrize("spec", [s for s in REGISTRY if s.series], ids=lambda s: s.command)
def test_declared_series_exist_in_fixture(spec) -> None:
    data = _load(spec)
    present = [s.path for s in spec.series if _dig(data, s.path) is not None]
    assert present, f"none of {[s.path for s in spec.series]} found in fixture for {spec.command}"


def _dig(obj, path):
    for part in path.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj
```

Run it: failures name the specs to curate.

- [ ] **Step 4: Curate `default_fields`**

For every failing spec open `tests/fixtures/<group>_<name>.json` and pick the fields a coach needs (totals, scores, averages, min/max, timestamps, qualitative keys) and drop identifiers, display strings, per-minute arrays not covered by a `SeriesSpec`, and duplicated nested copies.
Write them as dotted paths into `EndpointSpec.default_fields`.
Where the fixture shows a series path different from the spec's guess (e.g. `sleepStress` absent, `sleepLevels` present), fix the `SeriesSpec` list; `test_declared_series_exist_in_fixture` must pass.
Wire `STATIC_COMMANDS[0]["default_fields"] = list(LIST_FIELDS)` by importing `LIST_FIELDS` from `pace.commands.activity`.

- [ ] **Step 5: Verify and commit**

Run: `.venv/bin/python -m pytest -q` -> green (fixture tests may skip for endpoints the account lacks).

```bash
git add scripts/record_fixtures.py tests/fixtures tests/test_fixtures.py pace/spec.py pace/commands/schema.py pace/commands/activity.py
git commit -m "test: recorded Garmin fixtures; curate default fields per endpoint"
```

---

### Task 14: Docs

**Files:**
- Create: `docs/CONTRACT.md`
- Rewrite: `README.md`
- Modify: `docs/PLUGINS.md` (only if Task 12 left stale text)

- [ ] **Step 1: Write `docs/CONTRACT.md`**

Copy spec section 2 ("Output contract") verbatim from `docs/superpowers/specs/2026-09-04-llm-cli-design.md` into `docs/CONTRACT.md` under the title `# Pace output contract`, then append:

```markdown
## Keeping this file honest

`pace/envelope.py` owns the envelope keys and exit codes.
`pace/shape.py` owns pruning, field selection and the bucket ladder.
`tests/test_envelope.py` and `tests/test_shape.py` assert the examples above.
Change the code, the test, and this file in the same commit.
```

- [ ] **Step 2: Rewrite `README.md`**

```markdown
# Pace

Machine-first CLI for Garmin Connect, built to be driven by an LLM coach.
Every invocation prints exactly one compact JSON envelope on stdout; humans are not the audience.

## Install

```bash
uv sync
```

## Authenticate (human step)

```bash
pace auth login      # interactive email/password + MFA; tokens cached in ~/.garminconnect
pace auth status     # {"ok":true,...,"data":{"logged_in":true,...}}
```

An agent must never run `pace auth login`; it asks the user to.

## Discover

```bash
pace schema              # every command, flag, unit, exit code
pace schema wellness     # one group
pace <group> --help      # Typer help still works
```

## Use

```bash
pace wellness sleep --days 7
pace wellness hr --to yesterday --bucket 1h
pace activity list --days 14 --type running
pace activity details <activity-id>
pace training readiness
```

Output contract: `docs/CONTRACT.md`.
Skill for Claude Code: `.claude/skills/pace/SKILL.md` (install with `ln -s "$PWD/.claude/skills/pace" ~/.claude/skills/pace`).

## Cache and config

Responses are cached in `~/.pace/cache.db` (`PACE_HOME` overrides the directory).
TTL for today's data is short (5 min wellness, 1 h training); yesterday 1 day; older 7 days.
`~/.pace/config.toml`:

```toml
[cache_ttl_overrides]
"wellness.sleep" = 60
```

## Development

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/record_fixtures.py     # refresh tests/fixtures (needs login)
```

`scripts/garmin_api_reference.py` is the upstream interactive demo, kept as a reference for method names and payload shapes.
```

The skill path is referenced ahead of plan 3; leave the sentence in place.

- [ ] **Step 3: Verify and commit**

Run: `.venv/bin/python -m pytest -q` -> green.
Run: `grep -rn "table\|--format" README.md docs/PLUGINS.md` -> only the `activity export --format` mention, if any.

```bash
git add README.md docs/CONTRACT.md docs/PLUGINS.md
git commit -m "docs: README and output contract for pace 2.0"
```

---

## Self-review notes

- Spec 1 (layout, deletions, deps, config): Tasks 1, 12.
- Spec 2 (envelope, errors, exit codes, partial failure, shaping flags, bucket format, units static): Tasks 3, 4, 7, 11.
- Spec 3 (registry, factory, ranges, TTL by age, endpoints, hand-written activity/auth/cache/config): Tasks 5 to 10.
  Method names verified against garminconnect 0.2.38 (`get_fitnessage_data`, `get_body_battery(start, end)`, `get_device_solar_data(id, start, end)`).
- Spec 4 (schema + drift test): Task 11.
- Spec 6 (tests, fixtures, docs, version 2.0.0): Tasks 1, 13, 14.
- Deviation from spec: `tz_offset_s` derives the offset from any `*GMT`/`*Local` pair at the payload's top level, else system local; the spec allowed exactly this.
- Sections 5, 7, 8 of the spec (skill, mutations, diary) are separate plans.
