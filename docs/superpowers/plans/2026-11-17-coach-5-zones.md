# Coach 5: Thresholds and Zones Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** pace estimates the user's lactate thresholds (LT1, LT2) from past runs (critical speed, HR-speed regression, HR drift in normal runs, DFA alpha1 from RR intervals), analyzes field tests, accepts lab values, derives HR and pace zones from the current record, and keeps the watch's HR zones in sync.

**Architecture:** `pace/zones.py` is the threshold store, precedence, due rule and zone derivation (pure, on `pace/store.py`).
`pace/fit.py` downloads, caches and parses original FIT files (`fitdecode`) into RR intervals and per-second records.
`pace/hrv.py` holds RR artifact correction and DFA alpha1.
`pace/thresholds.py` holds the pure estimation methods (H1-H5), their combination, run selection and the field-test analyses (F1, F2), operating on activity rows, detail tables and RR series.
`pace/commands/zones.py` wraps everything; Garmin reads go through `cached_call`, the zone write through `run_write`.

**Tech Stack:** Python 3.14, typer, fitdecode (new dependency, approved by the user, verified on the user's FIT files), stdlib statistics, pytest.

**Spec:** `docs/superpowers/specs/2026-11-17-coach-plan-design.md` section 7, and `docs/superpowers/specs/2026-11-17-zones-methods.md` (the methods doc, approved by the user): its sections 1 (common data rules), 3-9 (methods), 11 and 14 (chosen set, zone mapping, due rule, run selection and call budget) are normative; every worked example in section 14 is a required unit test with the values given there.
Plan 5; depends on plans 1-3 (`pace.store`, `pace.plan`, `pace.sports`, `pace.load`'s fetch patterns); plan 4's skill runs after it.

## Global Constraints

- Store `$PACE_HOME/zones.json`: `{"records": [...], "current": id | None, "next_id": int, "estimates": [...]}`, same store rules as diary and plan (lock, atomic 0600 save, corrupt -> `internal` with path hint, local not_found hints never mention Garmin).
- Record: `{"id", "date", "source": "estimate" | "field_test" | "lab", "method", "lt1": {"hr", "pace_s_per_km"}, "lt2": {"hr", "pace_s_per_km"}, "max_hr", "rest_hr", "cs_mps", "d_prime_m", "activities": [ids], "confidence": "low" | "medium" | "high", "notes", "details": {...}}`; threshold values may be null.
- Precedence: `current` changes only by `zones accept ID`, except `zones add --source lab` makes the new record current when its date is on or after the current record's date (an older lab result entered for history does not become current).
  A lab record older than 183 days is reported `expired: true` (advisory).
  `zones remove ID` deletes a record (refused for the current record unless `--force`; then `current` becomes None).
- Derivation per methods doc 14.5 (rule "lt-5z-v1"); partial records: three-zone HR needs LT1 and LT2 HR, the five floors need both, pace zones need the paces; missing pieces are null and `zones push` is refused unless all five floors exist and validate.
- `zones show` returns `current`, `expired`, `zones`, `candidates` (records newer than current, not current), `due` per methods doc 14.6 (`{"estimate": bool, "field_test": {"lt1": bool, "lt2": bool}, "reasons": [...]}`), `garmin` (the watch zones read) and `in_sync` (the current five floors equal the watch's `zone1Floor..zone5Floor` of the entry pace pushes to).
- HR source: strap if the activity's `metadataDTO.sensors` contains an entry with `bleDeviceType == "HEART_RATE"` or an ANT+ heart-rate device (field and value pinned by the Task 0 fixtures); wrist runs are dropped from H2, H4 and H5 unless `--include-wrist`.
- Run selection and the call budget exactly as methods doc 14.7: at most 25 Garmin calls per `zones estimate` (detail calls <= 18, 1 personal-records call, FIT downloads <= 6), FIT files cached under `$PACE_HOME/fit/` forever (they never change), "no RR" remembered so a file is never re-downloaded for H4.
- `zones estimate --save` and `zones analyze --save` store a full record (source `estimate` / `field_test`) with inputs and confidence, so the coach never retypes numbers; without `--save` they are read-only.
- All arithmetic in pace; every result carries its inputs (activity ids, segment bounds, fit parameters) so the coach can explain it.
- Garmin zone write: endpoint, body and target entry (`sport`) as verified in Task 0; the push body is built from a fresh read (never the cache); `--dry-run` payload shows `before` and `after`; success invalidates the zones read key.
- No real Garmin ids, profile ids or FIT files in the repo: fixtures are synthetic or sanitized (ids replaced, `userProfileId`/serials removed).
- Python 3.14 (`.python-version`); `.venv/bin/python -m pytest -q` and `.venv/bin/ruff check pace tests scripts` clean at every commit; ASCII only; markdown one sentence per line; no Claude attribution; implementers never run `uv` (the controller adds dependencies).

## Review Focus

1. A lab record entered 7 months ago and a newer estimate: the lab stays current, `expired: true`, the estimate is a candidate; an older lab result entered for history does not replace a newer current record. Test in Task 1.
2. A steady run with a stop or GPS dropout in the middle: the drift analysis (F1, H5) excludes stopped samples and reports the excluded time, instead of a wrong drift %. Test in Task 3.
3. Every candidate run is a wrist run: no H2/H4/H5 estimate, with the reason, unless `--include-wrist`. Test in Task 3 and Task 5.
4. Zone floors not strictly increasing (LT1 HR above LT2 HR): `bad_args` on add, never pushed. Test in Task 1 and Task 5.
5. Garmin zones changed in the app since the last read: `zones push --dry-run` shows the live Garmin values as `before`. Test in Task 5.
6. The RR series and the per-second records are aligned as verified in Task 0 (cumulative RR from the record start, gaps at pauses), so alpha1 windows get the right HR and speed. Test in Task 2.

---

### Task 0: Live verification and fixtures (controller only, with the user)

- [ ] **Step 1: Dependency.** `uv add fitdecode` (PyPI; `.python-version` pins 3.14); full suite green; commit `build: add fitdecode`.
- [ ] **Step 2: Sensors fixture.** From one strap run and one wrist run (`pace activity show ID --raw`), record `metadataDTO.sensors` as sanitized fixtures `tests/fixtures/sensors_strap.json`, `sensors_wrist.json` (serials and ids replaced).
- [ ] **Step 3: RR alignment.** On the cached FIT file of a strap run, compare cumulative RR time with the `record` timestamps (start offset, behavior at auto-pause and stops, drift over the run); write the verified alignment rule into this plan's Task 2 and the methods doc section 6; build a small synthetic FIT-derived fixture (`tests/fixtures/fit_rr_small.json`: RR list + records) that reproduces the rule.
- [ ] **Step 4: Zone write.** With the user's yes: read `GET /biometric-service/heartRateZones` (list per `sport`), capture the request the Garmin Connect web app sends when HR zones are saved (Playwright, network tab) or try a `PUT` of the read payload unchanged; then change one floor by 1 bpm, read back, check the next day and on the watch, revert.
  Record method, path, body fields, which entry to write (`DEFAULT` or `RUNNING`, and whether `trainingMethod`/`restingHrAutoUpdateUsed` must be set so Garmin keeps custom floors) in this plan (Task 5) and the spec, with ids redacted; never commit captured requests, cookies or HAR files.
  If nothing verifies, Task 5 drops `zones push` and `zones show` prints the floors for the user to enter in the Garmin app.

---

### Task 1: Zone store, precedence, derivation, due rule

**Files:** Create `pace/zones.py`; Test `tests/test_zones.py`.

**Interfaces:**

```python
SOURCES = ("estimate", "field_test", "lab")
LAB_VALID_DAYS = 183
ZONE_RULE = "lt-5z-v1"
def load() -> dict
@contextmanager
def transaction() -> Iterator[dict]
def add_record(data: dict, record: dict, *, today: date) -> dict
def accept(data: dict, record_id: int) -> dict
def remove(data: dict, record_id: int, *, force: bool) -> dict
def current(data: dict) -> dict | None
def derive(record: dict, *, h2: dict | None = None) -> dict
    # {"rule", "three_hr": [...], "three_pace": [...], "floors": {"zone1Floor".."zone5Floor", "maxHeartRateUsed"} | None,
    #  "pace_floors": {"z1".."z5"}}; methods doc 14.5; h2 = {"a", "b", "v_min", "v_max"} for z1/z2 pace floors
def due(data: dict, *, today: date, runs_since: list[dict]) -> dict      # methods doc 14.6
def status(data: dict, *, today: date, garmin_zones: list[dict] | None, runs_since: list[dict]) -> dict
```

  Validation (`bad_args`): source in SOURCES; date parses, not in the future; HR values int 30..230; LT1 HR < LT2 HR < max HR when present; LT1 pace slower than LT2 pace; at least one threshold value.

- [ ] **Step 1: Write failing tests**: add/accept/current/remove (+ `--force` for current); lab newer becomes current, lab older does not; lab older than 183 days `expired` and stays current, newer estimate in `candidates`; invalid orderings -> `bad_args`; `derive` reproduces methods doc 14.5 worked example exactly (floors 114, 128, 142, 160, 173; pace floors 463, 368, 304, 250, 235; HRmax unknown -> 169); partial records give null pieces; `due` reproduces the 14.6 worked example; `in_sync` true/false against a Garmin zones list; corrupt file -> `internal`.
- [ ] **Step 2: Run** -> fail. **Step 3: Implement**. **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Commit** `feat: threshold store, zone derivation and due rule`.

---

### Task 2: FIT files, RR artifacts, DFA alpha1

**Files:** Create `pace/fit.py`, `pace/hrv.py`; Test `tests/test_fit.py`, `tests/test_hrv.py`.

**Interfaces:**

```python
# pace/fit.py
def fit_dir() -> Path                                   # $PACE_HOME/fit/
async def get_fit(activity_id: int, *, tracker) -> Path | None
    # cached file if present (no call); else one safe_call("download_activity", id, ORIGINAL), unzip the .fit into fit_dir, return path
def read_fit(path: Path) -> dict                        # {"rr_s": [float], "records": [{"t", "hr", "speed", "dist", "alt"}], "start": iso}
def aligned_rr(fit: dict) -> list[dict]                 # [{"t_s", "rr_s", "hr", "speed"}] per the Task 0 alignment rule
def no_rr(activity_id: int) -> bool / mark_no_rr(...)   # remembered in $PACE_HOME/fit/no_rr.json

# pace/hrv.py
def correct_artifacts(rr: list[float]) -> tuple[list[float], list[bool]]   # methods doc section 6 (21-beat median, 20% band, split/merge/replace, > 2 s = gap)
def dfa_alpha1(rr: list[float], *, n_min: int = 4, n_max: int = 16) -> float | None   # smoothness-priors detrending (lambda 500) per section 6
def windows(aligned: list[dict], *, size_s: int = 120, step_s: int = 5) -> list[dict]  # alpha1, mean HR, mean speed, artifact share, beats; qualifying rules of section 6
```

- [ ] **Step 1: Write failing tests**: the artifact-correction worked example of 14.2 exactly; the alpha1 statistical tests of 14.2 with a seeded generator and the stated tolerances (white 0.58 +/- 0.03, 1/f 1.03 +/- 0.05, Brownian 1.50 +/- 0.05, affine invariance, constant -> None, detrending, artifact robustness); window qualification (4% vs 6% artifacts, 190 beats skipped); `read_fit`/`aligned_rr` on the Task 0 fixture; `get_fit` uses the cache without a call and marks "no RR".
- [ ] **Step 2: Run** -> fail. **Step 3: Implement**. **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Commit** `feat: FIT parsing, RR artifact correction and DFA alpha1`.

---

### Task 3: History methods H1, H2, H3, H5, field tests F1, F2, test workouts

**Files:** Create `pace/thresholds.py`; Test `tests/test_thresholds.py`.

**Interfaces:**

```python
def hr_source(activity: dict) -> str                        # "strap" | "wrist" (Task 0 fixtures)
def moving_samples(table: dict, *, min_speed_mps: float = 0.5) -> tuple[list[dict], int]   # samples without stops/gaps, excluded seconds (section 1 rules)
def critical_speed(tables: list[dict], *, prs: list[dict] | None) -> dict   # H1: windows 180/420/720 s, OLS, gates, confidence
def hr_speed(tables: list[dict]) -> dict                    # H2: segments, OLS a, b, SEE, n, runs, v range
def lt1_from_cs(cs: dict, h2: dict | None) -> dict          # H3
def drift(table: dict, *, warmup_s: int, treadmill: bool) -> dict   # F1 drift % and verdict (14.3)
def drift_bounds(runs: list[dict]) -> dict                  # H5 (section 7, 14.2): bounds, errors, estimate, confidence, notes
def time_trial(table: dict) -> dict                         # F2 (14.4)
def test_workout(kind: str, *, lt1_hr: int | None, lt2_hr: int | None) -> dict   # DSL for "drift" | "time_trial" per 14.3/14.4; must pass build_workout
```

- [ ] **Step 1: Write failing tests**: every worked example of 14.1 (CS 4.000, D' 180, LT2 HR 160), 14.2 H3 (142 bpm, 304 s/km) and H5 (all cases incl. one-sided, 40-min and 27 C rejection), 14.3 (4.94% at LT1; 2.50% below; 8.16% above; 35-min rejected), 14.4 (LT2 HR 168, 250 s/km, no fade); stop in the middle excluded and reported (Review Focus 2); wrist runs dropped; `test_workout` outputs pass `build_workout`; `hr_source` on both fixtures.
- [ ] **Step 2: Run** -> fail. **Step 3: Implement**. **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Commit** `feat: threshold history methods and field test analysis`.

---

### Task 4: H4 from RR data, combination, run selection

**Files:** Modify `pace/thresholds.py`; Test `tests/test_thresholds.py`.

**Interfaces:**

```python
def alpha1_lt1(run_windows: list[list[dict]]) -> dict      # H4: per-run regressions, 0.75 crossing, median, spread, confidence (section 6, 14.2)
def combine_lt1(h3: dict | None, h4: dict | None, h5: dict | None, h2: dict | None) -> dict   # 14.2 combination
def select_runs(rows: list[dict], *, today: date, ref_hr: int | None, weeks: int) -> dict
    # 14.7: {"effort": [...], "steady": [...], "drift": [...], "skipped": [{"id", "reason"}]}
def select_fit(detailed: list[dict], *, ref_hr: int | None, cached: set[int], no_rr: set[int], rr_since: date) -> list[int]   # 14.7 span score, <= 6
```

- [ ] **Step 1: Write failing tests**: the H4 regression worked example (runs A, B -> 146 bpm, 291 s/km, medium) with alpha1 injected; no-crossing note; the combination worked examples (144 / 298 s/km medium; disagreement -> H5 142 low with F1 suggestion at 147; fallback to H3); the 14.7 selection worked example (29 candidates, 6 + 6 picks, 4 more drift details, 4 FIT downloads, 21 calls).
- [ ] **Step 2: Run** -> fail. **Step 3: Implement**. **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Commit** `feat: DFA alpha1 LT1, method combination and run selection`.

---

### Task 5: `zones` commands, schema, docs, Garmin sync

**Files:** Create `pace/commands/zones.py`; Modify `pace/cli.py`, `pace/commands/schema.py` (entries, group, `known`, manifest block `zones` with `rule`, `lab_valid_days`, `sources`, `due` rules, call budget; writes set in `tests/test_schema.py` gains `zones push`), `docs/CONTRACT.md`, `README.md`, the spec (post-hoc notes); Test `tests/test_zones_cmd.py`, `tests/test_schema.py`.

| command | kind | `data` |
|---|---|---|
| `zones show [--no-cache]` | read (local + zones read + list rows since the last estimate for `due`) | `status(...)` |
| `zones estimate [--weeks 8] [--include-wrist] [--save] [--no-cache]` | read; `--save` local_write | `{"lt1", "lt2", "methods": {...}, "runs_used", "runs_skipped", "calls", "record"?}` |
| `zones analyze ACTIVITY_ID --test drift|time_trial [--treadmill] [--save]` | read; `--save` local_write | analysis + `record`? |
| `zones test-workout drift|time_trial` | pure | `{"workout": DSL}` |
| `zones add --source S [--date] [--lt1-hr] [--lt1-pace] [--lt2-hr] [--lt2-pace] [--max-hr] [--rest-hr] [--method] [--confidence] [--activity ID]... [--notes]` | local_write | `{"record", "current"}` |
| `zones accept ID` | local_write | `{"current"}` |
| `zones remove ID [--force]` | local_write | `{"removed"}` |
| `zones push [--dry-run]` | Garmin write (Task 0 contract) | `run_write` result; dry-run payload `{"before", "after", "body"}` |

  `required_flags`: `zones add` `["--source"]`, `zones analyze` `["--test"]`.
  Paces accept `m:ss` per km (`pace.workout.parse_pace` semantics), stored as s/km.
  `zones estimate` reports `calls` used and never exceeds the 14.7 budget; the skill passes a long enough Bash timeout (documented in CONTRACT: up to about 1 minute cold, near-instant from cache).

- [ ] **Step 1: Write failing tests** with `fake_api` and synthetic tables/FIT fixtures: show combines local + Garmin + `in_sync` + `due`; estimate respects the budget and reports skipped wrist runs; `--save` stores a full record; analyze both tests (+ `--save`); add lab -> current (and older lab not current); accept; remove; push dry-run shows live `before` and makes no write; push sends the Task 0 body, invalidates the zones read, refused without a complete current record or with invalid floors.
- [ ] **Step 2: Run** -> fail. **Step 3: Implement**. **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Live check (controller, read-only)**: `pace zones show`, `pace zones estimate` on the user's account; look at the candidates with the user before anything is saved, accepted or pushed.
- [ ] **Step 6: Docs and commit** `feat: zones commands and Garmin zone sync`.
