# Pace 2.0 Mutations (workout + log) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the restricted set of Garmin writes an LLM coach needs -- structured workouts (push, schedule, delete, list, show) and health logs (weight, hydration, blood pressure) -- behind the same JSON envelope, with `--dry-run` everywhere and cache invalidation on success.

**Architecture:** A small `pace/writes.py` runner wraps every write: build the exact Garmin payload, emit it under `--dry-run`, otherwise call `safe_call`, invalidate affected cache keys, and return `{"action", "resource", "id", "echo"}`.
`pace/workout.py` is a pure translator from the compact workout DSL to Garmin's workout JSON (ids and keys verified against a real workout on the user's account).
Two new hand-written command modules (`workout`, `log`) reuse `envelope.run`, `cached_call`, `shape.apply` and the factory's shared options; `pace schema` grows `workout_dsl` and the new commands; docs follow.

**Tech Stack:** Python 3.14, typer, garminconnect 0.2.38 (`upload_workout`, `get_workouts`, `get_workout_by_id`, `connectapi`, `add_weigh_in`, `add_body_composition`, `get_weigh_ins`, `delete_weigh_in`, `add_hydration_data`, `set_blood_pressure`, `get_blood_pressure`), pytest + typer `CliRunner`.

**Spec:** `docs/superpowers/specs/2026-09-04-llm-cli-design.md` section 7 (Mutations) plus section 2 (envelope contract).

## Global Constraints

- stdout = exactly one compact JSON envelope per invocation; stderr empty unless `--verbose`. Error codes/exit codes unchanged: `bad_args`(2) `auth_required`(3) `rate_limited`(4) others 1, Abort 130.
- Write commands never READ the response cache; a successful write invalidates the cache keys of the affected resource (`Cache.invalidate(prefix)`), then returns `data: {"action": "create"|"delete", "resource": "workout"|"weight"|"hydration"|"bp", "id": <id or null>, "echo": <parsed input>}`.
- Every write command accepts `--dry-run`: no network call, `ok: true`, `data: {"dry_run": true, "payload": <exact Garmin payload>}`, `cache: null`, `shape: null`.
- Deletes exist only for workouts and weigh-ins (and blood pressure readings). No activity delete/edit, no gear assignment, no manual activity, no file upload. No `--yes` flag anywhere.
- Invalid DSL -> `bad_args`; `error.message` names the JSON path (e.g. `steps[1].steps[0].target`).
- Garmin IDs verified live (2026-09-06) and used verbatim: step types warmup 1 / cooldown 2 / interval 3 / recovery 4 / rest 5 / repeat 6; end conditions `lap.button` 1 / `time` 2 / `distance` 3 / `iterations` 7; target types `no.target` 1 / `power.zone` 2 / `cadence.zone` 3 / `heart.rate.zone` 4 / `speed.zone` 5 / `pace.zone` 6. Pace targets are `pace.zone` with `targetValueOne` = faster bound (higher m/s), `targetValueTwo` = slower bound; HR zones use `zoneNumber`; bpm/power/cadence ranges use `targetValueOne` (low) / `targetValueTwo` (high).
- Dependencies unchanged (`garminconnect`, `pydantic`, `typer`). ASCII only; markdown newline after each period. No Claude attribution in commits. Tests via `.venv/bin/python -m pytest -q`; `ruff check pace tests scripts` clean at every commit.
- Live writes against the user's Garmin account happen ONLY in Task 7 and only after the controller obtains the user's explicit go-ahead.

## File Map

| Path | Responsibility |
|---|---|
| `pace/cache.py` | + `Cache.invalidate(prefix) -> int` |
| `pace/writes.py` | `run_write(...)`: dry-run / call / invalidate / result envelope for every write |
| `pace/workout.py` | DSL parsing + translation to Garmin workout JSON (`build_workout`), unit parsers |
| `pace/commands/workout.py` | `list`, `show`, `push`, `schedule`, `delete` |
| `pace/commands/log.py` | `weight` (add/list/delete), `hydration`, `bp` (add/list) |
| `pace/commands/schema.py` | new STATIC_COMMANDS entries, `workout_dsl` block, groups |
| `pace/cli.py` | wire `workout`, `log` |
| `docs/CONTRACT.md`, `README.md` | writes section, DSL reference pointer |
| `tests/test_cache.py`, `tests/test_writes.py`, `tests/test_workout_dsl.py`, `tests/test_workout_cmd.py`, `tests/test_log_cmd.py`, `tests/test_schema.py` | tests |

---

### Task 1: `Cache.invalidate(prefix)`

**Files:**
- Modify: `pace/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Produces: `Cache.invalidate(self, prefix: str) -> int` -- deletes every row whose key starts with `prefix` (SQL `LIKE prefix || '%'`, with `%`/`_` in the prefix escaped), returns the number of rows removed. Cache keys are built by `pace.commands._helpers.cached_call` as `"<method>:<arg1>:<arg2>..."`, so `invalidate("get_hydration_data:2026-09-06")` hits one day and `invalidate("get_weigh_ins:")` hits every recorded range.

- [ ] **Step 1: Write failing tests** (append to `tests/test_cache.py`)

```python
@pytest.mark.asyncio
async def test_invalidate_prefix_only(tmp_path) -> None:
    cache = Cache(tmp_path / "c.db")
    await cache.get_or_fetch("get_hydration_data:2026-09-06", lambda: _ret(1), ttl=600)
    await cache.get_or_fetch("get_hydration_data:2026-09-05", lambda: _ret(2), ttl=600)
    await cache.get_or_fetch("get_weigh_ins:2026-09-01:2026-09-06", lambda: _ret(3), ttl=600)
    assert cache.invalidate("get_hydration_data:2026-09-06") == 1
    assert cache.invalidate("get_weigh_ins:") == 1
    assert cache.stats()["entries"] == 1
    v, hit, _ = await cache.get_or_fetch("get_hydration_data:2026-09-05", lambda: _ret(9), ttl=600)
    assert v == 2 and hit is True
    cache.close()


@pytest.mark.asyncio
async def test_invalidate_escapes_like_wildcards(tmp_path) -> None:
    cache = Cache(tmp_path / "c.db")
    await cache.get_or_fetch("a_b:1", lambda: _ret(1), ttl=600)
    await cache.get_or_fetch("axb:1", lambda: _ret(2), ttl=600)
    assert cache.invalidate("a_b") == 1          # `_` must not act as a wildcard
    assert cache.stats()["entries"] == 1
    cache.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_cache.py -q` -> AttributeError `invalidate`.

- [ ] **Step 3: Implement** (add to `class Cache`)

```python
    def invalidate(self, prefix: str) -> int:
        """Delete every entry whose key starts with `prefix`. Returns rows removed."""
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        cur = self._conn.execute(
            "DELETE FROM cache WHERE key LIKE ? ESCAPE '\\'", (escaped + "%",)
        )
        self._conn.commit()
        return cur.rowcount
```

- [ ] **Step 4: Run tests** -> green. **Step 5: Commit**

```bash
git add pace/cache.py tests/test_cache.py
git commit -m "feat: Cache.invalidate(prefix) for write-through invalidation"
```

---

### Task 2: Write runner `pace/writes.py`

**Files:**
- Create: `pace/writes.py`
- Test: `tests/test_writes.py`

**Interfaces:**
- Consumes: `pace.api.safe_call`, `pace.cache.Cache`, `pace.envelope.Result/PaceError/log`.
- Produces:

```python
@dataclass(frozen=True)
class WriteSpec:
    resource: str                        # "workout" | "weight" | "hydration" | "bp"
    action: str                          # "create" | "delete"
    method: str                          # garminconnect method name passed to safe_call
    args: tuple[Any, ...] = ()           # positional args for the method
    kwargs: dict[str, Any] = field(default_factory=dict)
    payload: Any = None                  # what --dry-run shows (the exact body / the args)
    echo: Any = None                     # parsed user input, returned on success
    invalidate: tuple[str, ...] = ()     # cache key prefixes to drop after success
    id_of: Callable[[Any], Any] | None = None   # extract id from the API response

async def run_write(spec: WriteSpec, *, dry_run: bool) -> Result
```

  - dry run: `Result({"dry_run": True, "payload": spec.payload})` with no network.
  - otherwise: `ok, resp, err = await safe_call(spec.method, *spec.args, **spec.kwargs)`; on error raise `PaceError.from_api(err)`; on success open `Cache()`, call `invalidate` for each prefix (log count), close; return `Result({"action", "resource", "id": spec.id_of(resp) if id_of else None, "echo": spec.echo})`.
  - Response payload from Garmin is NOT returned (it is noisy); only the id.

- [ ] **Step 1: Write failing tests**

`tests/test_writes.py`:

```python
"""Write runner: dry-run, call, invalidate, result."""

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

    async def safe_call(method, *args, **kwargs):
        state["calls"].append((method, args, kwargs))
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
```

- [ ] **Step 2: Run to verify failure** -> ModuleNotFoundError `pace.writes`.

- [ ] **Step 3: Implement `pace/writes.py`**

```python
"""Shared runner for every Garmin write: dry-run, call, invalidate, report."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pace.api import safe_call
from pace.cache import Cache
from pace.envelope import PaceError, Result, log


@dataclass(frozen=True)
class WriteSpec:
    resource: str
    action: str
    method: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    payload: Any = None
    echo: Any = None
    invalidate: tuple[str, ...] = ()
    id_of: Callable[[Any], Any] | None = None


async def run_write(spec: WriteSpec, *, dry_run: bool) -> Result:
    if dry_run:
        return Result({"dry_run": True, "payload": spec.payload})
    ok, resp, err = await safe_call(spec.method, *spec.args, **spec.kwargs)
    if not ok:
        assert err is not None
        raise PaceError.from_api(err)
    if spec.invalidate:
        cache = Cache()
        try:
            for prefix in spec.invalidate:
                log(f"invalidate {prefix}* -> {cache.invalidate(prefix)} rows")
        finally:
            cache.close()
    ident = spec.id_of(resp) if spec.id_of is not None and resp is not None else None
    return Result({"action": spec.action, "resource": spec.resource, "id": ident, "echo": spec.echo})
```

- [ ] **Step 4: Run tests** -> 4 passed; full suite green. **Step 5: Commit**

```bash
git add pace/writes.py tests/test_writes.py
git commit -m "feat: write runner with dry-run and cache invalidation"
```

---

### Task 3: Workout DSL translator `pace/workout.py`

**Files:**
- Create: `pace/workout.py`
- Test: `tests/test_workout_dsl.py`

**Interfaces:**
- Consumes: `pace.envelope.PaceError`.
- Produces (all in `pace.workout`):
  - `SPORTS: dict[str, tuple[int, str]]` = running (1,"running"), cycling (2,"cycling"), swimming (3,"swimming"), walking (4,"walking"), hiking (7,"hiking").
  - `STEP_TYPES = {"warmup": 1, "cooldown": 2, "interval": 3, "recovery": 4, "rest": 5}`; repeat = 6.
  - `parse_duration(text: str, path: str) -> int` seconds: `45s`, `10m`, `1h`, `1h30m`, `90m`, `1h05m30s`.
  - `parse_distance(text: str, path: str) -> float` meters: `400m`, `1km`, `5.5km`, `1.2 km`.
  - `parse_pace(text: str, path: str) -> float` m/s from `"4:15"` (min:sec per km).
  - `parse_range(text: str, path: str, kind: str) -> tuple[float, float]` from `"140-150"`; single value `"150"` gives `(150, 150)`; low <= high enforced.
  - `build_workout(dsl: dict) -> dict` -> the exact Garmin payload for `upload_workout`.
  - `estimate_duration_s(dsl_steps) -> int` (sum of durations, repeat multiplies, distance steps use target pace midpoint when present else 0).
  - `DSL_REFERENCE: dict` -- machine-readable description of the DSL (fields, enums, examples) for `pace schema`.
  - All errors: `PaceError("bad_args", f"{path}: <reason>")`.

- [ ] **Step 1: Write failing tests**

`tests/test_workout_dsl.py`:

```python
"""Compact workout DSL -> Garmin workout JSON."""

import pytest

from pace.envelope import PaceError
from pace.workout import (
    DSL_REFERENCE,
    build_workout,
    estimate_duration_s,
    parse_distance,
    parse_duration,
    parse_pace,
    parse_range,
)


def test_parse_duration() -> None:
    assert parse_duration("45s", "p") == 45
    assert parse_duration("10m", "p") == 600
    assert parse_duration("1h", "p") == 3600
    assert parse_duration("1h30m", "p") == 5400
    assert parse_duration("1h05m30s", "p") == 3930
    for bad in ("", "10", "10x", "-5m", "1m1h"):
        with pytest.raises(PaceError) as exc:
            parse_duration(bad, "steps[0].duration")
        assert exc.value.code == "bad_args" and exc.value.message.startswith("steps[0].duration")


def test_parse_distance() -> None:
    assert parse_distance("400m", "p") == 400.0
    assert parse_distance("1km", "p") == 1000.0
    assert parse_distance("5.5km", "p") == 5500.0
    assert parse_distance("1.2 km", "p") == 1200.0
    with pytest.raises(PaceError):
        parse_distance("5", "p")


def test_parse_pace_and_range() -> None:
    assert parse_pace("4:00", "p") == pytest.approx(1000 / 240)
    assert parse_pace("5:30", "p") == pytest.approx(1000 / 330)
    assert parse_range("140-150", "p", "hr") == (140.0, 150.0)
    assert parse_range("150", "p", "hr") == (150.0, 150.0)
    with pytest.raises(PaceError):
        parse_range("150-140", "p", "hr")
    with pytest.raises(PaceError):
        parse_pace("4-15", "p")


def _dsl():
    return {
        "name": "Tempo 3x10", "sport": "running",
        "steps": [
            {"type": "warmup", "duration": "15m"},
            {"repeat": 3, "steps": [
                {"type": "interval", "duration": "10m", "target": {"pace": "4:15-4:25"}},
                {"type": "recovery", "duration": "3m"}]},
            {"type": "cooldown", "distance": "1km"},
        ],
    }


def test_build_workout_structure() -> None:
    w = build_workout(_dsl())
    assert w["workoutName"] == "Tempo 3x10"
    assert w["sportType"] == {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}
    seg = w["workoutSegments"][0]
    assert seg["segmentOrder"] == 1 and seg["sportType"] == w["sportType"]
    steps = seg["workoutSteps"]
    assert [s["stepOrder"] for s in steps] == [1, 2, 5]
    warm = steps[0]
    assert warm["type"] == "ExecutableStepDTO"
    assert warm["stepType"] == {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1}
    assert warm["endCondition"]["conditionTypeKey"] == "time" and warm["endConditionValue"] == 900.0
    assert warm["targetType"]["workoutTargetTypeKey"] == "no.target"
    rep = steps[1]
    assert rep["type"] == "RepeatGroupDTO" and rep["numberOfIterations"] == 3
    assert rep["endCondition"] == {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False}
    assert rep["endConditionValue"] == 3.0
    assert [s["stepOrder"] for s in rep["workoutSteps"]] == [3, 4]
    cool = steps[2]
    assert cool["endCondition"]["conditionTypeKey"] == "distance" and cool["endCondition"]["conditionTypeId"] == 3
    assert cool["endConditionValue"] == 1000.0
    assert w["estimatedDurationInSecs"] == estimate_duration_s(_dsl()["steps"])


def test_pace_target_faster_bound_first() -> None:
    w = build_workout(_dsl())
    iv = w["workoutSegments"][0]["workoutSteps"][1]["workoutSteps"][0]
    assert iv["targetType"] == {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 6}
    assert iv["targetValueOne"] == pytest.approx(1000 / 255)   # 4:15 -> faster -> higher m/s
    assert iv["targetValueTwo"] == pytest.approx(1000 / 265)   # 4:25
    assert iv["zoneNumber"] is None


def test_other_targets_and_open_end() -> None:
    dsl = {"name": "mix", "sport": "cycling", "steps": [
        {"type": "interval", "open": True, "target": {"hr": "z2"}},
        {"type": "interval", "duration": "5m", "target": {"hr": "140-150"}},
        {"type": "interval", "duration": "5m", "target": {"power": "200-220"}},
        {"type": "rest", "duration": "1m", "target": {"cadence": "85-95"}},
    ]}
    steps = build_workout(dsl)["workoutSegments"][0]["workoutSteps"]
    z2 = steps[0]
    assert z2["endCondition"] == {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True}
    assert z2["endConditionValue"] == 0.0
    assert z2["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone" and z2["zoneNumber"] == 2
    assert z2["targetValueOne"] is None and z2["targetValueTwo"] is None
    bpm = steps[1]
    assert bpm["targetType"]["workoutTargetTypeId"] == 4 and (bpm["targetValueOne"], bpm["targetValueTwo"]) == (140.0, 150.0)
    assert bpm["zoneNumber"] is None
    assert steps[2]["targetType"]["workoutTargetTypeKey"] == "power.zone" and steps[2]["targetValueTwo"] == 220.0
    assert steps[3]["stepType"]["stepTypeKey"] == "rest"
    assert steps[3]["targetType"]["workoutTargetTypeKey"] == "cadence.zone" and steps[3]["targetValueOne"] == 85.0


def test_estimate_duration() -> None:
    assert estimate_duration_s(_dsl()["steps"]) == 900 + 3 * (600 + 180) + 0   # distance cooldown has no pace -> 0
    with_pace = [{"type": "interval", "distance": "1km", "target": {"pace": "4:00-5:00"}}]
    assert estimate_duration_s(with_pace) == 270          # 1000 m at midpoint 4:30/km


@pytest.mark.parametrize("dsl, path", [
    ({"sport": "running", "steps": []}, "name"),
    ({"name": "x", "sport": "rowing", "steps": [{"type": "warmup", "duration": "1m"}]}, "sport"),
    ({"name": "x", "sport": "running", "steps": []}, "steps"),
    ({"name": "x", "sport": "running", "steps": [{"type": "sprint", "duration": "1m"}]}, "steps[0].type"),
    ({"name": "x", "sport": "running", "steps": [{"type": "warmup"}]}, "steps[0]"),
    ({"name": "x", "sport": "running", "steps": [{"type": "warmup", "duration": "1m", "distance": "1km"}]}, "steps[0]"),
    ({"name": "x", "sport": "running", "steps": [{"type": "warmup", "duration": "1m", "target": {"pace": "4:00", "hr": "z2"}}]}, "steps[0].target"),
    ({"name": "x", "sport": "running", "steps": [{"type": "warmup", "duration": "1m", "target": {"hr": "z9"}}]}, "steps[0].target.hr"),
    ({"name": "x", "sport": "running", "steps": [{"repeat": 0, "steps": [{"type": "interval", "duration": "1m"}]}]}, "steps[0].repeat"),
    ({"name": "x", "sport": "running", "steps": [{"repeat": 2, "steps": [{"type": "interval", "duration": "1x"}]}]}, "steps[0].steps[0].duration"),
])
def test_invalid_dsl_names_path(dsl, path) -> None:
    with pytest.raises(PaceError) as exc:
        build_workout(dsl)
    assert exc.value.code == "bad_args"
    assert exc.value.message.startswith(path), exc.value.message


def test_dsl_reference_is_json_friendly() -> None:
    import json
    doc = json.loads(json.dumps(DSL_REFERENCE))
    assert set(doc["sports"]) == {"running", "cycling", "swimming", "walking", "hiking"}
    assert "example" in doc and doc["example"]["steps"]
```

- [ ] **Step 2: Run to verify failure** -> ModuleNotFoundError `pace.workout`.

- [ ] **Step 3: Implement `pace/workout.py`**

```python
"""Compact workout DSL -> Garmin Connect workout JSON.

IDs and keys below were read from a real workout on the user's account
(2026-09-06); the garminconnect ConditionType constants are NOT used because
they disagree with Garmin for `distance` and `lap.button`.
"""

import re
from typing import Any

from pace.envelope import PaceError

SPORTS: dict[str, tuple[int, str]] = {
    "running": (1, "running"), "cycling": (2, "cycling"), "swimming": (3, "swimming"),
    "walking": (4, "walking"), "hiking": (7, "hiking"),
}
STEP_TYPES = {"warmup": 1, "cooldown": 2, "interval": 3, "recovery": 4, "rest": 5}
REPEAT_TYPE = 6
END = {"lap.button": 1, "time": 2, "distance": 3, "iterations": 7}
TARGET = {"no.target": 1, "power.zone": 2, "cadence.zone": 3, "heart.rate.zone": 4, "speed.zone": 5, "pace.zone": 6}
TARGET_KEYS = ("pace", "hr", "power", "cadence")
END_KEYS = ("duration", "distance", "open")

_DURATION = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
_DISTANCE = re.compile(r"^(\d+(?:\.\d+)?)\s*(m|km)$")
_PACE = re.compile(r"^(\d{1,2}):(\d{2})$")
_RANGE = re.compile(r"^(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?$")
_ZONE = re.compile(r"^z([1-5])$", re.IGNORECASE)


def _bad(path: str, why: str) -> PaceError:
    return PaceError("bad_args", f"{path}: {why}")


def parse_duration(text: str, path: str) -> int:
    m = _DURATION.match(str(text).strip()) if text else None
    if not m or not any(m.groups()):
        raise _bad(path, f"invalid duration {text!r}; use 45s, 10m, 1h, 1h30m")
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mi * 60 + s


def parse_distance(text: str, path: str) -> float:
    m = _DISTANCE.match(str(text).strip()) if text else None
    if not m:
        raise _bad(path, f"invalid distance {text!r}; use 400m, 1km, 5.5km")
    value, unit = float(m.group(1)), m.group(2)
    return value * 1000.0 if unit == "km" else value


def parse_pace(text: str, path: str) -> float:
    """min:sec per km -> m/s."""
    m = _PACE.match(str(text).strip())
    if not m:
        raise _bad(path, f"invalid pace {text!r}; use min:sec per km like 4:15")
    secs = int(m.group(1)) * 60 + int(m.group(2))
    if secs <= 0:
        raise _bad(path, "pace must be positive")
    return 1000.0 / secs


def parse_range(text: str, path: str, kind: str) -> tuple[float, float]:
    m = _RANGE.match(str(text).strip())
    if not m:
        raise _bad(path, f"invalid {kind} {text!r}; use low-high like 140-150")
    lo = float(m.group(1))
    hi = float(m.group(2)) if m.group(2) else lo
    if lo > hi:
        raise _bad(path, f"{kind} range low > high")
    return lo, hi


def _sport(dsl: dict, path: str) -> dict[str, Any]:
    key = dsl.get("sport")
    if key not in SPORTS:
        raise _bad(f"{path}sport", f"unknown sport {key!r}; one of {', '.join(SPORTS)}")
    sid, skey = SPORTS[key]
    return {"sportTypeId": sid, "sportTypeKey": skey, "displayOrder": sid}


def _target(step: dict, path: str) -> dict[str, Any]:
    tgt = step.get("target")
    if tgt is None:
        return {"targetType": _ttype("no.target"), "targetValueOne": None, "targetValueTwo": None, "zoneNumber": None}
    if not isinstance(tgt, dict):
        raise _bad(f"{path}.target", "must be an object")
    keys = [k for k in TARGET_KEYS if k in tgt]
    if len(keys) != 1:
        raise _bad(f"{path}.target", f"exactly one of {', '.join(TARGET_KEYS)}")
    kind = keys[0]
    raw = str(tgt[kind])
    if kind == "pace":
        parts = [p.strip() for p in raw.split("-")]
        if len(parts) not in (1, 2):
            raise _bad(f"{path}.target.pace", f"invalid pace range {raw!r}; use 4:15-4:25")
        speeds = sorted(parse_pace(p, f"{path}.target.pace") for p in parts)
        fast, slow = speeds[-1], speeds[0]
        return {"targetType": _ttype("pace.zone"), "targetValueOne": fast, "targetValueTwo": slow, "zoneNumber": None}
    if kind == "hr":
        z = _ZONE.match(raw.strip())
        if z:
            return {"targetType": _ttype("heart.rate.zone"), "targetValueOne": None, "targetValueTwo": None,
                    "zoneNumber": int(z.group(1))}
        if raw.strip().lower().startswith("z"):
            raise _bad(f"{path}.target.hr", f"invalid zone {raw!r}; use z1..z5")
        lo, hi = parse_range(raw, f"{path}.target.hr", "hr")
        return {"targetType": _ttype("heart.rate.zone"), "targetValueOne": lo, "targetValueTwo": hi, "zoneNumber": None}
    tkey = "power.zone" if kind == "power" else "cadence.zone"
    lo, hi = parse_range(raw, f"{path}.target.{kind}", kind)
    return {"targetType": _ttype(tkey), "targetValueOne": lo, "targetValueTwo": hi, "zoneNumber": None}


def _ttype(key: str) -> dict[str, Any]:
    return {"workoutTargetTypeId": TARGET[key], "workoutTargetTypeKey": key, "displayOrder": TARGET[key]}


def _end(step: dict, path: str) -> tuple[dict[str, Any], float]:
    keys = [k for k in END_KEYS if k in step]
    if len(keys) != 1:
        raise _bad(path, f"exactly one of {', '.join(END_KEYS)} is required")
    k = keys[0]
    if k == "duration":
        return _econd("time", True), float(parse_duration(step[k], f"{path}.duration"))
    if k == "distance":
        return _econd("distance", True), parse_distance(step[k], f"{path}.distance")
    if step[k] is not True:
        raise _bad(f"{path}.open", "must be true")
    return _econd("lap.button", True), 0.0


def _econd(key: str, displayable: bool) -> dict[str, Any]:
    return {"conditionTypeId": END[key], "conditionTypeKey": key, "displayOrder": END[key], "displayable": displayable}


def _steps(items: Any, path: str, order: list[int]) -> list[dict[str, Any]]:
    if not isinstance(items, list) or not items:
        raise _bad(path, "must be a non-empty list of steps")
    out: list[dict[str, Any]] = []
    for i, step in enumerate(items):
        p = f"{path}[{i}]"
        if not isinstance(step, dict):
            raise _bad(p, "must be an object")
        if "repeat" in step:
            n = step["repeat"]
            if not isinstance(n, int) or isinstance(n, bool) or n < 1:
                raise _bad(f"{p}.repeat", "must be an integer >= 1")
            order[0] += 1
            my_order = order[0]
            children = _steps(step.get("steps"), f"{p}.steps", order)
            out.append({
                "type": "RepeatGroupDTO", "stepOrder": my_order,
                "stepType": {"stepTypeId": REPEAT_TYPE, "stepTypeKey": "repeat", "displayOrder": REPEAT_TYPE},
                "numberOfIterations": n, "workoutSteps": children,
                "endCondition": _econd("iterations", False), "endConditionValue": float(n),
                "smartRepeat": False, "childStepId": None,
            })
            continue
        kind = step.get("type")
        if kind not in STEP_TYPES:
            raise _bad(f"{p}.type", f"unknown step type {kind!r}; one of {', '.join(STEP_TYPES)}")
        end, value = _end(step, p)
        order[0] += 1
        out.append({
            "type": "ExecutableStepDTO", "stepOrder": order[0],
            "stepType": {"stepTypeId": STEP_TYPES[kind], "stepTypeKey": kind, "displayOrder": STEP_TYPES[kind]},
            "endCondition": end, "endConditionValue": value,
            **_target(step, p),
            "description": step.get("note") or None, "childStepId": None,
        })
    return out


def _step_seconds(step: dict) -> float:
    if "repeat" in step:
        return step["repeat"] * sum(_step_seconds(s) for s in step.get("steps", []))
    if "duration" in step:
        return parse_duration(step["duration"], "steps")
    if "distance" in step and isinstance(step.get("target"), dict) and "pace" in step["target"]:
        parts = [parse_pace(p.strip(), "steps") for p in str(step["target"]["pace"]).split("-")]
        mid = sum(parts) / len(parts)
        return parse_distance(step["distance"], "steps") / mid
    return 0.0


def estimate_duration_s(dsl_steps: list[dict]) -> int:
    return int(round(sum(_step_seconds(s) for s in dsl_steps)))


def build_workout(dsl: Any) -> dict[str, Any]:
    if not isinstance(dsl, dict):
        raise _bad("", "workout must be a JSON object")
    name = dsl.get("name")
    if not isinstance(name, str) or not name.strip():
        raise _bad("name", "required non-empty string")
    sport = _sport(dsl, "")
    steps = _steps(dsl.get("steps"), "steps", [0])
    return {
        "workoutName": name.strip(),
        "description": dsl.get("description") or None,
        "sportType": sport,
        "estimatedDurationInSecs": estimate_duration_s(dsl["steps"]),
        "workoutSegments": [{"segmentOrder": 1, "sportType": sport, "workoutSteps": steps}],
    }


DSL_REFERENCE: dict[str, Any] = {
    "sports": list(SPORTS),
    "step_types": list(STEP_TYPES),
    "repeat": {"repeat": "N (int >= 1)", "steps": "[nested steps]"},
    "end_condition": {"exactly_one_of": ["duration", "distance", "open"],
                      "duration": "45s | 10m | 1h | 1h30m", "distance": "400m | 1km | 5.5km", "open": True},
    "target": {"optional_exactly_one_of": ["pace", "hr", "power", "cadence"],
               "pace": "min:sec per km, single or range, e.g. 4:15-4:25",
               "hr": "z1..z5 or bpm range 140-150", "power": "W range 200-220", "cadence": "range 170-180"},
    "optional": {"description": "workout description", "note": "per-step description"},
    "example": {
        "name": "Tempo 3x10", "sport": "running",
        "steps": [
            {"type": "warmup", "duration": "15m"},
            {"repeat": 3, "steps": [
                {"type": "interval", "duration": "10m", "target": {"pace": "4:15-4:25"}},
                {"type": "recovery", "duration": "3m"}]},
            {"type": "cooldown", "distance": "1km"}]},
}
```

- [ ] **Step 4: Run tests** -> all pass (the parametrized invalid cases must each start with the named path -- adjust messages, not the test).

Note on `steps[0]` for the missing/duplicate end-condition cases: `_end` raises with `path` = `steps[0]`, which satisfies `startswith("steps[0]")`.

- [ ] **Step 5: Commit**

```bash
git add pace/workout.py tests/test_workout_dsl.py
git commit -m "feat: compact workout DSL translated to Garmin workout JSON"
```

---

### Task 4: `workout` command group

**Files:**
- Create: `pace/commands/workout.py`
- Modify: `pace/cli.py` (wire `workout`)
- Test: `tests/test_workout_cmd.py`

**Interfaces:**
- Consumes: `pace.writes.WriteSpec/run_write`, `pace.workout.build_workout`, `pace.envelope.run/fail/Result/PaceError`, `pace.factory.FIELDS_OPT/BUCKET_OPT/RAW_OPT/NO_CACHE_OPT/shape_args`, `pace.commands._helpers.cached_call`, `pace.cache.Cache/CacheTracker`, `pace.shape.apply`, `pace.dates.parse_date`.
- Produces: `pace.commands.workout.app` with commands:
  - `list [--limit N=50]` -> `get_workouts(0, limit)`; default fields `WORKOUT_LIST_FIELDS = ("workoutId", "workoutName", "sportType.sportTypeKey", "estimatedDurationInSecs", "estimatedDistanceInMeters", "updatedDate")`; TTL 600; `truncated` when the response length == limit.
  - `show WORKOUT_ID` -> `get_workout_by_id`; pruned full payload; TTL 86400.
  - `push FILE` (`-` = stdin) `[--dry-run]` -> `build_workout` then `WriteSpec(resource="workout", action="create", method="upload_workout", args=(payload,), payload=payload, echo=dsl, invalidate=("get_workouts:",), id_of=lambda r: r.get("workoutId"))`.
  - `schedule WORKOUT_ID DATE [--dry-run]` -> `WriteSpec("workout", "create", "connectapi", args=(f"/workout-service/schedule/{id}",), kwargs={"method": "POST", "json": {"date": iso}}, payload={"path": ..., "json": {"date": iso}}, echo={"workout_id": id, "date": iso}, id_of=lambda r: (r or {}).get("workoutScheduleId"))`. No cache to invalidate.
  - `delete WORKOUT_ID [--dry-run]` -> `WriteSpec("workout", "delete", "connectapi", args=(f"/workout-service/workout/{id}",), kwargs={"method": "DELETE"}, payload={"path": ...}, echo={"workout_id": id}, invalidate=("get_workouts:", f"get_workout_by_id:{id}"))`.
  - `DRY_RUN_OPT = typer.Option(False, "--dry-run", help="Show the exact Garmin payload; make no network call.")` exported for `log`.
  - Every command string is `"workout <name>"`.

- [ ] **Step 1: Write failing tests**

`tests/test_workout_cmd.py`:

```python
import json

import pytest
from typer.testing import CliRunner

from pace.api import ApiError
from pace.commands.workout import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")


@pytest.fixture
def fake_api(monkeypatch):
    state = {"calls": [], "resp": {"workoutId": 77, "workoutScheduleId": 9}, "err": None}

    async def safe_call(method, *args, **kwargs):
        state["calls"].append((method, args, kwargs))
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
```

- [ ] **Step 2: Run to verify failure** -> ModuleNotFoundError.

- [ ] **Step 3: Implement `pace/commands/workout.py`**

```python
"""Workout commands: list, show, push (DSL), schedule, delete."""

import json
import sys
from pathlib import Path
from typing import Any

import typer

from pace.cache import Cache, CacheTracker
from pace.commands._helpers import cached_call
from pace.dates import parse_date
from pace.envelope import PaceError, Result, fail, run
from pace.factory import BUCKET_OPT, FIELDS_OPT, NO_CACHE_OPT, RAW_OPT, shape_args
from pace.shape import apply
from pace.workout import build_workout
from pace.writes import WriteSpec, run_write

app = typer.Typer(no_args_is_help=True, help="Structured workouts: list, show, push, schedule, delete.")

DRY_RUN_OPT = typer.Option(False, "--dry-run", help="Show the exact Garmin payload; make no network call.")
_LIST_TTL = 600
_SHOW_TTL = 86400
WORKOUT_LIST_FIELDS: tuple[str, ...] = (
    "workoutId", "workoutName", "sportType.sportTypeKey", "estimatedDurationInSecs",
    "estimatedDistanceInMeters", "updatedDate",
)


def _read_json(source: str) -> Any:
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    except OSError as exc:
        raise PaceError("bad_args", f"cannot read {source}: {exc}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise PaceError("bad_args", f"{source}: invalid JSON ({exc.msg} at line {exc.lineno})") from None


@app.command("list")
def list_(limit: int = typer.Option(50, "--limit", "-l", help="Max workouts (newest first)."),
          fields: str = FIELDS_OPT, bucket: str = BUCKET_OPT, raw: bool = RAW_OPT, no_cache: bool = NO_CACHE_OPT) -> None:
    """Saved workouts."""
    cmd = "workout list"
    try:
        f, b, echo = shape_args(fields, bucket, raw)
    except PaceError as err:
        fail(cmd, err)

    async def work() -> Result:
        cache = Cache()
        tracker = CacheTracker()
        try:
            rows = await cached_call("get_workouts", 0, limit, cache=cache, ttl=_LIST_TTL, tracker=tracker, refresh=no_cache)
        finally:
            cache.close()
        rows = rows or []
        shaped, report = apply(rows, series=(), default_fields=WORKOUT_LIST_FIELDS, fields=f, bucket=b, raw=raw, span_s=0)
        return Result(shaped, tracker.report(), report, truncated=len(rows) >= limit)

    run(cmd, {"limit": limit, **echo}, work)


@app.command("show")
def show(workout_id: int = typer.Argument(..., metavar="WORKOUT_ID"),
         fields: str = FIELDS_OPT, bucket: str = BUCKET_OPT, raw: bool = RAW_OPT, no_cache: bool = NO_CACHE_OPT) -> None:
    """One saved workout, full structure."""
    cmd = "workout show"
    try:
        f, b, echo = shape_args(fields, bucket, raw)
    except PaceError as err:
        fail(cmd, err)

    async def work() -> Result:
        cache = Cache()
        tracker = CacheTracker()
        try:
            data = await cached_call("get_workout_by_id", workout_id, cache=cache, ttl=_SHOW_TTL, tracker=tracker, refresh=no_cache)
        finally:
            cache.close()
        shaped, report = apply(data, series=(), default_fields=(), fields=f, bucket=b, raw=raw, span_s=0)
        return Result(shaped, tracker.report(), report)

    run(cmd, {"workout_id": workout_id, **echo}, work)


@app.command("push")
def push(file: str = typer.Argument(..., metavar="FILE", help="Path to a DSL JSON file, or - for stdin."),
         dry_run: bool = DRY_RUN_OPT) -> None:
    """Create a workout on Garmin Connect from the compact DSL (see `pace schema` -> workout_dsl)."""
    cmd = "workout push"
    try:
        dsl = _read_json(file)
        payload = build_workout(dsl)
    except PaceError as err:
        fail(cmd, err)
    spec = WriteSpec(resource="workout", action="create", method="upload_workout", args=(payload,),
                     payload=payload, echo=dsl, invalidate=("get_workouts:",),
                     id_of=lambda r: r.get("workoutId") if isinstance(r, dict) else None)
    run(cmd, {"file": file, "dry_run": dry_run}, lambda: run_write(spec, dry_run=dry_run))


@app.command("schedule")
def schedule(workout_id: int = typer.Argument(..., metavar="WORKOUT_ID"),
             date: str = typer.Argument(..., metavar="DATE", help="YYYY-MM-DD | today | +Nd"),
             dry_run: bool = DRY_RUN_OPT) -> None:
    """Put a saved workout on the Garmin calendar (syncs to the watch)."""
    cmd = "workout schedule"
    try:
        iso = parse_date(date).isoformat()
    except PaceError as err:
        fail(cmd, err)
    path = f"/workout-service/schedule/{workout_id}"
    body = {"date": iso}
    spec = WriteSpec(resource="workout", action="create", method="connectapi", args=(path,),
                     kwargs={"method": "POST", "json": body}, payload={"path": path, "json": body},
                     echo={"workout_id": workout_id, "date": iso},
                     id_of=lambda r: r.get("workoutScheduleId") if isinstance(r, dict) else None)
    run(cmd, {"workout_id": workout_id, "date": iso, "dry_run": dry_run}, lambda: run_write(spec, dry_run=dry_run))


@app.command("delete")
def delete(workout_id: int = typer.Argument(..., metavar="WORKOUT_ID"), dry_run: bool = DRY_RUN_OPT) -> None:
    """Delete a saved workout (only workouts; activities are never deleted)."""
    path = f"/workout-service/workout/{workout_id}"
    spec = WriteSpec(resource="workout", action="delete", method="connectapi", args=(path,),
                     kwargs={"method": "DELETE"}, payload={"path": path}, echo={"workout_id": workout_id},
                     invalidate=("get_workouts:", f"get_workout_by_id:{workout_id}"))
    run("workout delete", {"workout_id": workout_id, "dry_run": dry_run}, lambda: run_write(spec, dry_run=dry_run))
```

Wire in `pace/cli.py`: add `workout` to the `from pace.commands import ...` line and `(workout, "workout")` to the `add_typer` loop.

- [ ] **Step 4: Run tests** -> 8 passed; full suite green except `tests/test_schema.py::test_every_cli_command_is_in_schema` (new commands not yet in the manifest -- expected until Task 6). Run `ruff check`.

- [ ] **Step 5: Commit**

```bash
git add pace/commands/workout.py pace/cli.py tests/test_workout_cmd.py
git commit -m "feat: workout list/show/push/schedule/delete"
```

---

### Task 5: `log` command group

**Files:**
- Create: `pace/commands/log.py`
- Modify: `pace/cli.py` (wire `log`)
- Test: `tests/test_log_cmd.py`

**Interfaces:**
- Consumes: as Task 4 plus `pace.commands.workout.DRY_RUN_OPT`, `pace.dates.resolve_range`, `pace.factory.FROM_OPT/TO_OPT/DAYS_OPT`.
- Produces `pace.commands.log.app` with sub-groups `weight`, `bp` and command `hydration`:
  - `log weight add KG [--date D] [--fat PCT] [--water PCT] [--muscle KG] [--bone KG] [--bmi X] [--dry-run]`.
    Only weight -> `add_weigh_in(weight=kg, unitKey="kg", timestamp="<D>T12:00:00")` (Garmin wants a timestamp; noon local of the given date avoids day-boundary drift).
    Any extra -> `add_body_composition(timestamp="<D>T12:00:00", weight=kg, percent_fat=, percent_hydration=, muscle_mass=, bone_mass=, bmi=)` (kwargs only for supplied values).
    Payload for dry-run = the kwargs dict plus `"method"`. Invalidate `("get_weigh_ins:", f"get_daily_weigh_ins:{D}")`. `id_of`: `r.get("samplePk")` when dict.
  - `log weight list --from/--to/--days` -> ONE `get_weigh_ins(start, end)` call (cached, TTL 600), default fields `("dateWeightList", "totalAverage")` and pruned; `data` is the raw range payload (not per-day list) because Garmin already returns the range in one document.
  - `log weight delete SAMPLE_PK --date D [--dry-run]` -> `delete_weigh_in(weight_pk=str(pk), cdate=D)`; invalidate as add.
  - `log hydration ML [--date D] [--dry-run]` -> `add_hydration_data(value_in_ml=ml, cdate=D)`; invalidate `(f"get_hydration_data:{D}",)`; `id_of` None.
  - `log bp add SYS DIA PULSE [--notes T] [--date D] [--dry-run]` -> `set_blood_pressure(systolic, diastolic, pulse, timestamp="<D>T12:00:00", notes=notes or "")`; invalidate `("get_blood_pressure:",)`; `id_of`: `r.get("version")` when dict.
  - `log bp list --from/--to/--days` -> ONE `get_blood_pressure(start, end)` call, TTL 600, default fields `("measurementSummaries",)`.
  - Command strings: `"log weight add"`, `"log weight list"`, `"log weight delete"`, `"log hydration"`, `"log bp add"`, `"log bp list"`.
  - Validation: KG in (20, 400), PCT in [0, 100], ML in (-5000, 5000) and != 0, SYS/DIA in (30, 300), PULSE in (20, 250); violations -> `bad_args` naming the argument.

- [ ] **Step 1: Write failing tests**

`tests/test_log_cmd.py`:

```python
import json

import pytest
from typer.testing import CliRunner

from pace.commands.log import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")


@pytest.fixture
def fake_api(monkeypatch):
    state = {"calls": [], "resp": {"samplePk": 123, "version": 9}}

    async def safe_call(method, *args, **kwargs):
        state["calls"].append((method, args, kwargs))
        return True, state["resp"], None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    monkeypatch.setattr("pace.writes.safe_call", safe_call)
    return state


def _doc(res):
    assert res.stdout.count("\n") == 1, res.stdout
    return json.loads(res.stdout)


def test_weight_only_uses_add_weigh_in(fake_api) -> None:
    doc = _doc(runner.invoke(app, ["weight", "add", "66.3", "--date", "2026-09-06"]))
    assert doc["data"] == {"action": "create", "resource": "weight", "id": 123, "echo": {"weight_kg": 66.3, "date": "2026-09-06"}}
    assert fake_api["calls"][0] == ("add_weigh_in", (), {"weight": 66.3, "unitKey": "kg", "timestamp": "2026-09-06T12:00:00"})


def test_weight_with_composition_uses_body_composition(fake_api) -> None:
    doc = _doc(runner.invoke(app, ["weight", "add", "66.3", "--date", "2026-09-06", "--fat", "12.5", "--muscle", "31"]))
    m, a, kw = fake_api["calls"][0]
    assert m == "add_body_composition" and a == ()
    assert kw == {"timestamp": "2026-09-06T12:00:00", "weight": 66.3, "percent_fat": 12.5, "muscle_mass": 31.0}
    assert doc["data"]["echo"]["fat_pct"] == 12.5


def test_weight_dry_run_shows_method_and_kwargs(fake_api) -> None:
    doc = _doc(runner.invoke(app, ["weight", "add", "70", "--date", "2026-09-06", "--dry-run"]))
    assert doc["data"]["dry_run"] is True and doc["data"]["payload"]["method"] == "add_weigh_in"
    assert fake_api["calls"] == []


def test_weight_validation(fake_api) -> None:
    res = runner.invoke(app, ["weight", "add", "5"])
    assert res.exit_code == 2 and "KG" in _doc(res)["error"]["message"]
    res = runner.invoke(app, ["weight", "add", "70", "--fat", "150"])
    assert res.exit_code == 2 and "fat" in _doc(res)["error"]["message"]


def test_weight_list_single_range_call(fake_api) -> None:
    fake_api["resp"] = {"dateWeightList": [{"weight": 66300, "samplePk": 1, "x": None}], "totalAverage": {"weight": 66300}, "junk": 1}
    doc = _doc(runner.invoke(app, ["weight", "list", "--to", "2026-09-06", "--days", "7"]))
    assert fake_api["calls"][0] == ("get_weigh_ins", ("2026-08-31", "2026-09-06"), {})
    assert doc["data"] == {"dateWeightList": [{"weight": 66300, "samplePk": 1}], "totalAverage": {"weight": 66300}}
    assert doc["args"]["from"] == "2026-08-31" and doc["command"] == "log weight list"


def test_weight_delete(fake_api) -> None:
    fake_api["resp"] = None
    doc = _doc(runner.invoke(app, ["weight", "delete", "123", "--date", "2026-09-06"]))
    assert doc["data"] == {"action": "delete", "resource": "weight", "id": None, "echo": {"sample_pk": "123", "date": "2026-09-06"}}
    assert fake_api["calls"][0] == ("delete_weigh_in", (), {"weight_pk": "123", "cdate": "2026-09-06"})


def test_hydration(fake_api) -> None:
    doc = _doc(runner.invoke(app, ["hydration", "500", "--date", "2026-09-06"]))
    assert doc["command"] == "log hydration"
    assert fake_api["calls"][0] == ("add_hydration_data", (), {"value_in_ml": 500.0, "cdate": "2026-09-06"})
    assert doc["data"]["resource"] == "hydration" and doc["data"]["id"] is None
    assert runner.invoke(app, ["hydration", "0"]).exit_code == 2


def test_bp_add_and_list(fake_api) -> None:
    doc = _doc(runner.invoke(app, ["bp", "add", "118", "76", "52", "--date", "2026-09-06", "--notes", "morning"]))
    assert fake_api["calls"][0] == ("set_blood_pressure", (118, 76, 52), {"timestamp": "2026-09-06T12:00:00", "notes": "morning"})
    assert doc["data"] == {"action": "create", "resource": "bp", "id": 9,
                           "echo": {"systolic": 118, "diastolic": 76, "pulse": 52, "date": "2026-09-06", "notes": "morning"}}
    fake_api["resp"] = {"measurementSummaries": [{"systolic": 118}], "from": "x"}
    doc = _doc(runner.invoke(app, ["bp", "list", "--to", "2026-09-06"]))
    assert fake_api["calls"][1] == ("get_blood_pressure", ("2026-09-06", "2026-09-06"), {})
    assert doc["data"] == {"measurementSummaries": [{"systolic": 118}]}
    assert runner.invoke(app, ["bp", "add", "400", "76", "52"]).exit_code == 2
```

- [ ] **Step 2: Run to verify failure** -> ModuleNotFoundError.

- [ ] **Step 3: Implement `pace/commands/log.py`**

```python
"""Health logs: weight (add/list/delete), hydration, blood pressure (add/list)."""

from typing import Any

import typer

from pace.cache import Cache, CacheTracker
from pace.commands._helpers import cached_call
from pace.commands.workout import DRY_RUN_OPT
from pace.dates import parse_date, resolve_range
from pace.envelope import PaceError, Result, fail, run
from pace.factory import BUCKET_OPT, DAYS_OPT, FIELDS_OPT, FROM_OPT, NO_CACHE_OPT, RAW_OPT, TO_OPT, shape_args
from pace.shape import apply
from pace.writes import WriteSpec, run_write

app = typer.Typer(no_args_is_help=True, help="Health logs written to Garmin Connect: weight, hydration, blood pressure.")
weight_app = typer.Typer(no_args_is_help=True, help="Weigh-ins and body composition.")
bp_app = typer.Typer(no_args_is_help=True, help="Blood pressure readings.")
app.add_typer(weight_app, name="weight")
app.add_typer(bp_app, name="bp")

DATE_OPT = typer.Option("today", "--date", "-d", help="YYYY-MM-DD | today | yesterday | -Nd")
_LIST_TTL = 600


def _check(name: str, value: float, lo: float, hi: float, inclusive: bool = False) -> None:
    ok = lo <= value <= hi if inclusive else lo < value < hi
    if not ok:
        raise PaceError("bad_args", f"{name} out of range ({lo}-{hi}): {value}")


def _noon(date_str: str) -> tuple[str, str]:
    d = parse_date(date_str).isoformat()
    return d, f"{d}T12:00:00"


def _ranged_read(cmd: str, method: str, default_fields: tuple[str, ...], from_: str | None, to: str, days: int,
                 fields: str, bucket: str, raw: bool, no_cache: bool) -> None:
    try:
        start, end = resolve_range(from_, to, days)
        f, b, echo = shape_args(fields, bucket, raw)
    except PaceError as err:
        fail(cmd, err)
    s, e = start.isoformat(), end.isoformat()

    async def work() -> Result:
        cache = Cache()
        tracker = CacheTracker()
        try:
            data = await cached_call(method, s, e, cache=cache, ttl=_LIST_TTL, tracker=tracker, refresh=no_cache)
        finally:
            cache.close()
        shaped, report = apply(data or {}, series=(), default_fields=default_fields, fields=f, bucket=b, raw=raw, span_s=0)
        return Result(shaped, tracker.report(), report)

    run(cmd, {"from": s, "to": e, **echo}, work)


@weight_app.command("add")
def weight_add(kg: float = typer.Argument(..., metavar="KG"), date: str = DATE_OPT,
               fat: float | None = typer.Option(None, "--fat", help="Body fat %"),
               water: float | None = typer.Option(None, "--water", help="Body water %"),
               muscle: float | None = typer.Option(None, "--muscle", help="Muscle mass kg"),
               bone: float | None = typer.Option(None, "--bone", help="Bone mass kg"),
               bmi: float | None = typer.Option(None, "--bmi"),
               dry_run: bool = DRY_RUN_OPT) -> None:
    """Record a weigh-in (plus optional body composition)."""
    cmd = "log weight add"
    try:
        _check("KG", kg, 20, 400)
        for name, v in (("fat", fat), ("water", water)):
            if v is not None:
                _check(name, v, 0, 100, inclusive=True)
        d, ts = _noon(date)
    except PaceError as err:
        fail(cmd, err)
    extras = {"percent_fat": fat, "percent_hydration": water, "muscle_mass": muscle, "bone_mass": bone, "bmi": bmi}
    extras = {k: float(v) for k, v in extras.items() if v is not None}
    echo: dict[str, Any] = {"weight_kg": kg, "date": d}
    echo.update({"fat_pct": fat, "water_pct": water, "muscle_kg": muscle, "bone_kg": bone, "bmi": bmi})
    echo = {k: v for k, v in echo.items() if v is not None}
    if extras:
        method, kwargs = "add_body_composition", {"timestamp": ts, "weight": kg, **extras}
    else:
        method, kwargs = "add_weigh_in", {"weight": kg, "unitKey": "kg", "timestamp": ts}
    spec = WriteSpec(resource="weight", action="create", method=method, kwargs=kwargs,
                     payload={"method": method, **kwargs}, echo=echo,
                     invalidate=("get_weigh_ins:", f"get_daily_weigh_ins:{d}"),
                     id_of=lambda r: r.get("samplePk") if isinstance(r, dict) else None)
    run(cmd, {**echo, "dry_run": dry_run}, lambda: run_write(spec, dry_run=dry_run))


@weight_app.command("list")
def weight_list(from_: str | None = FROM_OPT, to: str = TO_OPT, days: int = DAYS_OPT, fields: str = FIELDS_OPT,
                bucket: str = BUCKET_OPT, raw: bool = RAW_OPT, no_cache: bool = NO_CACHE_OPT) -> None:
    """Weigh-ins in a date range (one Garmin call)."""
    _ranged_read("log weight list", "get_weigh_ins", ("dateWeightList", "totalAverage"),
                 from_, to, days, fields, bucket, raw, no_cache)


@weight_app.command("delete")
def weight_delete(sample_pk: str = typer.Argument(..., metavar="SAMPLE_PK", help="samplePk from `log weight list`."),
                  date: str = typer.Option(..., "--date", "-d", help="Date of the weigh-in."),
                  dry_run: bool = DRY_RUN_OPT) -> None:
    """Delete one weigh-in."""
    cmd = "log weight delete"
    try:
        d = parse_date(date).isoformat()
    except PaceError as err:
        fail(cmd, err)
    kwargs = {"weight_pk": sample_pk, "cdate": d}
    spec = WriteSpec(resource="weight", action="delete", method="delete_weigh_in", kwargs=kwargs,
                     payload={"method": "delete_weigh_in", **kwargs}, echo={"sample_pk": sample_pk, "date": d},
                     invalidate=("get_weigh_ins:", f"get_daily_weigh_ins:{d}"))
    run(cmd, {"sample_pk": sample_pk, "date": d, "dry_run": dry_run}, lambda: run_write(spec, dry_run=dry_run))


@app.command("hydration")
def hydration(ml: float = typer.Argument(..., metavar="ML", help="Millilitres; negative subtracts."),
              date: str = DATE_OPT, dry_run: bool = DRY_RUN_OPT) -> None:
    """Add water intake for a day."""
    cmd = "log hydration"
    try:
        if ml == 0:
            raise PaceError("bad_args", "ML must be non-zero")
        _check("ML", ml, -5000, 5000)
        d = parse_date(date).isoformat()
    except PaceError as err:
        fail(cmd, err)
    kwargs = {"value_in_ml": float(ml), "cdate": d}
    spec = WriteSpec(resource="hydration", action="create", method="add_hydration_data", kwargs=kwargs,
                     payload={"method": "add_hydration_data", **kwargs}, echo={"ml": ml, "date": d},
                     invalidate=(f"get_hydration_data:{d}",))
    run(cmd, {"ml": ml, "date": d, "dry_run": dry_run}, lambda: run_write(spec, dry_run=dry_run))


@bp_app.command("add")
def bp_add(systolic: int = typer.Argument(..., metavar="SYS"), diastolic: int = typer.Argument(..., metavar="DIA"),
           pulse: int = typer.Argument(..., metavar="PULSE"), date: str = DATE_OPT,
           notes: str | None = typer.Option(None, "--notes"), dry_run: bool = DRY_RUN_OPT) -> None:
    """Record a blood pressure reading."""
    cmd = "log bp add"
    try:
        _check("SYS", systolic, 30, 300)
        _check("DIA", diastolic, 30, 300)
        _check("PULSE", pulse, 20, 250)
        d, ts = _noon(date)
    except PaceError as err:
        fail(cmd, err)
    kwargs = {"timestamp": ts, "notes": notes or ""}
    echo = {"systolic": systolic, "diastolic": diastolic, "pulse": pulse, "date": d, "notes": notes}
    echo = {k: v for k, v in echo.items() if v is not None}
    spec = WriteSpec(resource="bp", action="create", method="set_blood_pressure", args=(systolic, diastolic, pulse),
                     kwargs=kwargs, payload={"method": "set_blood_pressure", "args": [systolic, diastolic, pulse], **kwargs},
                     echo=echo, invalidate=("get_blood_pressure:",),
                     id_of=lambda r: r.get("version") if isinstance(r, dict) else None)
    run(cmd, {**echo, "dry_run": dry_run}, lambda: run_write(spec, dry_run=dry_run))


@bp_app.command("list")
def bp_list(from_: str | None = FROM_OPT, to: str = TO_OPT, days: int = DAYS_OPT, fields: str = FIELDS_OPT,
            bucket: str = BUCKET_OPT, raw: bool = RAW_OPT, no_cache: bool = NO_CACHE_OPT) -> None:
    """Blood pressure readings in a date range (one Garmin call)."""
    _ranged_read("log bp list", "get_blood_pressure", ("measurementSummaries",),
                 from_, to, days, fields, bucket, raw, no_cache)
```

Wire `log` in `pace/cli.py` like `workout`.

- [ ] **Step 4: Run tests** -> 8 passed; `ruff check` clean; full suite green except the schema drift test.

- [ ] **Step 5: Commit**

```bash
git add pace/commands/log.py pace/cli.py tests/test_log_cmd.py
git commit -m "feat: log weight/hydration/bp commands"
```

---

### Task 6: Schema, contract, README

**Files:**
- Modify: `pace/commands/schema.py`, `docs/CONTRACT.md`, `README.md`, `docs/superpowers/specs/2026-09-04-llm-cli-design.md` (post-hoc note)
- Test: `tests/test_schema.py`

**Interfaces:**
- `pace schema` `data` gains `"workout_dsl": pace.workout.DSL_REFERENCE` and a `"writes"` block: `{"dry_run": "--dry-run on every write: no network, data.payload = exact Garmin body", "result": {"action", "resource", "id", "echo"}, "invalidation": "successful writes drop the affected cache keys", "confirm": "agents must show the planned write and get an explicit yes before calling"}`.
- STATIC_COMMANDS entries for: `workout list` (flags `--limit` + shape flags, default_fields = `WORKOUT_LIST_FIELDS`), `workout show` (id), `workout push` (args `FILE`, flags `--dry-run`, `keyed: "none"`), `workout schedule` (args `WORKOUT_ID DATE`, `--dry-run`), `workout delete` (id, `--dry-run`), `log weight add` (args `KG`, flags `--date --fat --water --muscle --bone --bmi --dry-run`), `log weight list` (range + shape flags), `log weight delete` (args `SAMPLE_PK`, flags `--date --dry-run`), `log hydration` (args `ML`, flags `--date --dry-run`), `log bp add` (args `SYS DIA PULSE`, flags `--date --notes --dry-run`), `log bp list` (range + shape flags). Each entry also gets `"write": true` for the mutating ones (new optional key; read entries omit it).
- `groups` gains `workout` and `log`; the unknown-group check in `schema()` includes them.

- [ ] **Step 1: Extend tests** (append to `tests/test_schema.py`)

```python
def test_writes_and_dsl_in_schema() -> None:
    data = build_schema(None)
    assert data["workout_dsl"]["example"]["name"]
    assert set(data["writes"]) == {"dry_run", "result", "invalidation", "confirm"}
    writes = {c["name"] for c in data["commands"] if c.get("write")}
    assert writes == {"workout push", "workout schedule", "workout delete", "log weight add", "log weight delete",
                      "log hydration", "log bp add"}
    for c in data["commands"]:
        if c.get("write"):
            assert "--dry-run" in c["flags"], c["name"]
    assert {"workout", "log"} <= set(data["groups"])
```

The existing `test_every_cli_command_is_in_schema` is the drift guard: it must pass again after this task (it fails at HEAD of Task 5).

- [ ] **Step 2: Run to verify failure** -> `test_every_cli_command_is_in_schema` and the new test fail.

- [ ] **Step 3: Implement**

In `pace/commands/schema.py`:
- `from pace.commands.workout import WORKOUT_LIST_FIELDS` and `from pace.workout import DSL_REFERENCE`.
- Add the STATIC_COMMANDS entries listed above (copy the existing dict shape; set `"write": True` on mutating ones; `"keyed": "id"` for `workout show/delete`, `"none"` for `push`, `"id+date"` for `schedule`, `"date"` for the two `list` range commands).
- Extend `GROUPS`-merge in `build_schema` with `"workout": "Structured workouts: list, show, push, schedule, delete."` and `"log": "Health logs written to Garmin Connect."`; extend the `known` set in `schema()`.
- Add to the returned dict: `"workout_dsl": DSL_REFERENCE` and the `writes` block quoted above.

`docs/CONTRACT.md`: new section "Writes" containing the safety contract (dry-run shape, success shape, invalidation, deletes limited to workouts/weigh-ins/BP, no --yes) and a pointer "DSL reference: `pace schema` -> `workout_dsl`"; a sentence that write commands never read the cache.
`README.md`: add to "Use": `pace workout push plan.json --dry-run`, `pace workout push plan.json`, `pace workout schedule <id> +1d`, `pace log weight add 66.3`, `pace log hydration 500`; one sentence "Writes require explicit user confirmation in the coaching skill; the CLI itself has no --yes."
Spec post-hoc notes: schedule/delete implemented via `connectapi` (POST `/workout-service/schedule/{id}`, DELETE `/workout-service/workout/{id}`); payload built as plain dicts (not the library's pydantic models) because Garmin's real ids differ from `garminconnect.workout.ConditionType`.

- [ ] **Step 4: Run tests** -> full suite green; `ruff check` clean; `.venv/bin/python -m pace schema log | head -c 600` shows the new entries.

- [ ] **Step 5: Commit**

```bash
git add pace/commands/schema.py tests/test_schema.py docs/CONTRACT.md README.md docs/superpowers/specs/2026-09-04-llm-cli-design.md
git commit -m "docs: schema, contract and README for writes and the workout DSL"
```

---

### Task 7: Live verification (human-gated; controller runs it)

**Precondition:** the controller asks the user for explicit permission to create, schedule and delete ONE test workout and to add/delete ONE weigh-in on their Garmin account. Nothing here runs without that yes.

- [ ] **Step 1: Dry runs (no consent needed)**

```bash
cat > /tmp/claude-1000/-home-matteo-projects-trainscout/8c720074-fc33-4dac-9707-bfee868ff33e/scratchpad/w.json <<'JSON'
{"name": "pace smoke test", "sport": "running", "steps": [
  {"type": "warmup", "duration": "5m"},
  {"repeat": 2, "steps": [{"type": "interval", "duration": "1m", "target": {"pace": "4:30-4:45"}},
                          {"type": "recovery", "duration": "1m"}]},
  {"type": "cooldown", "open": true, "target": {"hr": "z1"}}]}
JSON
.venv/bin/pace workout push /tmp/claude-1000/-home-matteo-projects-trainscout/8c720074-fc33-4dac-9707-bfee868ff33e/scratchpad/w.json --dry-run
.venv/bin/pace log weight add 66.0 --dry-run
```

- [ ] **Step 2: With consent -- workout round trip**

```bash
.venv/bin/pace workout push .../w.json                 # note data.id
.venv/bin/pace workout show <id> --fields workoutName,estimatedDurationInSecs,workoutSegments
.venv/bin/pace workout schedule <id> +1d               # note data.id (workoutScheduleId)
.venv/bin/pace workout delete <id>                     # deleting the workout also removes its schedule entries
.venv/bin/pace workout list --limit 3                  # smoke test gone
```

Expected: every call `ok: true`; `show` reflects the pace target as `pace.zone` with `targetValueOne > targetValueTwo`; if `schedule` returns `endpoint_unavailable`/`not_found`, record the exact error and open a follow-up (the command stays, documented as unverified) rather than blocking the plan.

- [ ] **Step 3: With consent -- weigh-in round trip**

```bash
.venv/bin/pace log weight add 66.0 --date today        # note data.id (samplePk)
.venv/bin/pace log weight list --days 1                # entry present
.venv/bin/pace log weight delete <samplePk> --date today
.venv/bin/pace log weight list --days 1 --no-cache     # entry gone
```

- [ ] **Step 4: Record**

Append the observed ids/paths and any endpoint quirks to `docs/CONTRACT.md` "Writes" (e.g. the schedule response shape) and commit: `git commit -am "docs: live-verified workout and weigh-in round trips"`.

---

## Self-review notes

- Spec 7 safety contract: Task 2 (dry-run, result shape, invalidation via Task 1), no `--yes` anywhere, deletes limited (Tasks 4-5).
- Spec 7 `workout` table: Task 4 (list/show/push/schedule/delete; schedule/delete via `connectapi`, verified live in Task 7).
- Spec 7 DSL: Task 3; reference emitted by schema in Task 6.
- Spec 7 `log` table: Task 5 (`log weight add` replaces the spec's positional-only `log weight KG` because Typer needs a subcommand to coexist with `list`/`delete` -- noted for the skill).
- Deviation: Garmin payload built as plain dicts, not `garminconnect.workout` pydantic models -- the library's `ConditionType` ids disagree with Garmin for `distance`/`lap.button` (verified live).
- Type consistency: `WriteSpec` fields used identically in Tasks 2, 4, 5; `DRY_RUN_OPT` defined in Task 4 and imported by Task 5; `WORKOUT_LIST_FIELDS` defined in Task 4 and imported by Task 6.
