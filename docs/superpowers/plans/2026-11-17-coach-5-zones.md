# Coach 5: Thresholds and Zones Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** pace estimates the user's lactate thresholds (LT1, LT2) from past runs, analyzes field tests, accepts lab values, derives HR and pace zones from the current record, and keeps the watch's HR zones in sync.

**Architecture:** `pace/zones.py` is the threshold store and zone derivation (pure, on `pace/store.py`).
`pace/thresholds.py` holds the pure estimation and test-analysis methods, operating on activity rows and per-sample tables.
`pace/commands/zones.py` wraps them; the Garmin zone read and write go through `run_write` / `cached_call` like every other command.

**Tech Stack:** Python 3.14, typer, stdlib statistics, pytest.

**Spec:** `docs/superpowers/specs/2026-11-17-coach-plan-design.md` section 7 (thresholds and zones), section 5B (run review uses the zones), Constraints.
Plan 5; depends on plans 1-2 (`pace.store`, `pace.plan`, `pace.workout`), independent of plans 3-4 except that plan 4's skill text names these commands (plan 4 runs after this plan).

## Global Constraints

- Store `$PACE_HOME/zones.json`: `{"records": [...], "current": id | None, "next_id": int}`, same store rules as diary and plan (lock, atomic save, corrupt -> `internal`).
- Record: `{"id", "date", "source": "estimate" | "field_test" | "lab", "method", "lt1": {"hr", "pace_s_per_km"}, "lt2": {"hr", "pace_s_per_km"}, "max_hr", "rest_hr", "activities": [ids], "confidence": "low" | "medium" | "high", "notes"}`; any threshold value may be null.
- Precedence (spec 7): `current` changes only through `zones accept ID`, except `zones add --source lab` which makes the new record current; a lab record older than 183 days is reported `expired: true` by `zones show` (advisory: it stays current until another is accepted).
- Methods are chosen by the research step (Task 1) and recorded in `coaching.md` with evidence grade and verified sources; the plan fixes their interfaces, not their formulas.
- HR source: an activity used for thresholds must be a strap run (its `get_activity` `metadataDTO.sensors` has an entry with `bleDeviceType == "HEART_RATE"` or an ANT+ heart-rate type); wrist runs are excluded unless `--include-wrist`, and outputs list which runs were used.
- All arithmetic in pace; every estimate returns its inputs (activity ids, segment bounds) so the coach can explain it.
- Garmin zones read: `GET /biometric-service/heartRateZones` (verified 2026-11-17: list of `{trainingMethod, restingHeartRateUsed, lactateThresholdHeartRateUsed, zone1Floor..zone5Floor, maxHeartRateUsed, restingHrAutoUpdateUsed, sport, changeState}`).
  The write is verified live in Task 4 before it is built; if no write endpoint works, `zones push` is dropped and the coach tells the user the five floors to enter in the Garmin app.
- Rate limit: estimation over many runs needs `get_activity` (sensors) and `get_activity_details` (samples) per run; cap the runs examined per call (`--max-runs`, default 12) and rely on the one-day / one-week cache.
- No real ids in tests or docs; ASCII only; markdown one sentence per line; pytest and ruff clean at every commit; no Claude attribution.

## Review Focus

1. A lab record entered 7 months ago and a newer estimate: `zones show` keeps the lab record current, marks it expired, and lists the estimate as a candidate. Test in Task 2.
2. A drift test run with a GPS dropout or a stop in the middle: the analysis excludes stopped samples and reports the excluded time, instead of a wrong drift %. Test in Task 3.
3. Estimation when every candidate run is a wrist run: no estimate, with a reason, unless `--include-wrist`. Test in Task 3.
4. Zone floors that are not strictly increasing (LT1 HR above LT2 HR from a bad record): `bad_args` on add, never pushed to the watch. Test in Task 2.
5. `zones push` when Garmin's zones were changed in the app since the last read: the dry-run shows the current Garmin values next to the new ones. Test in Task 4.

---

### Task 1: Research the methods (no code)

**Files:**
- Create: `docs/superpowers/specs/2026-11-17-zones-methods.md`
- Later reused by plan 4 for `.claude/skills/pace/coaching.md` ("Thresholds and zones" section).

- [ ] **Step 1:** Find and open current sources on: LT1 / aerobic threshold from HR drift or aerobic decoupling over a steady run (protocol, duration, drift cut-off); LT2 / critical speed from two or more maximal efforts or race results (model, durations, errors); LT2 HR from a 30-minute time trial; estimating thresholds from training data without a test; the relation of lab LT1/LT2 to field markers; three-zone and five-zone models anchored on LT1/LT2.
  Prefer peer-reviewed reviews; practitioner protocols are allowed with the grade "weak/expert opinion".
- [ ] **Step 2:** Write the methods doc: for each method, the protocol, the formula pace will implement, its inputs, its known error, evidence grade, and sources (author, year, title, venue, DOI or URL, and whether full text or abstract was read).
  End with the chosen set: one LT1 estimate, one LT2 estimate from history, one LT1 field test, one LT2 field test, and the zone mapping (3 zones and 5 watch zones from LT1/LT2 HR).
- [ ] **Step 3:** Commit `git commit -m "docs: threshold and zone methods with sources"`.
  The controller reviews this doc before Task 3 starts; formulas in Tasks 2-3 follow it.

---

### Task 2: Zone store and derivation

**Files:**
- Create: `pace/zones.py`
- Test: `tests/test_zones.py`

**Interfaces:**
- Consumes: `pace.store`, `pace.dates.parse_date`, the zone mapping from the Task 1 doc.
- Produces (`pace.zones`):

```python
SOURCES = ("estimate", "field_test", "lab")
LAB_VALID_DAYS = 183
def load() -> dict
@contextmanager
def transaction() -> Iterator[dict]
def add_record(data: dict, record: dict, *, today: date) -> dict      # validates; lab -> becomes current
def accept(data: dict, record_id: int) -> dict
def current(data: dict) -> dict | None
def derive(record: dict) -> dict
    # {"three": [{"name", "hr_min", "hr_max", "pace_min_s", "pace_max_s"}],
    #  "five": [{"zone": 1..5, "hr_floor", "pace_floor_s"}], "rule": "<mapping name from the methods doc>"}
def status(data: dict, *, today: date) -> dict    # {"current", "expired", "zones", "candidates": newer non-current records}
```

  Validation (`bad_args`): source in SOURCES; date parses and is not in the future; HR values int 30..230; LT1 HR < LT2 HR < max HR when present; paces positive, LT1 pace slower (larger s/km) than LT2 pace; at least one threshold value present.

- [ ] **Step 1: Write failing tests**: add/accept/current; lab becomes current; lab older than 183 days -> `expired` and stays current, newer estimate in `candidates`; invalid orderings -> `bad_args`; `derive` gives strictly increasing floors and contiguous three-zone bounds; corrupt file -> `internal`.
- [ ] **Step 2: Run** -> fail. **Step 3: Implement**. **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Commit** `git commit -m "feat: threshold store and zone derivation"`.

---

### Task 3: Estimation, test analysis, test workouts

**Files:**
- Create: `pace/thresholds.py`
- Test: `tests/test_thresholds.py`

**Interfaces:**
- Consumes: Task 1 methods, Task 2 record shape, the per-sample table shape of `activity details` (cols `t, hr, speed_mps, dist_m, elev_m, cadence, power_w`), `pace.workout.build_workout` (test workouts must pass it).
- Produces (`pace.thresholds`):

```python
def hr_source(activity: dict) -> str                         # "strap" | "wrist" from get_activity metadataDTO.sensors
def moving_samples(table: dict, *, min_speed_mps: float = 0.5) -> tuple[list[dict], int]   # (samples, excluded seconds)
def analyze_drift(table: dict, *, warmup_s: int) -> dict     # LT1 field test: halves compared per the methods doc; returns drift %, verdict, segments
def analyze_time_trial(table: dict, *, duration_s: int) -> dict   # LT2 field test per the methods doc
def estimate(runs: list[dict]) -> list[dict]                  # runs: [{"activity", "table", "hr_source"}]; one candidate record per method with inputs and confidence
def test_workout(kind: str) -> dict                           # DSL for "drift" | "time_trial", names prefixed by the plan's push
```

- [ ] **Step 1: Write failing tests** from synthetic sample tables: steady run with no drift / with 8 % drift; a stop in the middle excluded and reported; time trial HR over the specified window; estimate skips wrist runs and says so; `--include-wrist` path; `test_workout` output passes `build_workout`; `hr_source` on sensors present / absent.
- [ ] **Step 2: Run** -> fail. **Step 3: Implement** per the methods doc. **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Commit** `git commit -m "feat: threshold estimation and field test analysis"`.

---

### Task 4: `zones` commands and Garmin sync

**Files:**
- Create: `pace/commands/zones.py`
- Modify: `pace/cli.py`, `pace/commands/schema.py` (entries, group, `known`, writes set in `tests/test_schema.py`), `docs/CONTRACT.md`, `README.md`, the spec (post-hoc notes)
- Test: `tests/test_zones_cmd.py`, `tests/test_schema.py`

**Interfaces:**
- Commands:

| command | kind | `data` |
|---|---|---|
| `zones show` | read (local + one `cached_call("connectapi", "/biometric-service/heartRateZones", ttl=3600)`) | `status(...)` + `{"garmin": [...]}` |
| `zones estimate [--weeks N=8] [--max-runs N=12] [--include-wrist] [--no-cache]` | read | `{"candidates": [...], "runs_used": [...], "runs_skipped": [{"id", "reason"}]}` |
| `zones analyze ACTIVITY_ID --test drift|time_trial [--warmup 15m]` | read | analysis dict |
| `zones test-workout drift|time_trial` | pure | `{"workout": DSL}` |
| `zones add --source S [--date] [--lt1-hr] [--lt1-pace] [--lt2-hr] [--lt2-pace] [--max-hr] [--rest-hr] [--method] [--activity ID]... [--notes]` | local_write | `{"record": ...}` |
| `zones accept ID` | local_write | `{"current": ...}` |
| `zones push [--dry-run]` | Garmin write | `run_write` result; dry-run payload shows `before` (current Garmin floors) and `after` |

  `--lt1-pace` / `--lt2-pace` take `m:ss` per km (reuse `pace.workout.parse_pace` semantics, stored as s/km).

- [ ] **Step 1: Live verification of the zone write** (controller, with the user's yes before any write): capture the request the Garmin Connect web app sends when HR zones are saved (Playwright on connect.garmin.com, network tab), or try `PUT /biometric-service/heartRateZones` with the read payload unchanged (a no-op write); read back; record the verified method, path and body in this plan and the spec. If nothing verifies, drop `zones push` as the spec says.
- [ ] **Step 2: Write failing tests** `tests/test_zones_cmd.py` with `fake_api`: show combines local and Garmin; estimate caps runs at `--max-runs` and reports skipped wrist runs; analyze; add lab -> current; accept; push dry-run shows before/after and makes no call; push sends the verified body and invalidates the zones cache key; push refused when no current record or floors invalid.
- [ ] **Step 3: Run** -> fail. **Step 4: Implement** commands, schema (`zones push` `"write": True` with `--dry-run`; `zones add/accept` `"local_write": True`), docs.
- [ ] **Step 5: Run** full suite and ruff -> green.
- [ ] **Step 6: Live check** (read-only): `pace zones show`, `pace zones estimate` on the user's account; look at the candidates with the user before anything is accepted or pushed.
- [ ] **Step 7: Commit** `git commit -m "feat: zones commands and Garmin zone sync"`.
