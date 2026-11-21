# Coach 3: Weekly Load and Charts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One source of weekly and daily training load (`pace load`), and PNG charts (`pace chart KIND`) the coach shows in the terminal and later sends to Telegram.

**Architecture:** `pace/load.py` is a pure aggregation of activities and plan sessions into week and day rows with a rolling 7-day sum, plus one shared fetch used by every caller.
`pace/charts/` holds a matplotlib-free registry, one shared style module and one module per chart kind; each kind is a pure `render(data, out, **opts) -> summary` fed by an async `fetch`, so drawing is testable from hand-built data.
`pace/commands/load.py` and `pace/commands/chart.py` wrap them in the envelope; `wellness sleep-range` exposes the sleep-stats range endpoint as a read command.

**Tech Stack:** Python 3.14, matplotlib (object API, `matplotlib.figure.Figure`, no pyplot; new dependency, approved), typer, pytest.

**Spec:** `docs/superpowers/specs/2026-11-17-coach-plan-design.md` section 3 (load and charts), section 4 (the dashboard reuses `pace load`).
Plan 3 of 4; depends on plans 1-2 (`pace.plan`, `pace.sports`, `pace.matching`, `pace.commands.plan` status fetch).

## Global Constraints

- Week key: ISO week from `date.isocalendar()` formatted `f"{y}-W{w:02d}"` (never `strftime("%Y-W%W")`); weeks start Monday; days are local dates from `startTimeLocal[:10]`, never GMT.
- Per-day values: `actual` from activities of the selected sport family; `planned` from sessions of the selected sport (excluding `skipped` and `rest`; a stored `done` counts).
  A day's `effective` value is the one the rolling line uses: actual for days before today, planned for days after today, and for today actual if any activity of the selected sport is recorded today, else planned.
- `rolling7` on day D = sum of `effective` over D-6 .. D; the aggregation reads 6 days before the first emitted day so the first rows are complete.
- Week rows: `actual` (sum of actual) for weeks starting on or before today; `planned` (sum of planned over all 7 days) for weeks ending on or after today; `projected` (sum of `effective`) for every week.
  By construction `rolling7` on a week's Sunday equals that week's `projected`; for a past week `projected == actual`.
- Sums are computed unrounded and rounded to 2 decimals only at output.
- `planned.missing` per metric counts sessions whose target for that metric is null.
- Range flags: `pace load` and `pace chart volume` use weeks (`--weeks N` back, default 16, >= 1; `--weeks-ahead N`, default: through the week of the plan's last session, never before this week, capped at 26 even when given); every other chart kind and `plan` commands use days (`--from`, `--to`, `--past`, `--ahead`).
- Charts: PNG only (`format="png"`; `--out` must end in `.png`, else `bad_args`), exactly 1200 x 675 px (figsize 8 x 4.5, `dpi=150`, constrained layout, never `bbox_inches="tight"`), one shared style; matplotlib is imported lazily inside `pace/charts/style.py` functions and renderers, never by `pace/charts/__init__.py`, `pace/commands/chart.py` at import time, or `pace/commands/schema.py`.
- Default output `$PACE_HOME/charts/<name>.png` where name = `volume-<metric>[-all]-<from>-<to>`, `activity-<id>`, or `<kind>-<from>-<to>`; `--out PATH` overrides; envelope `data: {"path", "kind", "range", "summary"}`.
- Load the `dataviz` skill before writing the style module; follow its palette and mark rules (tokens in one place, light background, since PNGs are viewed in chat apps).
- Health data (verified live 2026-11-17 on the user's Forerunner 55): `GET /sleep-service/stats/sleep/daily/{from}/{to}` returns `{"individualStats": [{"calendarDate", "values": {...}}], "overallStats": ...}`; `values` holds `totalSleepTimeInSeconds` (= deep + light + REM, awake excluded), `deepTime`, `lightTime`, `remTime`, `awakeTime`, `restingHeartRate`, `avgHeartRate`, `avgOvernightHrv`, `hrv7dAverage`, `hrvStatus`, `sleepScore` (the last four null on this watch); at most 28 days inclusive per call (29 -> HTTP 400).
  Weight: `get_weigh_ins(start, end)` (one call; grams).
  HR zones per activity: `hrTimeInZone_1..5` (seconds) on activity list rows.
- Fixtures for sleep-stats and weigh-ins are hand-built from the shapes above (the recorder redacts `weight` and has no argv for the range endpoint until `wellness sleep-range` exists); no real ids or profile ids.
- Adding matplotlib needs the private index token: `source ~/.bashrc && update_aws_token && uv add matplotlib`.
- Every command test monkeypatches `pace.config.PACE_HOME` and `pace.config.CACHE_DB` to `tmp_path`.
- ASCII only; markdown one sentence per line; pytest and ruff clean at every commit; no Claude attribution.

## Review Focus

1. An activity whose local date differs from its GMT date (local `2026-11-16 00:30:00`, GMT `2026-11-15 23:30:00`): counted on Monday; and the mirrored case. Test in Task 1.
2. The first days of the range: `rolling7` on the first emitted day includes the six days before it. Test in Task 1.
3. For every week in a range spanning past, current and future weeks: `rolling7` on its Sunday equals its `projected`. Test in Task 1.
4. A chart range with no data (no weigh-ins, no plan): the PNG renders with a "no data in range" note and `summary.empty` is true. Test in Task 2 and Task 3.
5. Two volume charts with different metrics in one turn get different default file names. Test in Task 2.

---

### Task 1: Load aggregation and `pace load`

**Files:**
- Create: `pace/load.py`, `pace/commands/load.py`
- Modify: `pace/cli.py`, `pace/commands/schema.py` (entry, group text, `known` set), `docs/CONTRACT.md`, `README.md`
- Test: `tests/test_load.py`, `tests/test_load_cmd.py`, `tests/test_schema.py`

**Interfaces:**
- Consumes: `pace.sports.family`, `pace.plan.load` (imported as `from pace import plan as plan_store` to avoid the name clash), `pace.plan.sessions_in`, `cached_call`, `pace.cache.Cache`.
- Produces (`pace.load`):

```python
METRICS = ("km", "elev_m", "hours")
def week_key(d: date) -> str
def aggregate(activities: list[dict], sessions: list[dict], *, start: date, end: date,
              today: date, sport: str) -> dict
    # rows emitted for start..end; activities/sessions may include the 6 days before start (used for rolling7)
    # -> {"weeks": [...], "days": [...], "range": {"from", "to"}, "sport": sport}
def default_range(plan_data: dict, *, today: date, weeks: int, weeks_ahead: int | None) -> tuple[date, date]
    # start = Monday of today's week minus (weeks - 1) weeks; end = Sunday of max(this week, week of the plan's
    # last session, or this week + weeks_ahead when given), capped at this week + 26
async def fetch_inputs(start: date, end: date, *, today: date, cache: Cache, tracker, refresh: bool) -> tuple[list[dict], list[dict]]
    # activities: one cached_call("get_activities_by_date", (start - 6).isoformat(), min(end, today).isoformat(), None, ttl=600),
    # skipped when start - 6 > today; type flattened to its typeKey; sessions: plan sessions in start - 6 .. end
```

  Activity values: km = `distance / 1000`, elev_m = `elevationGain`, hours = (`movingDuration` if > 0 else `duration`) / 3600; missing values count 0.
  Session values from `target`: km = `distance_m / 1000`, elev_m = `elevation_m`, hours = `duration_s / 3600`; a null target adds 0 and counts in `missing`.
  `sport == "running"` keeps running-family activities and running sessions; `"all"` keeps everything.
  Week row: `{"week", "start", "actual": {...} | None, "planned": {..., "missing": {...}} | None, "projected": {...}}`.
  Day row: `{"date", "actual": {...} | None (None after today), "planned": {..., "missing"} | None (None when no session that day), "effective": {...}, "rolling7": {...}}`; past days keep `planned` so a missed day is visible.

- `pace load [--weeks N] [--weeks-ahead N] [--sport running|all] [--no-cache]`; schema `keyed: "week"` (a new keyed value, documented in the schema notes: week-ranged, no `--from/--to`).

- [ ] **Step 1: Write failing tests** `tests/test_load.py` (`today = date(2026, 11, 18)`, a Wednesday):
  - `test_week_rows_split`: past week `actual` only (+ `projected == actual`), current week `actual`, `planned` and `projected`, next week `planned` and `projected`.
  - `test_effective_and_rolling7`: 10 km runs Mon and Tue of this week, planned 12 km Thursday; `effective` and `rolling7` on Tue, Wed and Thu match hand sums.
  - `test_sunday_equals_projected`: over a 5-week range, for every week `rolling7` on its Sunday equals `projected` (all three metrics, after rounding).
  - `test_first_days_complete`: runs on the 3 days before `start` count in `rolling7` of `start` but produce no rows before it.
  - `test_today_rule_respects_sport`: a ride today and a planned run today -> with `sport="running"` today's effective is the planned run; with a run recorded today, the run.
  - `test_local_not_gmt`: local `2026-11-16 00:30:00` / GMT `2026-11-15 23:30:00` counts on Monday 16th; local `2026-11-15 23:30:00` / GMT `2026-11-16 00:30:00` (hypothetical eastward zone) counts on Sunday 15th.
  - `test_iso_year_boundary`: an activity on 2027-01-02 lands in `2026-W53`; range 2026-12-21 .. 2027-01-10 yields `2026-W52`, `2026-W53`, `2027-W01` once each.
  - `test_missing_targets_and_exclusions`: null `distance_m` counted in `missing.km`; `skipped` and `rest` excluded; cycling excluded for running, included for all.
  - `test_default_range`: no plan -> ends this Sunday; plan ending last month -> still this Sunday; plan 40 weeks out -> capped at 26; `weeks_ahead=30` -> capped at 26.
- [ ] **Step 2: Write failing command test** `tests/test_load_cmd.py`: one `get_activities_by_date` call from `start - 6` with `to` clipped to today; `--sport bogus`, `--weeks 0`, `--weeks-ahead -1` -> exit 2.
- [ ] **Step 3: Run** -> fail.
- [ ] **Step 4: Implement** `pace/load.py`, `pace/commands/load.py` (registered as a single command, like `schema`), schema entry, group text, `known` set, the `keyed: "week"` note; `test_every_cli_command_is_in_schema` stays green.
- [ ] **Step 5: Run** full suite and ruff -> green.
- [ ] **Step 6: Docs**: CONTRACT "Load" section (row shapes, `effective`/`projected`/`rolling7` rules, missing counts, defaults, the 6-day lookback, and that `get_activities_by_date` pages internally in requests the limiter does not count); README line.
- [ ] **Step 7: Commit** `git commit -m "feat: pace load weekly and daily training load"`.

---

### Task 2: Chart framework, `volume` and `plan` charts

**Files:**
- Create: `pace/charts/__init__.py`, `pace/charts/style.py`, `pace/charts/volume.py`, `pace/charts/plan.py`, `pace/commands/chart.py`
- Modify: `pyproject.toml` / `uv.lock` (matplotlib), `pace/cli.py`, `pace/commands/schema.py` (entry, group, `known`), `docs/CONTRACT.md`, `README.md`
- Test: `tests/test_charts.py`, `tests/test_chart_cmd.py`

**Interfaces:**
- Consumes: Task 1 `aggregate`, `default_range`, `fetch_inputs`; plan 2 `pace.matching.match` and the status fetch from `pace/commands/plan.py` (extract it to a function `fetch_status_inputs(start, end, *, today, cache, tracker, refresh)` there if plan 2 did not, and reuse it; the chart calls `match(..., effort=None)`, never fetching effort).
- Produces:

```python
# pace/charts/__init__.py  (no matplotlib import)
KINDS: tuple[str, ...]                     # grows per task: ("volume", "plan") here
def default_out(kind: str, *, start: date | None, end: date | None, metric: str | None = None,
                sport: str | None = None, activity_id: int | None = None) -> Path

# pace/charts/style.py  (matplotlib imported inside the functions)
COLORS: dict[str, str]                     # tokens: actual, planned, projected, line, done, modified, missed, unconfirmed, skipped, rest, unplanned, grid, text, muted
def figure() -> tuple["Figure", "Axes"]    # Figure(figsize=(8, 4.5), dpi=150, layout="constrained"), shared rc applied via the figure
def save(fig, path: Path) -> Path          # mkdir parents; fig.savefig(path, format="png", dpi=150)
def empty_note(ax, text: str = "No data in range") -> None

# each kind module
def render(data: dict, out: Path, **opts) -> dict                     # returns summary (always has "empty": bool)
async def fetch(start: date, end: date, *, today: date, cache, tracker, refresh: bool, **opts) -> dict
```

  `volume`: data = `aggregate(...)`; `metric` in `km | elev | hours`; weekly bars: past weeks `actual` solid, the current week `actual` solid with (`projected - actual`) stacked lighter, future weeks `projected` lighter; the `rolling7` line at daily resolution (x = day, bar for a week spans its 7 days), solid up to today, dashed after; summary = `{"metric", "sport", "weeks": [{"week", "actual", "projected"}], "rolling7_today", "empty"}`.
  `plan`: data = `match(...)` for the range; one mark per session on a date axis colored by status, unplanned activities as hollow marks; summary = counts per status, `empty`.

- `pace chart KIND [ID] [--weeks] [--weeks-ahead] [--from] [--to] [--past] [--ahead] [--metric km|elev|hours] [--sport running|all] [--out PATH] [--no-cache]`: `ID` only for `activity` (positive int; required there, `bad_args` elsewhere); `--weeks/--weeks-ahead/--metric` only for `volume`, day flags only for the other kinds, `--sport` for `volume` and `intensity`; a flag given to a kind that does not use it -> `bad_args`.
  Defaults: `volume` as `pace load`; `plan` past 14 ahead 14; health kinds past 90 ahead 0.

- [ ] **Step 1: Add matplotlib** (`source ~/.bashrc && update_aws_token && uv add matplotlib`); confirm `.venv/bin/python -c "import matplotlib"`.
- [ ] **Step 2: Write failing tests** `tests/test_charts.py`, renderers from hand-built data:
  - `test_volume_png_size_and_summary`: PNG signature and IHDR width/height 1200 x 675 (read bytes 16-24); `summary.weeks` equals the input for the metric.
  - `test_volume_metric_switch`: `metric="elev"` uses `elev_m`.
  - `test_plan_status_counts` equal the matcher output.
  - `test_empty_data_note`: empty input -> PNG written, `summary["empty"] is True`.
  - `test_import_is_lazy`: in a subprocess, `import pace.cli` then `"matplotlib" not in sys.modules`.
- [ ] **Step 3: Write failing command tests** `tests/test_chart_cmd.py`: `pace chart volume --out <tmp>/v.png` -> envelope `path`; `--out x.jpg` -> exit 2; unknown kind -> exit 2; `--metric` with `plan` -> exit 2; default names differ for `--metric km` vs `--metric hours` and land under `$PACE_HOME/charts/`.
- [ ] **Step 4: Run** -> fail.
- [ ] **Step 5: Implement** (load the `dataviz` skill first), schema entry and group.
- [ ] **Step 6: Run** full suite and ruff -> green.
- [ ] **Step 7: Look at both PNGs once** (Read tool) and fix anything illegible at phone size.
- [ ] **Step 8: Docs**: CONTRACT "Charts" (envelope, kinds so far, flags per kind, default names, `summary`); README lines.
- [ ] **Step 9: Commit** `git commit -m "feat: chart framework with volume and plan charts"`.

---

### Task 3: Health sources, `wellness sleep-range`, recovery and sleep charts

**Files:**
- Create: `pace/charts/sources.py`, `pace/charts/recovery.py`, `pace/charts/sleep.py`
- Modify: `pace/commands/wellness.py` (or a hand-written command module registered in the wellness group), `pace/charts/__init__.py` (KINDS), `pace/commands/chart.py`, `pace/commands/schema.py`, `scripts/record_fixtures.py` (record the new read), `docs/CONTRACT.md`, `README.md`
- Test: `tests/test_charts.py`, `tests/test_chart_cmd.py`, `tests/test_sleep_range.py`

**Interfaces:**
- Produces (`pace.charts.sources`):

```python
SLEEP_CHUNK_DAYS = 28
def sleep_chunks(start: date, end: date) -> list[tuple[date, date]]
    # fixed windows anchored on date.toordinal() // 28 so keys repeat day to day; each clipped to [start, end]
async def sleep_stats(start: date, end: date, *, today: date, cache, tracker, refresh: bool) -> list[dict]
    # end clipped to today; per chunk cached_call("connectapi", f"/sleep-service/stats/sleep/daily/{a}/{b}",
    # ttl=ttl_for_day(b, ...)) using pace.cache.ttl_for_day; returns one row per date, sorted, deduplicated:
    # {"date", "sleep_s", "deep_s", "light_s", "rem_s", "awake_s", "rhr", "avg_hr", "hrv", "hrv_7d", "score"}
    # (hrv = avgOvernightHrv, hrv_7d = hrv7dAverage, score = sleepScore; None when absent)
```

- `pace wellness sleep-range [--from] [--to] [--days]` (keyed `date`, standard range flags): the rows above as `data`; read command in the schema; recorder gains it.
- Kinds added to KINDS: `recovery` (daily resting HR and its 7-day trailing calendar mean; a second axis with `hrv` and `hrv_7d` only when any value is non-null; summary per day + latest 7-day mean + `hrv_available`), `sleep` (stacked deep / light / REM / awake hours per night, score line only when present; summary per night + 7-day mean of `sleep_s`).

- [ ] **Step 1: Write failing tests** (hand-built payloads in the verified shape):
  - `test_sleep_chunks_fixed_windows`: same `(a, b)` chunk keys for ranges ending today and yesterday except the last chunk; every chunk <= 28 days inclusive; a 60-day range has no gaps or duplicates.
  - `test_sleep_rows_mapping`: fields mapped as above; future dates never requested.
  - `test_recovery_hides_hrv_when_null` (one axis, `hrv_available` false) and shows it with values.
  - `test_sleep_chart_summary`, `test_recovery_empty_note`.
  - `test_sleep_range_command`: chunked calls, envelope rows.
- [ ] **Step 2: Run** -> fail. **Step 3: Implement**. **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Docs**: CONTRACT (`wellness sleep-range`, 28-day chunks, HRV and score absent on the current watch, `recovery` and `sleep` kinds), README.
- [ ] **Step 6: Commit** `git commit -m "feat: sleep range read, recovery and sleep charts"`.

---

### Task 4: Weight, intensity and activity charts

**Files:**
- Create: `pace/charts/weight.py`, `pace/charts/intensity.py`, `pace/charts/activity.py`
- Modify: `pace/charts/sources.py` (`weigh_ins`), `pace/charts/__init__.py`, `pace/commands/chart.py`, `docs/CONTRACT.md`, `README.md`, the spec (post-hoc notes)
- Test: `tests/test_charts.py`, `tests/test_chart_cmd.py`

**Interfaces:**
- `sources.weigh_ins(start, end, *, today, cache, tracker, refresh) -> list[{"date", "kg"}]`: one `cached_call("get_weigh_ins", a, b, ttl=3600)`, latest weigh-in per day, grams / 1000.
- `weight`: points per weigh-in and a 7-day trailing calendar mean (mean of the weigh-ins within D-6 .. D, drawn on days with a weigh-in); summary first, last, mean, count, `empty`.
- `intensity`: per ISO week, actual seconds in HR zones 1-5 (`hrTimeInZone_*` summed over activities of the selected sport family, from `fetch_inputs`) next to planned seconds by intensity (`easy | moderate | hard`, from session `duration_s`, with the count of sessions lacking a duration); summary per week and the share of zones 1-2.
- `activity ID`: from `cached_call("get_activity_details", id, ttl=86400)` bucketed with the `activity details` columns; running family: pace (min/km, inverted axis, speeds below 0.5 m/s dropped); other sports: speed (km/h); HR and elevation over distance; summary distance, moving time, avg pace or speed, avg HR, elevation gain.

- [ ] **Step 1: Write failing tests**: `test_weight_trailing_mean`, `test_weight_empty_note`, `test_intensity_zone_sums` (two runs one week, cycling excluded for running), `test_activity_chart_running_pace` and `_cycling_speed`, `test_activity_requires_id` (exit 2), `test_id_rejected_for_other_kinds` (exit 2).
- [ ] **Step 2: Run** -> fail. **Step 3: Implement**. **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Live check**: render every kind once against the user's account into the repo root (`--out ./chart-<kind>.png`, never committed), look at each once, fix legibility.
- [ ] **Step 6: Docs**: CONTRACT kinds; README; spec post-hoc notes for section 3: `hrv` kind became `recovery` (resting HR, HRV when available), sleep score only when present, health charts read `wellness sleep-range` (28-day chunks) instead of per-day reads, `volume` file names carry the metric, `pace load` gained `projected` and `effective`.
- [ ] **Step 7: Commit** `git commit -m "feat: weight, intensity and activity charts"`.
