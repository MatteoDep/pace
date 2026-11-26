# Dashboard export (PR A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `pace export` runs a per-athlete recipe of read commands in one process, within a call and time budget, and writes one bundle file the dashboard reads; plus the three endpoint changes the first sections need and the coach-side procedure.

**Architecture:** The export invokes each dataset's own click command in-process and parses the envelope it prints, so a recipe line returns exactly what the shell command returns.
A process-wide budget sits in `pace.api.safe_call`, where every Garmin call passes; past it a call fails with the new error code `deferred` and the dataset falls back to its previous bundle section.
The rate limiter is made safe across event loops, since every command runs its own `asyncio.run`.

**Tech Stack:** Python 3.14, Typer/click, pytest (pytest-asyncio), garminconnect 0.2.38.

**Spec:** `docs/superpowers/specs/2026-11-26-dashboard-recipes-design.md` (Part 1 and the PR A items of Part 3; Part 2 is PR B, out of scope here).

## Global Constraints

- Work in the worktree `~/projects/trainscout-wt/dashboard-export` (branch `dashboard-export`); never in `~/projects/trainscout`, which the installed `pace` and the coach skill use live.
- Run tests with `~/projects/trainscout/.venv/bin/python -m pytest` from the worktree root (it imports the worktree's `pace`); never `uv sync`.
- Every pace call prints exactly one JSON envelope on stdout; nothing else goes to stdout.
- Code reads `pace.config.PACE_HOME` at call time (tests monkeypatch it), never a copy taken at import.
- No real Garmin ids in code, tests or docs: use synthetic ids (`12345678901`, uuid `0123456789abcdef0123456789abcdef`).
- Docs in markdown: one sentence per line; ASCII only (no em/en dashes, curly quotes, arrows or the ellipsis character).
- Keep `docs/CONTRACT.md`, `pace schema` and `.claude/skills/pace/reference.md` in step with every behaviour change in the same task (`tests/test_skill.py` checks that every command and flag named in the skill files exists in `pace schema`, and that every command appears in `reference.md`).
- Commits: conventional prefix (`feat:`, `fix:`, `docs:`), no Claude attribution or trailers.
- Values from the spec: default budget 60 calls and 480 s; dataset id pattern `^[A-Za-z0-9_-]{1,64}$`; recipe `version` 1; refused recipe flags `--export`, `--out`, `--out-dir`, `--save`, `--no-cache`, `--help`; recipe path `$PACE_HOME/dashboard/recipe.json`; bundle path `$PACE_HOME/dashboard/bundle.json`.

## Review Focus

- A recipe line whose command fans out over days (more than 3 calls) followed by another command in the same process: must not raise "bound to a different event loop" (Task 1 test `test_limiter_and_slot_survive_a_second_event_loop`).
- An export whose budget runs out halfway through a date-keyed dataset: the days that were not fetched come from the previous bundle by date, and the rest of the bundle is written (Task 4 test `test_deferred_days_merge_from_previous_bundle`).
- A recipe naming a command that writes, or an exportable command with a writing flag: refused by `--check` before any call (Task 3 tests `test_allowlist_*`).
- A previous bundle that is missing, unreadable or from an older recipe (dataset ids that no longer exist, or new ones): the export still writes a complete bundle (Task 4 test `test_previous_bundle_missing_or_corrupt`).
- `--every` sampling must keep the cache warm from one day to the next: only the newest day is new (Task 2 test `test_sample_days_grid_is_stable_day_to_day`).

---

### Task 1: Rate limiter across event loops, call and time budget, `deferred`

**Files:**
- Modify: `pace/ratelimit.py`, `pace/api.py`, `pace/envelope.py`, `tests/conftest.py`, `docs/CONTRACT.md` (error code table, "Rate limits and cost")
- Test: `tests/test_ratelimit.py`, `tests/test_api_errors.py`

**Interfaces:**
- Produces (in `pace/ratelimit.py`):
  - `class BudgetExhausted(Exception)` with `reason: str`.
  - `@dataclass class Budget: max_calls: int; deadline: float; calls: int = 0; stopped: str | None = None` (deadline on the `time.monotonic()` clock), with `check(self, wait: float = 0.0) -> None` raising `BudgetExhausted` when `stopped` is set, `calls >= max_calls`, or `time.monotonic() + wait > deadline`, and `stop(self, code: str) -> None` setting `stopped`.
  - `RateLimiter.acquire(self, budget: Budget | None = None) -> None`: unchanged waiting rules. With a budget, under the lock and in each pass of the wait loop it calls `budget.check(wait)` before sleeping or registering, and increments `budget.calls` when it registers the call, so calls gathered by a fan-out are counted one by one. One `asyncio.Lock` per running event loop (kept in a `weakref.WeakKeyDictionary` keyed by the loop); the call history is shared by all loops.
  - `def slot() -> asyncio.Semaphore`: the `CONCURRENCY`-slot semaphore of the running loop, one per loop. The module-level `concurrency` semaphore is removed.
  - `STOP_CODES = ("rate_limited", "network")`.
  - `@contextmanager def budget(max_calls: int, max_seconds: float) -> Iterator[Budget]`: makes it the active budget for the block, always cleared on exit; nesting raises `RuntimeError` and leaves the outer budget active.
  - `def active_budget() -> Budget | None`.
- Produces (in `pace/api.py`): `safe_call` passes `active_budget()` to `limiter.acquire(budget=...)`; on `BudgetExhausted` it returns `(False, None, ApiError("deferred", f"Not sent: {reason}"))` without calling Garmin; when a call fails with a code in `STOP_CODES` and a budget is active, it calls `budget.stop(code)`. Without an active budget, behaviour is unchanged. `async with slot():` replaces `async with concurrency:`.
- Produces (in `pace/envelope.py`): `HINTS["deferred"] = "The export's call or time budget ran out before this call; nothing was sent to Garmin. The next export continues from the cache."` (exit code 1, the default).
- Produces (in `tests/conftest.py`): an autouse fixture that sets `pace.api.get_api` to a function raising `AssertionError("test reached the real Garmin client: monkeypatch pace.api.get_api")`; tests that fake the client patch it again on top.

- [ ] **Step 1: Write the failing tests** in `tests/test_ratelimit.py`:

```python
def test_limiter_and_slot_survive_a_second_event_loop() -> None:
    # min_interval > 0 so acquire waits on the lock (a 0.0 interval never binds the lock to a loop)
    rl = RateLimiter(min_interval=0.01, per_minute=1000, per_hour=1000, per_day=1000)
    async def burst():
        async def one():
            async with slot():
                await rl.acquire()
                await asyncio.sleep(0.01)
        await asyncio.gather(*(one() for _ in range(6)))   # contends: 6 > CONCURRENCY
    asyncio.run(burst())
    asyncio.run(burst())                                    # raised "bound to a different event loop" before
    assert len(rl._history) == 12                           # one history across loops

def test_acquire_raises_instead_of_waiting_past_deadline() -> None:
    rl = RateLimiter(min_interval=5.0, per_minute=1000, per_hour=1000, per_day=1000)
    async def go():
        await rl.acquire()
        with pytest.raises(BudgetExhausted):
            await rl.acquire(budget=Budget(max_calls=10, deadline=time.monotonic() + 1.0))   # would wait ~5 s
    asyncio.run(go())

def test_budget_context_is_cleared_and_not_nested() -> None:
    with budget(2, 60) as b:
        assert active_budget() is b
        with pytest.raises(RuntimeError):
            with budget(1, 1):
                pass
        assert active_budget() is b
    assert active_budget() is None
```

And in `tests/test_api_errors.py` (real `safe_call`, fake client: a `fake_client` fixture monkeypatching `pace.api.get_api` to return an object whose methods record calls and can be told to raise, and `pace.api.limiter` to `RateLimiter(min_interval=0.01, per_minute=1000, per_hour=1000, per_day=1000)`):

```python
def test_budget_caps_a_gathered_fanout(fake_client) -> None:
    async def fan():
        return await asyncio.gather(*(safe_call("get_x") for _ in range(5)))
    with budget(2, 60) as b:
        res = asyncio.run(fan())
    assert sum(ok for ok, _, _ in res) == 2 and fake_client.calls == 2 and b.calls == 2
    assert all(err.code == "deferred" for ok, _, err in res if not ok)

def test_budget_past_deadline_defers_without_calling(fake_client) -> None:
    with budget(10, 0):
        ok, _, err = asyncio.run(safe_call("get_x"))
    assert not ok and err.code == "deferred" and fake_client.calls == 0

def test_rate_limited_ends_the_budget(fake_client) -> None:
    fake_client.raise_next(GarminConnectTooManyRequestsError("429"))
    async def two():
        return [await safe_call("get_x"), await safe_call("get_x")]
    with budget(10, 60) as b:
        first, second = asyncio.run(two())
    assert first[2].code == "rate_limited" and second[2].code == "deferred"
    assert b.stopped == "rate_limited" and fake_client.calls == 1

def test_no_budget_no_change(fake_client) -> None:
    assert all(asyncio.run(safe_call("get_x"))[0] for _ in range(3)) and fake_client.calls == 3
```

- [ ] **Step 2: Run them to verify they fail**

Run: `~/projects/trainscout/.venv/bin/python -m pytest tests/test_ratelimit.py tests/test_api_errors.py -q`
Expected: FAIL (`ImportError: cannot import name 'slot'` / `'budget'`).

- [ ] **Step 3: Implement** the interfaces above in `pace/ratelimit.py`, `pace/api.py`, `pace/envelope.py`, and the conftest guard.
  `test_schema.py::test_limits_exit_codes_and_notes_are_declared` imports `CONCURRENCY` and `limiter`; keep both names.

- [ ] **Step 4: Update `docs/CONTRACT.md`**: add `deferred` (exit 1) to the error code table ("only inside `pace export`, when its call or time budget ran out; nothing was sent"), and one paragraph in "Rate limits and cost" on the export budget (cap on calls checked under the limiter lock, deadline that includes limiter waits, ended by `rate_limited` or `network`, cache hits free).

- [ ] **Step 5: Run the whole suite**

Run: `~/projects/trainscout/.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add pace/ratelimit.py pace/api.py pace/envelope.py docs/CONTRACT.md tests/conftest.py tests/test_ratelimit.py tests/test_api_errors.py
git commit -m "feat: rate limiter safe across event loops; export call and time budget with deferred error"
```

---

### Task 2: Endpoint changes: `--every`, race predictions over a range, `load --rows`

**Files:**
- Modify: `pace/dates.py`, `pace/spec.py`, `pace/factory.py`, `pace/commands/load.py`, `pace/commands/schema.py`, `docs/CONTRACT.md`, `.claude/skills/pace/reference.md`
- Test: `tests/test_dates.py`, `tests/test_factory.py`, `tests/test_load_cmd.py`, `tests/test_schema.py`

**Interfaces:**
- Produces:
  - `pace.dates.sample_days(start: date, end: date, every: int) -> list[date]`: ascending, no duplicates; the days `d` in `[start, end]` with `d.toordinal() % every == 0`, plus `end`; `every == 1` gives every day. Raises `PaceError("bad_args", "--every must be >= 1")` when `every < 1`.
  - `EndpointSpec.dated: Literal[None, "single", "range", "span"]` and new field `span_args: tuple[Any, ...] = ()`. `training race-predictions` becomes `dated="span", span_args=("daily",)`.
  - Factory, for `dated` `"single"` or `"range"`: option `--every` (int, default 1) feeding `sample_days`; `args` echo gains `"every"` (update the exact `args` assertion in `tests/test_factory.py::test_dated_range_ascending_and_args_echo` to include `"every": 1`).
  - Factory, for `dated="span"`: options `--from` (None), `--to` (None), `--days` (None). With none of the three, the command calls the method with no dates, exactly as today. With any of them, the range resolves through `resolve_range(from_, to or "today", days if days is not None else 1)`, more than `MAX_DAYS` days is `bad_args`, and ONE `cached_call(spec.method, *prefix, start_iso, end_iso, *spec.span_args, ttl=ttl_for_day(end, today_ttl(spec)))` returns the rows, shaped with `apply` as a list. `args` echo: `from`, `to` (null without a range) plus the shaping echo.
  - `pace load --rows`: `data` is the list of dashboard rows `--export` writes today (both sports, `week`, `day` and `next` rows); `--rows` with `--export` or with `--sport` is `bad_args`. Share one helper `_dashboard_rows(acts, sessions, plan_data, *, start, end, today) -> list[dict]` between the two (`sessions` feeds `aggregate`, `plan_data["sessions"]` feeds `_next_row`, as today).
  - `pace schema`: `_keyed` returns `"date"` for span; flags are `RANGE_FLAGS + ["--every"] + SHAPE_FLAGS` for single/range and `RANGE_FLAGS + SHAPE_FLAGS` for span; `GLOBAL_FLAGS["--every"]` describes it; the `load` entry lists `--rows`.

- [ ] **Step 1: Write the failing tests**

`tests/test_dates.py`:
```python
def test_sample_days_grid_and_last_day() -> None:
    s, e = date(2026, 11, 1), date(2026, 11, 26)
    got = sample_days(s, e, 7)
    assert got[-1] == e and all(d.toordinal() % 7 == 0 for d in got[:-1])
    assert got == sorted(set(got)) and all(s <= d <= e for d in got)
    assert sample_days(s, e, 1) == [s + timedelta(days=i) for i in range(26)]
    assert sample_days(e, e, 7) == [e]

def test_sample_days_grid_is_stable_day_to_day() -> None:
    s, e = date(2026, 8, 1), date(2026, 11, 26)
    today = set(sample_days(s, e, 7)); tomorrow = set(sample_days(s + timedelta(1), e + timedelta(1), 7))
    assert len(tomorrow - today) <= 2   # the new last day, plus a grid day it may cross

def test_sample_days_rejects_zero() -> None:
    with pytest.raises(PaceError): sample_days(date(2026, 1, 1), date(2026, 1, 2), 0)
```

`tests/test_factory.py` (with the existing `fake_api` fixture and `_app`/`_invoke` helpers):
```python
def test_every_calls_only_grid_days_and_the_last(fake_api) -> None:
    code, doc, _ = _invoke(_app(DATED), "--to", "2026-01-15", "--days", "15", "--every", "7")
    called = [a[0] for _, a in fake_api["calls"]]
    assert code == 0 and called == [d.isoformat() for d in sample_days(date(2026, 1, 1), date(2026, 1, 15), 7)]
    assert doc["args"]["every"] == 7

def test_every_zero_is_bad_args(fake_api) -> None:
    code, doc, _ = _invoke(_app(DATED), "--every", "0")
    assert code == 2 and doc["error"]["code"] == "bad_args" and fake_api["calls"] == []

SPAN = EndpointSpec("training", "race-predictions", "h", "get_race_predictions", 3600, dated="span", span_args=("daily",))

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
```

`tests/test_load_cmd.py` (existing `api` fixture and `_run`):
```python
def test_rows_returns_the_export_rows(api, tmp_path) -> None:
    code, doc = _run("--weeks", "2", "--rows")
    _, exp = _run("--weeks", "2", "--export", str(tmp_path / "l.json"))
    assert code == 0 and doc["data"] == json.loads((tmp_path / "l.json").read_text())["rows"]
    assert {r["table"] for r in doc["data"]} >= {"week", "day"}

@pytest.mark.parametrize("extra", [["--sport", "all"], ["--export", "x.json"]])
def test_rows_refuses_sport_and_export(api, extra) -> None:
    code, doc = _run("--rows", *extra)
    assert code == 2 and doc["error"]["code"] == "bad_args"
```

`tests/test_schema.py`:
```python
def test_every_and_span_flags_in_schema() -> None:
    cmds = {c["name"]: c for c in build_schema(None)["commands"]}
    assert "--every" in cmds["wellness sleep"]["flags"] and "--every" in cmds["training fitness-age"]["flags"]
    rp = cmds["training race-predictions"]
    assert rp["keyed"] == "date" and {"--from", "--to", "--days"} <= set(rp["flags"]) and "--every" not in rp["flags"]
    assert "--rows" in cmds["load"]["flags"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `~/projects/trainscout/.venv/bin/python -m pytest tests/test_dates.py tests/test_factory.py tests/test_load_cmd.py tests/test_schema.py -q`
Expected: FAIL (`sample_days` missing, unknown option `--every`, `--rows`).

- [ ] **Step 3: Implement** the interfaces above.

- [ ] **Step 4: Docs.** `docs/CONTRACT.md`: `--every` in "Shaping flags" or "Partial failure in a range" (where per-day calls are described), race predictions over a range, and `--rows` in the Load "Export" section. `.claude/skills/pace/reference.md` command map: note `--every` on date-keyed reads, the race-predictions range, and `load --rows`.

- [ ] **Step 5: Run the whole suite**, expected all pass.

- [ ] **Step 6: Commit**

```bash
git add pace/dates.py pace/spec.py pace/factory.py pace/commands/load.py pace/commands/schema.py docs/CONTRACT.md .claude/skills/pace/reference.md tests/
git commit -m "feat: --every sampling on per-day reads, race predictions over a range, load --rows"
```

---

### Task 3: Recipe model, validation and the exportable allowlist

**Files:**
- Create: `pace/recipe.py`
- Modify: `pace/commands/schema.py`
- Test: `tests/test_recipe.py`, `tests/test_schema.py`

**Interfaces:**
- Consumes: `pace schema` command entries (`build_schema(None)["commands"]`).
- Produces (in `pace/commands/schema.py`):
  - Every command entry carries `"exportable": bool`: `True` for every registry entry (`_entry`), and for static entries exactly those named in `EXPORTABLE_STATIC = frozenset({"activity list", "activity show", "activity gear", "activity splits", "activity details", "activity weather", "activity last", "load", "plan show", "plan status", "wellness sleep-range", "log weight list", "log bp list", "workout list", "workout show", "workout calendar", "zones show"})`; `False` for every other entry.
  - `EXPORT_REFUSED_FLAGS = ("--export", "--out", "--out-dir", "--save", "--no-cache", "--help")`.
  - A top-level `"export"` block: `{"recipe_path": "$PACE_HOME/dashboard/recipe.json", "bundle_path": "$PACE_HOME/dashboard/bundle.json", "recipe_version": 1, "refused_flags": [...], "max_calls_default": 60, "max_seconds_default": 480}`.
- Produces (in `pace/recipe.py`):
  - Constants `RECIPE_VERSION = 1`, `ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")`, `MAX_DATASETS = 50`, `MAX_FOREACH_LIMIT = 100`, `EXPORT_MAX_CALLS = 60`, `EXPORT_MAX_SECONDS = 480` (schema reads the last two from here; `DEFAULT_MAX_CALLS` is already the zones estimate default in `schema.py`).
  - `@dataclass(frozen=True) class Foreach: dataset: str; limit: int`.
  - `@dataclass(frozen=True) class Dataset: id: str; command: str; argv: tuple[str, ...]; rows: str | None; columns: dict[str, str]; foreach: Foreach | None` with property `placeholders -> tuple[str, ...]` (the names of `argv` items of the exact form `{name}`).
  - `def parse_recipe(obj: Any, commands: dict[str, dict]) -> list[Dataset]`: `commands` maps command name to its schema entry. Raises `PaceError("bad_args", ...)` whose message starts with the dataset's id (or `datasets[i]` when the id itself is bad) and names the rule. Rules: top level is an object with exactly `version` (== 1) and `datasets` (a non-empty list of at most `MAX_DATASETS` objects); dataset keys only `id`, `command`, `argv`, `rows`, `columns`, `foreach`; `id` matches `ID_RE` and is unique; `command` is a schema command with `exportable` true; `argv` a list of strings, none of them a refused flag nor starting with a refused flag followed by `=`; `rows` a non-empty string; `columns` a non-empty object of string to non-empty string; `foreach` an object with exactly `dataset` (an id defined earlier in the list) and `limit` (int 1..`MAX_FOREACH_LIMIT`); placeholders only with `foreach`, each naming a key of the parent's `columns`; a `foreach` dataset has at least one placeholder; a `{` or `}` anywhere else in `argv` is refused.
  - `DEFAULT_RECIPE: dict`: the spec's default recipe, with the `load` line listing every column of the `load --rows` rows by identity (`"table": "table"`, `"week": "week"`, ...; take the column list from `pace/commands/load.py`).
  - `def recipe_path() -> Path` and `def bundle_path() -> Path` under `pace.config.PACE_HOME / "dashboard"`, read at call time.

- [ ] **Step 1: Write the failing tests** in `tests/test_recipe.py`. Build `commands` from `build_schema(None)` for the real allowlist; a helper `_ds(**kw)` returns a minimal valid dataset dict (`{"id": "rhr", "command": "wellness sleep-range", "argv": ["--days", "7"], "columns": {"date": "date"}}` merged with `kw`).

```python
def test_default_recipe_is_valid() -> None:
    assert [d.id for d in parse_recipe(DEFAULT_RECIPE, COMMANDS)][:2] == ["load", "rhr"]

@pytest.mark.parametrize("bad, needle", [
    ({"version": 2, "datasets": [_ds()]}, "version"),
    ({"version": 1, "datasets": []}, "datasets"),
    ({"version": 1, "datasets": [_ds(), _ds()]}, "duplicate"),
    ({"version": 1, "datasets": [_ds(id="a b")]}, "id"),
    ({"version": 1, "datasets": [_ds(extra=1)]}, "extra"),
    ({"version": 1, "datasets": [_ds(columns={})]}, "columns"),
    ({"version": 1, "datasets": [_ds(argv=["{date}"])]}, "foreach"),
    ({"version": 1, "datasets": [_ds(id="c", command="activity show", argv=["{id}"], foreach={"dataset": "later", "limit": 5})]}, "later"),
    ({"version": 1, "datasets": [_ds(), _ds(id="c", command="activity show", argv=["{nope}"], foreach={"dataset": "rhr", "limit": 5})]}, "nope"),
    ({"version": 1, "datasets": [_ds(), _ds(id="c", command="activity show", argv=["x{date}"], foreach={"dataset": "rhr", "limit": 5})]}, "{"),
    ({"version": 1, "datasets": [_ds(), _ds(id="c", command="activity show", argv=["{date}"], foreach={"dataset": "rhr", "limit": 0})]}, "limit"),
])
def test_recipe_rules(bad, needle) -> None:
    with pytest.raises(PaceError) as e: parse_recipe(bad, COMMANDS)
    assert e.value.code == "bad_args" and needle in e.value.message

@pytest.mark.parametrize("command, argv", [
    ("gear link", []), ("zones estimate", []), ("diary show", []), ("chart", []),
    ("load", ["--export", "x.json"]), ("load", ["--export=x.json"]), ("wellness sleep-range", ["--no-cache"]),
    ("activity list", ["--help"]),
])
def test_allowlist_refuses_writes_and_writing_flags(command, argv) -> None:
    with pytest.raises(PaceError) as e:
        parse_recipe({"version": 1, "datasets": [_ds(command=command, argv=argv)]}, COMMANDS)
    assert e.value.code == "bad_args"

def test_allowlist_unmarked_command_is_refused() -> None:
    cmds = {**COMMANDS, "new thing": {"name": "new thing", "exportable": False}}
    with pytest.raises(PaceError): parse_recipe({"version": 1, "datasets": [_ds(command="new thing")]}, cmds)

def test_foreach_placeholders() -> None:
    ds = parse_recipe({"version": 1, "datasets": [
        _ds(id="acts", command="activity list", argv=["--limit", "3"], columns={"id": "activityId"}),
        _ds(id="eff", command="activity show", argv=["{id}"], foreach={"dataset": "acts", "limit": 3}, columns={"rpe": "rpe"})]}, COMMANDS)
    assert ds[1].placeholders == ("id",) and ds[1].foreach == Foreach("acts", 3)
```

`tests/test_schema.py`:
```python
def test_exportable_flags_and_export_block() -> None:
    doc = build_schema(None); cmds = {c["name"]: c for c in doc["commands"]}
    assert all(isinstance(c["exportable"], bool) for c in cmds.values())
    assert cmds["training fitness-age"]["exportable"] and cmds["load"]["exportable"]
    assert not cmds["gear link"]["exportable"] and not cmds["diary show"]["exportable"]
    assert not any(c["exportable"] for c in cmds.values() if c.get("write") or c.get("local_write"))
    assert doc["export"]["max_calls_default"] == 60 and "--no-cache" in doc["export"]["refused_flags"]
```

- [ ] **Step 2: Run them to verify they fail** (`ModuleNotFoundError: pace.recipe`).

- [ ] **Step 3: Implement** `pace/recipe.py` and the schema changes. Check each default-recipe path against the command's real output shape documented in the spec ("Default recipe (v1)"); the live check happens after the tasks.
  Add one sentence to `docs/CONTRACT.md` near the schema description: every command entry carries `exportable`, and the `export` block lists the refused flags (the full export section comes in Task 4).

- [ ] **Step 4: Run the whole suite**, expected all pass.

- [ ] **Step 5: Commit**

```bash
git add pace/recipe.py pace/commands/schema.py docs/CONTRACT.md tests/test_recipe.py tests/test_schema.py
git commit -m "feat: dashboard recipe validation and the exportable command allowlist"
```

---

### Task 4: The export runner and `pace export`

**Files:**
- Create: `pace/exporter.py`, `pace/commands/export.py`
- Modify: `pace/cli.py`, `pace/commands/schema.py` (static entry for `export`, group text), `docs/CONTRACT.md` (new `## Export` section), `.claude/skills/pace/reference.md` (command map rows for `pace export`)
- Test: `tests/test_exporter.py`

**Interfaces:**
- Consumes: Task 1 `ratelimit.budget`, `active_budget`, error code `deferred`; Task 3 `parse_recipe`, `Dataset`, `DEFAULT_RECIPE`, `recipe_path`, `bundle_path`, `EXPORT_MAX_CALLS`, `EXPORT_MAX_SECONDS`.
- Produces (in `pace/exporter.py`):
  - `def click_command(name: str) -> click.Command`: the leaf command for `"wellness sleep-range"`, walking `typer.main.get_command(pace.cli.app)` (import `pace.cli` inside the function: `pace.cli` imports this module's command).
  - `def check_argv(datasets: list[Dataset]) -> None`: parses each dataset's `argv` with `click_command(ds.command).make_context(...)` (placeholders replaced by `"0"`), no network; a click usage error becomes `PaceError("bad_args", "<id>: <click message>")`.
  - `def invoke(name: str, argv: list[str]) -> dict`: runs `click_command(name).main(args=argv, prog_name=f"pace {name}", standalone_mode=False)` with stdout captured (`contextlib.redirect_stdout`) and `sys.stdin` swapped for an empty `io.StringIO` (a credentials prompt must fail, never wait), and returns the parsed envelope (the last line printed). A `click.ClickException` becomes an error envelope with code `bad_args`; any other exception, or no output at all, becomes code `internal`; a return value of 130 (Typer turns Ctrl-C into `Exit(130)`) raises `KeyboardInterrupt` so the export stops. It never calls the root group (its callback would reset `--verbose`).
  - `def extract(data: Any, ds: Dataset) -> tuple[list[dict], list[str]]`: returns (rows, failed day ISO dates). Rows: `data` at the `ds.rows` path when given (a missing path or a `null` there gives no rows: pace prunes empty lists), else `data` itself; a list gives its items, an object one row; a non-dict item counts as `{"value": item}`. An item that is exactly `{"date": str, "error": dict}` is a failed day, not a row. Each row becomes `{column: value at path or None}` in `ds.columns` order, using `pace.shape._get_path`.
  - `def run_recipe(datasets: list[Dataset], *, previous: dict | None, max_calls: int, max_seconds: float) -> tuple[dict, dict]`: returns `(bundle, summary)` per the spec's "Bundle" and "Failures" sections; raises `PaceError("auth_required", ...)` as soon as a dataset's envelope has that code. Datasets run in order inside one `ratelimit.budget(max_calls, max_seconds)`; per dataset, `calls` is the budget's `calls` delta and `cache_oldest_age_s` comes from the envelope's `cache`. Every dataset runs even after the budget ends (cache hits stay free; misses come back `deferred` from `safe_call`). Fallbacks, all against `previous["datasets"][id]` and only when its `meta.columns` equals this line's column names: whole failure keeps its rows with `stale: true`, its `generated`, `reason` (`"deferred"` for code `deferred`, else `"error"`) and `error: {"code": code}`; failed days are replaced by the previous rows whose date column (the column mapped from path `date`) matches, else dropped, and counted in `day_errors`; a failed or deferred `foreach` child is recorded in `meta.errors` as `{"parent": {...}, "code": code}` and the previous rows whose `parent.<col>` values match are kept. `foreach` children read the parent section's rows as written in this run (stale ones included), the first `limit` of them; a parent row whose placeholder value is null or whose string form starts with `-` is not run and gets an error entry with code `bad_args`; each child row gains `parent.<col>` keys. The bundle carries error codes only; the summary carries `{code, message}` (message cut to 300 characters). `meta.command` is `" ".join([ds.command, *ds.argv])`, `meta.columns` the column names in order, and `generated` the time the dataset ran (`datetime.now().astimezone().isoformat(timespec="seconds")`).
- Produces (in `pace/commands/export.py`): `pace export [--recipe PATH] [--out PATH] [--max-calls N] [--max-seconds N] [--check] [--init]`:
  - The body is synchronous and emits with `envelope.emit(envelope.ok(...))` / `envelope.fail(...)`; it never uses `envelope.run`, since every dataset runs its own `asyncio.run`. It wraps its own unexpected exceptions into an `internal` envelope (stdout stays one JSON document) and a `KeyboardInterrupt` into the `internal` "Aborted by user." envelope with exit 130, writing no bundle.
  - `pace.store.save_json` gains `sort_keys: bool = True`; the recipe and the bundle are written with `sort_keys=False`, so `id` leads each recipe line and column order survives.
  - `--init`: writes `DEFAULT_RECIPE` to `--recipe` or `recipe_path()`; `bad_args` if the file exists; `data = {"path", "datasets": n}`.
  - Otherwise reads the recipe (missing default path: `DEFAULT_RECIPE`, labelled `"default"`; a missing `--recipe` path or invalid JSON is `bad_args`), `parse_recipe`, `check_argv`. The `Recipe` label in the bundle is `default` or the path with the home directory as `~`.
  - `--check`: `data = {"recipe", "datasets": [ids], "valid": true}`; no network, no file written.
  - Run: `--max-calls` < 1 or `--max-seconds` < 1 is `bad_args`; without Garmin tokens (`pace.auth.auth_status()["authenticated"]` false) it is `auth_required` before any dataset; reads the previous bundle at the output path (`json` load; unreadable or not an object counts as none), `run_recipe`, then `save_json(out, bundle, sort_keys=False)`; `data` = the summary: `{"path", "recipe", "bytes", "calls", "seconds", "datasets": {id: {rows, calls, stale, reason, error, day_errors, errors}}}`.
  - Envelope `args`: `{"recipe", "out", "max_calls", "max_seconds", "check", "init"}`.
  - Registered in `pace/cli.py` as `app.command("export", help="Run the dashboard recipe and write the bundle file.")`; schema static entry `{"name": "export", "keyed": "none", "args": [], "flags": ["--recipe", "--out", "--max-calls", "--max-seconds", "--check", "--init"], "exportable": False, ...}`, and `"export"` in the `groups` text and the `known` groups of `schema()`.

- [ ] **Step 1: Write the failing tests** in `tests/test_exporter.py`. One fixture, `garmin`, runs the real `safe_call`, limiter and budget against a fake client: it patches `pace.api.get_api` (a fake whose methods return per-method payloads, can fail per argument with a given exception, and record calls), `pace.api.limiter` (`RateLimiter(min_interval=0.01, per_minute=1000, per_hour=1000, per_day=1000)`) and `pace.auth.auth_status` (authenticated), plus a tmp `PACE_HOME` and `CACHE_DB` (as `tests/test_load_cmd.py` does). `activity list` without a range goes through `pace.api.paginate`, so patching `_helpers.safe_call` alone would miss it; the conftest guard from Task 1 catches any leak. Use registry commands (`training fitness-age`, `gear list`, `gear activities`) and `activity list` so payloads are simple.

```python
def test_two_fanout_datasets_in_one_process(garmin) -> None:
    # 7-day fan-outs contend for the semaphore and the limiter lock; the second dataset used to crash
    b, s = run_recipe(_parse([_fa("fa1", days=7), _fa("fa2", days=7)]), previous=None, max_calls=60, max_seconds=60)
    assert len(b["datasets"]["fa1"]["rows"]) == 7 and len(b["datasets"]["fa2"]["rows"]) == 7

def test_extract_paths() -> None:
    ds = _dataset(rows="a.list", columns={"x": "k.v", "y": "missing"})
    assert extract({"a": {"list": [{"k": {"v": 1}}, 5]}}, ds)[0] == [{"x": 1, "y": None}, {"x": None, "y": None}]
    assert extract({"a": {"list": None}}, ds) == ([], [])
    assert extract({"b": 1}, ds) == ([], [])                                             # pruned empty list
    assert extract({"k": {"v": 2}}, _dataset(columns={"x": "k.v"}))[0] == [{"x": 2}]     # object -> one row
    assert extract([{"date": "2026-01-02", "error": {"code": "network"}}], _dataset(columns={"d": "date"})) == ([], ["2026-01-02"])

def test_failed_dataset_keeps_previous_rows_stale(garmin) -> None: ...
    # previous bundle has rows for "shoes" with the same meta.columns; gear list now fails with a 404 ->
    # same rows, stale True, previous generated, reason "error", bundle error == {"code": "not_found"}, summary has the message

def test_previous_rows_not_reused_after_a_column_change(garmin) -> None: ...
    # same failure, but the previous section's meta.columns differ -> no rows, stale True

def test_failed_days_merge_from_previous_bundle(garmin) -> None: ...
    # fitness-age over 3 days, day 2 fails with a 404: day 2 row comes from previous bundle by date; day_errors == 1; stale False

def test_deferred_days_merge_from_previous_bundle(garmin) -> None: ...
    # max_calls=2 over a 3-day fan-out: two days fetched, the third deferred and filled from the previous bundle by date

def test_after_the_budget_ends_cached_datasets_still_refresh(garmin) -> None: ...
    # warm the cache for dataset B, then run [A (needs 2 calls), B] with max_calls=1:
    # A is stale with reason "deferred"; B is fresh (stale False, calls 0)

def test_rate_limited_defers_the_rest(garmin) -> None: ...
    # dataset A fails with a 429; dataset B (cold) is never sent to Garmin: stale, reason "deferred"

def test_foreach_fill_limit_parent_columns_and_one_child_failing(garmin) -> None: ...
    # shoes: 3 rows; foreach limit 2 over gear activities {uuid}: exactly 2 children called with each uuid;
    # child rows carry "parent.uuid"; child 2 fails -> meta.errors has its uuid and code, previous rows with that parent.uuid kept

def test_foreach_refuses_flag_like_placeholder_values(garmin) -> None: ...
    # a parent row whose value is "--no-cache" or null: that child is not run; meta.errors has code "bad_args"

def test_auth_required_stops_and_writes_nothing(garmin, tmp_path) -> None: ...
    # CLI: auth_status unauthenticated -> previous bundle file unchanged, no client call, code auth_required, exit 3

def test_previous_bundle_missing_or_corrupt(garmin, tmp_path) -> None: ...
    # CLI with a corrupt bundle file and a recipe whose ids differ from the previous one: exit 0, every dataset present

def test_cli_check_init_and_run(garmin, tmp_path) -> None: ...
    # --init writes the default recipe (first key of each line is "id") and refuses a second time (exit 2);
    # --check makes no call and lists ids; --check with argv the parser refuses ("--days", "x") is bad_args naming the dataset;
    # a run writes the bundle (mode 0600), its meta "Recipe" value has no home directory path, and stdout is exactly one JSON line
```

Fill in each `...` test from its comment, with concrete payloads and exact assertions on `rows`, `stale`, `reason`, `day_errors`, `errors` and the calls recorded by the fake.

- [ ] **Step 2: Run them to verify they fail** (`ModuleNotFoundError: pace.exporter`).

- [ ] **Step 3: Implement** `pace/exporter.py`, `pace/commands/export.py`, the CLI registration and the schema entry.

- [ ] **Step 4: Docs.** `docs/CONTRACT.md`: a `## Export` section (recipe format and rules, allowlist and refused flags, budget, bundle shape, failure handling, `--check`, `--init`, the summary). Note that commands which catch per-item errors themselves (`plan status` effort, `zones show` due check) return nulls for a deferred item with no `stale` flag. `.claude/skills/pace/reference.md`: a command map row for `pace export` (keyed none, "Run the dashboard recipe and write the bundle file").

- [ ] **Step 5: Run the whole suite**, expected all pass.

- [ ] **Step 6: Commit**

```bash
git add pace/exporter.py pace/commands/export.py pace/cli.py pace/commands/schema.py pace/store.py docs/CONTRACT.md .claude/skills/pace/reference.md tests/test_exporter.py
git commit -m "feat: pace export runs the dashboard recipe within a call and time budget"
```

---

### Task 5: Coach side: dashboard procedure

**Files:**
- Modify: `.claude/skills/pace/dashboard.md`, `README.md` (one line under the command overview if it lists commands)
- Test: `tests/test_skill.py`

**Interfaces:**
- Consumes: `pace export` (Task 4) and its flags; the bundle shape.

- [ ] **Step 1: Write the failing test** in `tests/test_skill.py`:

```python
def test_dashboard_uses_export() -> None:
    dash = DASHBOARD.read_text()
    assert "pace export" in dash and "pace load --export" not in dash
    assert "## Add a view" in dash and "bolted on" not in dash
    assert "dash.loader" in dash and "pace export --check" in dash
```

- [ ] **Step 2: Run it to verify it fails.**

- [ ] **Step 3: Rewrite `dashboard.md`** per the spec's Part 3 (PR A items):
  - Intro: the page reads one attached bundle from `pace export`; each dataset reads its own section with a `dash.loader` (`answer.datasets.<id>.rows`); the `load` dataset keeps its calcs and filters.
  - Create: `pace export` (default recipe, or `pace export --init` first when the athlete's page needs a custom one), upload the bundle once, one dataset per section with its loader.
  - Refresh: `pace export`; upload the bundle; for the datasets that read the previous bundle, one ArtifactData `batch` of `update`s, each pinned to its dataset's version, with the new `source.url` and `updated: {at: <Generated>, by: "pace coach"}`; the asset cleanup rule stays (keep the replaced asset, delete older `application/json` assets); then the `check` and settings steps. A section with `stale: true` in the summary is mentioned to the athlete once with its error; `auth_required` means the athlete logs in, no refresh.
  - Add a view (replaces the "bolted on" rule): on the athlete's request only; add a recipe line to `$PACE_HOME/dashboard/recipe.json` (create it with `pace export --init` first), `pace export --check`, `pace export`, add the dataset (file source, loader for the new section) and the page section (read `files/index.html` first, keep the one-writer rule), read `check` once, record a diary note. Deep dives on one run or one block stay separate artifacts or `pace chart` PNGs.
  - Unattended runs: the permissions line now names `pace export` and dataset updates.

- [ ] **Step 4: Run the whole suite**, expected all pass (the skill span checker validates every `pace ...` span and flag).

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/pace/dashboard.md README.md tests/test_skill.py
git commit -m "docs: dashboard refresh and new views through pace export"
```

---

### After the tasks (controller, not a task)

- Live check of the default recipe from the worktree against real Garmin with a throwaway `PACE_HOME` (cold cache), then again at once (warm): fix any column path that returns only nulls, and write the measured cold and warm calls and seconds into CONTRACT.md next to the default recipe.
- Rollout once the PR merges: move the live dashboard's `load` dataset to the bundle's `load` section (source URL plus a `dash.loader` in the page), and read `check`.
