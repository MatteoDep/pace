# Coach 5: Thresholds and Zones Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** pace estimates the user's lactate thresholds (LT1, LT2) from past runs (critical speed, HR-speed regression, HR drift in normal runs, DFA alpha1 from RR intervals), analyzes three field tests (drift, time trial, alpha1 ramp), accepts lab values, derives HR and pace zones from the current LT1 and LT2 records, and keeps the watch's HR zones in sync.

**Architecture:** `pace/zones.py` is the threshold store, per-threshold precedence, due rule, HRmax and H4 status memory, estimates log and zone derivation (pure, on `pace/store.py`).
`pace/fit.py` downloads original FIT files, parses them in memory (`fitdecode` behind a thin adapter, a pure `parse_messages` behind it) and caches only derived JSON.
`pace/hrv.py` holds RR artifact correction, DFA alpha1 (banded detrending) and alpha1 windows.
`pace/thresholds.py` holds the pure estimation methods (H1-H5), the alpha1 gate, the field-test analyses (F1, F2, F3), their combination and run selection, operating on list rows, sample tables and RR series.
`pace/commands/zones.py` wraps everything: `run_estimate(fetcher)` orchestrates an estimate through an injected, call-counting fetcher; Garmin reads go through `cached_call`, the zone write through `run_write`.

**Tech Stack:** Python 3.14, typer, fitdecode (new dependency, approved by the user, verified on the user's FIT files), stdlib statistics, pytest; no numpy.

**Spec:** `docs/superpowers/specs/2026-11-17-coach-plan-design.md` section 7, and `docs/superpowers/specs/2026-11-17-zones-methods.md` (the methods doc, approved by the user): its sections 1 (common data rules), 3-9a (methods), 10 (lab records), 11 and 14 (chosen set, zone mapping, due rule, run selection and call budget, F3) are normative; every worked example in section 14 is a required unit test with the values given there.
Plan 5; depends on plans 1-3 (`pace.store`, `pace.plan`, `pace.sports`, `pace.load`'s fetch patterns, `pace.charts.sources.sleep_stats`); plan 4's skill runs after it.

## Global Constraints

- Store `$PACE_HOME/zones.json`: `{"records": [...], "current": {"lt1": id | None, "lt2": id | None}, "next_id": int, "estimates": [...], "hr_max": {"bpm", "activity", "date"} | None, "h4": {"streak": int, "unreliable": bool, "since": date | None, "counted": [ids]}}`, same store rules as diary and plan (lock, atomic 0600 save, corrupt -> `internal` with path hint, local not_found hints never mention Garmin).
- Record: `{"id", "date", "source": "estimate" | "field_test" | "lab", "method", "lt1": {"hr", "pace_s_per_km"}, "lt2": {"hr", "pace_s_per_km"}, "max_hr", "rest_hr", "cs_mps", "d_prime_m", "activities": [ids], "confidence": "low" | "medium" | "high", "notes", "details": {...}}`; threshold values may be null; estimate records store `details.h2 = {"a", "b", "v_min", "v_max"}`.
- Current is per threshold (methods doc 10 and 14.5): `zones accept ID [--only lt1|lt2]` sets current for every threshold the record has (or only the one named; `bad_args` if the record lacks it); `zones add --source lab` sets current for each threshold the lab record has, if the record date is on or after the date of that threshold's current record (an older lab result entered for history changes nothing).
  Lab expiry is per threshold: a current lab record older than 183 days is reported `expired: true` for that threshold (advisory).
  `zones accept ID --only lt1|lt2` is refused (`bad_args`) when the resulting current pair has LT1 HR >= LT2 HR.
  `zones remove ID` deletes a record (refused while it is current for any threshold unless `--force`; then those thresholds become None).
- Zones derive from the current LT1 record + the current LT2 record per methods doc 14.5 (rule "lt-5z-v1"); partial data: three-zone HR needs LT1 and LT2 HR, the five floors need both, pace zones need the paces; z1/z2 pace floors need an H2 (null for lab records); missing pieces are null and `zones push` is refused unless all five floors exist and validate.
- HRmax and resting HR (methods doc 1): `hr_max` is the running maximum in `zones.json`, raised by every `zones estimate`, every `zones analyze`, and a record added with a higher `max_hr`; `rest_hr` = median `restingHeartRate` of the last 28 days from `sleep_stats` (plan 3; 1-2 calls, the chunks are fixed on the calendar).
  With HRmax unknown, zone5Floor uses the 14.5 fallback, and `zones push` keeps Garmin's `maxHeartRateUsed` and validates zone5Floor < it.
- `zones show` returns `current` (per threshold: record id, source, date, method, confidence: the provenance), `expired` (per threshold), `zones`, `records` (all records, newest first, each with `current_for: ["lt1", "lt2"]` or `[]`), `candidates` (records newer than the current record of a threshold they have, not current for it), `due` per methods doc 14.6, per threshold (`{"estimate": bool, "field_test": {"lt1": bool, "lt2": bool}, "reasons": [...]}`), `hr_max`, `h4` (status), `garmin` (the watch zones read) and `in_sync` (the current five floors equal the watch's `zone1Floor..zone5Floor` of the entry pace pushes to).
- HR source: strap if `get_activity`'s `metadataDTO.sensors` contains an entry with `bleDeviceType == "HEART_RATE"` or an ANT+ heart-rate device (field and value pinned by the Task 0 fixtures); sensors and RPE exist only in `get_activity` (not in details, not in list rows); wrist runs are dropped from H2, H4, H5 and F3 unless `--include-wrist`.
- Sample clock: every sample and FIT record carries `t` = elapsed seconds since the activity start (pauses leave timestamp gaps, so the stop rule sees them); the FIT timer clock is stored separately as `timer_t` only if needed.
- Samples: `get_activity_details` is fetched with `maxchart` = 2 x the run's elapsed seconds, capped at 20000, and samples are read by raw epoch timestamps (not `details_table`); stops follow methods doc section 1 only (more than 10 s below 1.0 m/s, or a timestamp gap > 5 s at the fetched resolution).
- Run selection and the call budget exactly as methods doc 14.7: `zones estimate --max-calls N` (default 40) is a hard cap on all Garmin calls including the 16-week extension; each picked run costs one `get_activity` and one `get_activity_details`; the result reports `calls`, `estimated_seconds` (from the `pace.ratelimit` limits) and `deferred`; zones reads of `get_activity` and `get_activity_details` for runs older than 1 day use a 60-day TTL (own cache keys), so a capped or interrupted estimate continues from cache.
- `zones estimate` always appends a summary `{date, lt1_hr, lt2_hr, lt2_pace, methods, confidence, partial}` to `estimates` (a local write of the log, schema `"local_write": true`); `partial` is true when the cap deferred work, and `due` skips partial entries; `--save` additionally stores a full record (source `estimate`).
  `zones analyze` always writes locally (schema `"local_write": true`, with the note that `--save` also stores a record): it raises `hr_max`, and `--test ramp` records its alpha1 gate outcome in the H4 memory; `--save` stores a field-test record per methods doc 14.3, 14.4, 14.8.
- `insufficient_data` is not an error: an estimate is `ok` with per-method `status: "ok" | "insufficient_data" | "skipped" | "alpha1_unusable"` and `reasons`.
- FIT data: the zip is read in memory and only its single `.fit` member is parsed, never extracted to disk; only derived JSON is stored, `$PACE_HOME/fit/<id>.json` (`rr_s`, per-second records without lat/lon, `start`; no device or serial fields), written atomically (tmp + `os.replace`, file 0600, directory 0700); a parse error is reported and nothing is cached; `$PACE_HOME/fit/no_rr.json` (`{id: reason}`, reason `no_hrv_messages` | `rr_rejected`) is updated under the store lock; `pace cache clear` does not clear `$PACE_HOME/fit/` (documented in CONTRACT).
- H4 memory (methods doc 6): runs and ramps count in start-time order, each at most once (ids kept in `counted`; re-evaluating a cached run never increments the streak again); `not_monotonic` takes precedence over `one_sided`; two consecutive "not monotonic" runs or ramps mark H4 unreliable; while marked, estimates skip H4 and make no FIT downloads, until `zones estimate --retry-h4`.
- Heat: no weather calls; every H5 and F1 evidence row reports `temperature` (C from the FIT data, else `"unknown"`); the coach is told heat is unfiltered.
- All arithmetic in pace; every result carries its inputs (activity ids, segment bounds, fit parameters) so the coach can explain it.
- Garmin zone write: endpoint, body and target entry (`sport`) as verified in Task 0; the push body is built from a fresh read (never the cache); `zones push --dry-run` is a documented exception to "dry-run makes no network call": it makes one live read for `before` and no write; success invalidates the zones read key; `hr_zones` is added to the write result resources.
- No real Garmin ids, profile ids or FIT files in the repo: fixtures are synthetic or sanitized (ids replaced, `userProfileId`/serials removed).
- Python 3.14 (`.python-version`); `.venv/bin/python -m pytest -q` and `.venv/bin/ruff check pace tests scripts` clean at every commit; ASCII only; markdown one sentence per line; no Claude attribution; implementers never run `uv` (the controller adds dependencies).

## Review Focus

1. Per-threshold current: an F1 (LT1-only) record accepted keeps the current LT2; `zones accept ID --only lt2` on a full record leaves LT1 alone; a lab record entered 7 months ago stays current for its thresholds with `expired: true`, a newer estimate is a candidate; an older lab result never replaces a newer current record. Test in Task 1.
2. Budget cap: `zones estimate` never makes more than `--max-calls` Garmin calls (extension included), counts `get_activity` and `get_activity_details` per picked run, reports `deferred`, and a rerun reuses the cached reads. Test in Task 5a (the 14.7 worked example: 40 calls, and 30 + 10 with `--max-calls 30`).
3. A steady run with a stop or GPS dropout in the middle: the drift analysis (F1, H5) excludes stopped samples and reports the excluded time, instead of a wrong drift %. Test in Task 3b.
4. Every candidate run is a wrist run: no H2/H4/H5 estimate, with the reason, unless `--include-wrist`. Test in Task 3a and Task 5a.
5. Zone floors not strictly increasing (LT1 HR above LT2 HR): `bad_args` on add, never pushed. Test in Task 1 and Task 5c.
6. Garmin zones changed in the app since the last read: `zones push --dry-run` shows the live Garmin values as `before`, with one read and no write. Test in Task 5c.
7. The RR series and the per-second records are aligned as verified in Task 0 on the elapsed clock (`t` = seconds since the activity start, cumulative RR from the record start, gaps at pauses), so alpha1 windows get the right HR, speed and cadence. Test in Task 2.
8. Alpha1 gates: the athlete's observed ramp pattern (methods doc 6, 14.8) gives `alpha1_unusable`, never an LT1; two in a row mark H4 unreliable and stop FIT downloads. Test in Task 4 and Task 5a.

---

### Task 0: Live verification and fixtures (controller only, with the user)

- [ ] **Step 1: Dependency.** `uv add fitdecode` (PyPI; `.python-version` pins 3.14); full suite green; commit `build: add fitdecode`.
- [ ] **Step 2: Sensors fixture.** From one strap run and one wrist run (`pace activity show ID --raw`, which is `get_activity`), record `metadataDTO.sensors` as sanitized fixtures `tests/fixtures/sensors_strap.json`, `sensors_wrist.json` (serials and ids replaced); confirm RPE is there and not in the details or list rows; record how a list row marks a race (`eventType` key and value).
- [ ] **Step 3: FIT parsing and RR alignment.** On a strap run's FIT file (in memory, not committed): confirm the fitdecode adapter yields `(name, fields)` pairs as `parse_messages` expects; that `t` = record timestamp minus the activity start (elapsed, pauses as gaps) matches the details' epoch minus start, and how the FIT timer clock differs; whether speed and altitude come as `enhanced_speed`/`enhanced_altitude`; the running `cadence` unit (strides/min) and `fractional_cadence`; whether `record` carries temperature.
  Compare cumulative RR time with the `record` timestamps (start offset, behavior at auto-pause and stops, drift over the run); write the verified alignment rule into Task 2 and methods doc section 6; build a synthetic fixture `tests/fixtures/fit_rr_small.json` (a list of `(name, fields)` messages: RR + records) that reproduces the rule.
  A tiny real FIT fixture is added only if it can be fully sanitized (no ids, serials, GPS); otherwise none.
- [ ] **Step 4: Zone write.** With the user's yes: read `GET /biometric-service/heartRateZones` (list per `sport`), capture the request the Garmin Connect web app sends when HR zones are saved (Playwright, network tab) or try a `PUT` of the read payload unchanged; then change one floor by 1 bpm, read back, check the next day and on the watch, revert.
  Record method, path, body fields, which entry to write (`DEFAULT` or `RUNNING`, and whether `trainingMethod`/`restingHrAutoUpdateUsed` must be set so Garmin keeps custom floors) in this plan (Task 5c) and the spec, with ids redacted; never commit captured requests, cookies or HAR files.
  If nothing verifies, Task 5c drops `zones push` and `zones show` prints the floors for the user to enter in the Garmin app.
- [ ] **Step 5: Detail resolution.** On a run longer than 4000 s, call `get_activity_details` with `maxchart` = 2 x elapsed seconds and check the sample spacing (about 1 s, not 4 s) and the raw epoch timestamp field; pin the unit of `directRunCadence` (steps/min or strides/min) against the list row's `averageRunningCadenceInStepsPerMinute`; write both into Task 3a.

---

### Task 0 results (controller, 2026-11-18)

- Step 1: fitdecode 0.11.0 added (commit 3f9911b).
- Step 2: `tests/fixtures/sensors_strap.json` (one `bleDeviceType: "HEART_RATE"` BLE sensor) and `sensors_wrist.json` (`sensors: null`, the key is null, not an empty list); RPE (`summaryDTO.directWorkoutRpe`) is only on `get_activity`, not in details nor list rows; a race is a list row with `eventType.typeKey == "race"` (1 of the athlete's 338 activities; their 42 km run is `uncategorized`).
- Step 3: fitdecode yields data messages with `.name` and `.fields` (name/value), so the adapter maps them to `(name, {field: value})`; records are 1 Hz with `timestamp`, `heart_rate`, `speed` and `enhanced_speed`, `altitude` and `enhanced_altitude`, `distance`, `cadence` in strides/min plus `fractional_cadence` (steps/min = 2 x (cadence + fractional_cadence)), GPS fields (dropped); no temperature on this watch; `session.start_time` equals the first record timestamp.
  Elapsed `t` = record timestamp - start: an auto-pause leaves a gap in the records (731 s in the checked run, matching timer stop/start events), and no `hrv` beats are logged during the pause.
  RR alignment: `hrv` messages are interleaved after records; plain cumulative RR drifts behind elapsed time (up to 95 s over 3 h, missing beats); rule: start the beat clock at the elapsed `t` of the record preceding the first `hrv` message, add each RR, and when an `hrv` message is reached (before its beats are added) resync to the preceding record's `t` if the offset exceeds 3 s; the RR sanity span excludes pause gaps (44 resyncs in 3 h; 95% of offsets within 2.6 s; one large resync after a pause).
  Fixture: `tests/fixtures/fit_rr_small.json` (synthetic messages with a pause) reproduces the rule.
- Step 5: `get_activity_details(id, maxchart=2 x elapsed)` returns every sample at 1 s spacing (2379 vs 1199 at maxchart 2000); timestamps are `directTimestamp` epoch ms; `directRunCadence` is strides/min (90 vs list row 179 steps/min), `directDoubleCadence` exists alongside.
- Step 4 (zone write, user's yes 2026-11-18): `GET /biometric-service/heartRateZones` returns a list with one entry here, `sport: "DEFAULT"`, fields `trainingMethod` (`HR_RESERVE`), `restingHeartRateUsed`, `lactateThresholdHeartRateUsed` (null), `zone1Floor`..`zone5Floor`, `maxHeartRateUsed`, `restingHrAutoUpdateUsed` (false), `sport`, `changeState` (`UNCHANGED`), plus the owner's profile id.
  Write: `PUT /biometric-service/heartRateZones` (connectapi, JSON body = the full list as read) returns 204 with no body.
  The unchanged list changed nothing; the list with `zone1Floor` +1 and that entry's `changeState: "CHANGED"` read back with the new floor (and `changeState` back to `UNCHANGED`), with `trainingMethod` left as read.
  Still open (user): the floor on the watch after a sync and on the next day; then revert.
  Ruling: Task 5c writes the floors (and `maxHeartRateUsed` when HRmax is known) into every entry with `sport` `DEFAULT` or `RUNNING`, sets their `changeState` to `CHANGED`, keeps every other field and entry as read; refuses when no such entry exists.

### Task 1: Zone store, precedence, derivation, due rule

Can run before Task 0 completes (no Garmin data needed).

**Files:** Create `pace/zones.py`; Test `tests/test_zones.py`.

**Interfaces:**

```python
SOURCES = ("estimate", "field_test", "lab")
THRESHOLDS = ("lt1", "lt2")
LAB_VALID_DAYS = 183
ZONE_RULE = "lt-5z-v1"
def R(x: float) -> int                                     # floor(x + 0.5), methods doc 14.5
def load() -> dict
@contextmanager
def transaction() -> Iterator[dict]
def add_record(data: dict, record: dict, *, today: date) -> dict   # lab: per-threshold current rule; raises hr_max if max_hr higher
def accept(data: dict, record_id: int, *, only: str | None = None) -> dict
def remove(data: dict, record_id: int, *, force: bool) -> dict
def current(data: dict) -> dict                            # {"lt1": record | None, "lt2": record | None}
def derive(record_lt1: dict | None, record_lt2: dict | None, *, h2: dict | None, hr_max: int | None) -> dict
    # {"rule", "three_hr": [...], "three_pace": [...], "floors": {"zone1Floor".."zone5Floor", "maxHeartRateUsed"} | None,
    #  "pace_floors": {"z1".."z5"}}; methods doc 14.5; h2 from the LT1 record's details, else the LT2 record's
def log_estimate(data: dict, summary: dict) -> None        # appends to "estimates"
def update_hr_max(data: dict, bpm: int, *, activity: int, day: date) -> bool
def record_alpha1_outcome(data: dict, outcome: str, *, day: date) -> None   # "usable" | "not_monotonic" | "one_sided"; streak, unreliable
def due(data: dict, *, today: date, runs_since: list[dict]) -> dict      # methods doc 14.6, per threshold, reads "estimates"
def status(data: dict, *, today: date, garmin_zones: list[dict] | None, runs_since: list[dict]) -> dict
```

  Validation (`bad_args`): source in SOURCES; date parses, not in the future; HR values int 30..230; LT1 HR < LT2 HR < max HR when present; LT1 pace slower than LT2 pace; at least one threshold value; `--only` names a threshold the record has.

- [ ] **Step 1: Write failing tests**: add/accept/current/remove (+ `--force` for current); accept of an LT1-only record keeps current LT2; `--only`; lab newer becomes current per threshold, lab older does not, lab with only LT2 leaves LT1; lab older than 183 days `expired` for its thresholds and stays current, newer estimate in `candidates`; invalid orderings -> `bad_args`; `R(312.5) == 313` (unlike `round()`), `R(142.3) == 142`; `derive` reproduces methods doc 14.5 worked example exactly (H2 fitted on 2.0..4.0 m/s; floors 114, 128, 142, 160, 173; pace floors 463, 368, 304, 250, 235; HRmax unknown -> 169), z1 null with the 14.1 range 2.8..3.8, z1/z2 null when both the current LT1 and LT2 records are lab records (no H2 anywhere); `accept --only` refused when the resulting pair has LT1 HR >= LT2 HR; `due` skips `partial` estimates; a counted activity id never raises the H4 streak twice; partial records give null pieces; `due` reproduces the 14.6 worked example per threshold from `estimates`; `hr_max` only rises; the H4 streak (2 x not_monotonic -> unreliable; usable resets; one_sided unchanged); `status` returns `records` newest first with `current_for`; `in_sync` true/false against a Garmin zones list; corrupt file -> `internal`.
- [ ] **Step 2: Run** -> fail. **Step 3: Implement**. **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Commit** `feat: threshold store, zone derivation and due rule`.

---

### Task 2: FIT files, RR artifacts, DFA alpha1

**Files:** Create `pace/fit.py`, `pace/hrv.py`; Test `tests/test_fit.py`, `tests/test_hrv.py`.

**Interfaces:**

```python
# pace/fit.py
def fit_dir() -> Path                                   # $PACE_HOME/fit/ (0700)
def parse_messages(messages: Iterable[tuple[str, dict]]) -> dict
    # pure: {"rr_s": [float], "records": [{"t", "hr", "speed", "dist", "alt", "cadence", "temp"?}], "start": iso}
    # t = elapsed seconds since the activity start (pauses leave gaps); timer_t only if needed; speed = enhanced_speed or speed; alt = enhanced_altitude or altitude;
    # cadence in steps/min = 2 x (cadence + fractional_cadence); lat/lon and device fields dropped
def read_fit_bytes(data: bytes) -> dict                 # thin fitdecode adapter: zip in memory, the single .fit member -> parse_messages
async def get_fit(activity_id: int, *, tracker) -> dict | None
    # cached <id>.json if present (no call); else one safe_call("download_activity", id, ORIGINAL) -> read_fit_bytes;
    # atomic write (tmp + os.replace, 0600); parse error -> reported, nothing cached; no hrv messages -> mark_no_rr(id, "no_hrv_messages")
    # (the orchestrator marks "rr_rejected" when aligned_rr raises on the section 6 sanity check)
def aligned_rr(fit: dict) -> list[dict]                 # [{"t_s", "rr_s", "hr", "speed", "dist", "alt", "cadence"}] per the Task 0 rule
def no_rr(activity_id: int) -> str | None / mark_no_rr(activity_id, reason)   # $PACE_HOME/fit/no_rr.json under the store lock

# pace/hrv.py
def correct_artifacts(rr: list[float]) -> tuple[list[float], list[bool]]   # methods doc section 6 (21-beat median, 20% band, split/merge/replace, > 2 s = gap)
def smoothness_detrend(x: list[float], lam: float = 500.0) -> list[float]  # banded pentadiagonal LDL' solve, O(N)
def dfa_alpha1(rr: list[float], *, n_min: int = 4, n_max: int = 16) -> float | None
def windows(aligned: list[dict], *, size_s: int = 120, step_s: int = 5) -> dict
    # {"windows": [{alpha1, hr, speed, cadence, artifact_share, beats, t_mid}], "dropped_coupling": int}; qualifying rules of section 6
```

- [ ] **Step 1: Write failing tests**: the artifact-correction worked example of 14.2 exactly; `smoothness_detrend` equals a dense reference solve on a small N (written in the test) within 1e-9; the alpha1 statistical tests of 14.2 with 50 seeded series per case and the stated tolerances (white 0.58 +/- 0.03, 1/f 1.03 +/- 0.05, Brownian 1.43 +/- 0.05 with detrending (1.50 +/- 0.05 without), affine invariance, constant -> None, detrending, artifact robustness); window qualification (4% vs 6% artifacts, 190 beats skipped, coupling: HR 176 at cadence 185 dropped and counted, HR 160 at cadence 172 kept, HR 88 at cadence 172 dropped by the cadence / 2 check); `parse_messages` on the JSON fixture (enhanced fields preferred, cadence doubled with the fraction, lat/lon absent); `aligned_rr` on the Task 0 fixture; `get_fit` uses the cache without a call, writes atomically with mode 0600, caches nothing on a parse error, and marks `no_hrv_messages`; a zip with a path-traversal member name is read without touching the disk.
- [ ] **Step 2: Run** -> fail. **Step 3: Implement**. **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Commit** `feat: FIT parsing, RR artifact correction and DFA alpha1`.

---

### Task 3a: History methods H1, H2, H3

**Files:** Create `pace/thresholds.py`; Test `tests/test_thresholds.py`.

**Interfaces:**

```python
def hr_source(activity: dict) -> str                        # "strap" | "wrist", from get_activity (Task 0 fixtures)
def samples(details: dict) -> list[dict]                    # raw epoch timestamps -> [{"t", "hr", "speed", "dist", "alt", "cadence"}], cadence in steps/min (Task 0 Step 5)
def moving_samples(rows: list[dict]) -> tuple[list[dict], int]   # methods doc 1 stop rule only (> 10 s below 1.0 m/s, gap > 5 s); excluded seconds
def max_hr(table: list[dict]) -> int | None               # highest 5-s rolling median HR, first 300 s excluded, HR 30..230; None for no data (callers pass strap runs only)
# runs = [{"id", "hr_source", "table"}]; results carry "runs": [{"id", "used": bool, "reason"?}] (per-run usage and skip reasons)
def critical_speed(runs: list[dict], *, prs: list[dict] | None) -> dict   # H1: windows 180/420/720 s, OLS, gates, confidence, status; wrist runs used (speed only)
def hr_speed(runs: list[dict], *, include_wrist: bool = False) -> dict    # H2: segments, OLS a, b, SEE, n, v_min, v_max, status; wrist runs skipped with reason
def lt1_from_cs(cs: dict, h2: dict | None) -> dict          # H3
```

- [ ] **Step 1: Write failing tests**: every worked example of 14.1 (CS 4.000, D' 180, LT2 HR 160), 14.2 H3 (142 bpm, 304 s/km); `max_hr` ignores a 200 bpm spike in the first 300 s and a single-sample spike later (the 5-s median removes it), drops HR outside 30..230, returns None without HR; per-run `runs` usage and skip reasons; `moving_samples` with a 12 s stretch at 0.8 m/s (stop) vs a 9 s stretch (not a stop) and a 6 s timestamp gap (stop); wrist runs dropped from H2 with the reason; `insufficient_data` status with reasons (not an error); `hr_source` on both fixtures.
- [ ] **Step 2: Run** -> fail. **Step 3: Implement**. **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Commit** `feat: threshold history methods H1-H3`.

---

### Task 3b: H5, field tests F1 and F2, test workouts

**Files:** Modify `pace/thresholds.py`; Test `tests/test_thresholds.py`.

**Interfaces:**

```python
def drift_candidate(row: dict, rows: list[dict]) -> bool    # H5 list-row filter incl. the 24 h after a race / aerobicTrainingEffect >= 4.0 rule
def drift_segment(run: dict, *, fit_records: list[dict] | None, include_wrist: bool = False) -> dict
    # H5 step 1, run = {"id", "hr_source", "table"}: evidence row {id, h, drift, label, bounds, length_s, temperature} | {"id", "rejected": reason};
    # wrist -> rejected unless include_wrist; temperature = mean FIT record temp over the segment, matched on elapsed t
def drift_bounds(evidence: list[dict]) -> dict              # H5 step 2 (section 7, 14.2): bounds, errors, estimate, confidence, notes
def drift(table: list[dict], *, laps: list[dict], treadmill: bool, fit_records: list[dict] | None = None,
          f3_hr: int | None = None) -> dict                  # F1 drift %, verdict, temperature (elapsed-t match), saveable, confidence (F3 downgrade, 14.3)
def time_trial(table: list[dict], *, laps: list[dict]) -> dict              # F2 (14.4), confidence
def test_workout(kind: str, *, lt1_record: dict | None, estimate_lt1_hr: int | None, h2: dict | None) -> dict
    # DSL for "drift" | "time_trial" | "ramp" per 14.3, 14.4, 14.8 (record -> estimate HR -> RPE notes fallback); must pass build_workout
```

- [ ] **Step 1: Write failing tests**: 14.2 H5 bounds examples (evidence rows only, incl. one-sided and E = 1), H5 segment examples (40-min rejection, 27 C rejection from FIT temperature matched on elapsed t, `temperature: "unknown"`, wrist run rejected with reason unless `include_wrist`), H5 candidate examples (20 h vs 30 h after a race or a run with `aerobicTrainingEffect` 4.2); 14.3 (4.94% at LT1 and saveable; 2.50% below; 8.16% above; 35-min rejected; uneven pace not saveable); 14.4 (LT2 HR 168, 250 s/km, no fade; a faded TT has confidence low); a stop in the middle excluded and reported (Review Focus 3); `test_workout("drift")` with a current LT1 record at 304 s/km has a steady step with pace target 294..314 s/km; with only an estimate `lt1_hr` 142, an HR target 139..145 on the warm-up end and no steady target; with neither, RPE notes and no targets; `test_workout("ramp")` from LT1 142 gives 8 steps at 121, 127, 133, 139, 145, 151, 157, 163 bpm, pace targets with an H2 (HR targets for steps whose speed is outside the 14.5 10% range, for example 121 bpm with an H2 fitted on 2.8..3.8 m/s), HR targets from an estimate, RPE notes with nothing; every output passes `build_workout`; F1 confidence low when `f3_hr` differs by more than 5 bpm.
- [ ] **Step 2: Run** -> fail. **Step 3: Implement**. **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Commit** `feat: drift bounds, field test analysis and test workouts`.

---

### Task 4: H4 and F3 from RR data, combination, run selection

**Files:** Modify `pace/thresholds.py`; Test `tests/test_thresholds.py`.

**Interfaces:**

```python
def spearman(x: list[float], y: list[float]) -> float       # average ranks for ties
def alpha1_gate(points: list[dict]) -> dict                 # {"usable": bool, "outcome": "usable" | "not_monotonic" | "one_sided", "rho", "reasons"}
def alpha1_lt1(run_windows: list[dict]) -> dict             # H4: per-run gate, regressions, 0.75 crossing, median, spread, confidence, per-run outcomes
def ramp(aligned: list[dict], *, laps: list[dict], treadmill: bool) -> dict   # F3 (section 9a, 14.8): per-step windows, coupling drop, gate, fit, saveable
def combine_lt1(h3: dict | None, h4: dict | None, h5: dict | None, h2: dict | None) -> dict   # 14.2 combination
def select_history(rows_8w: list[dict], *, exclude: set[int] = frozenset()) -> dict
    # 14.7 phase 1: {"effort": [...], "steady": [...], "skipped": [{"id", "reason"}]}; list-row ranking only
def select_drift(rows_12w: list[dict], *, ref_hr: int | None) -> dict
    # 14.7 phase 2: alternate below/above ref, continue on the other side; percentiles without ref
def select_fit(detailed: list[dict], *, ref_hr: int | None, cached: set[int], no_rr: set[int], rr_since: date, unreliable: bool) -> list[int]   # 14.7 span score, <= 6, [] if unreliable
```

  Percentiles are nearest-rank (value at rank ceil(p / 100 x n), ascending); every tie is broken newest first, then lowest id; with fewer candidates than slots, all are picked once, never duplicated.

- [ ] **Step 1: Write failing tests**: the H4 regression worked example (runs A, B -> 146 bpm, 291 s/km, medium) with alpha1 injected; the one-sided run (outcome `one_sided`, note); the H4 gate test on the observed pattern (40 coupling drops, rho +0.49, `not_monotonic`); the F3 worked examples of 14.8 (145 bpm, 294 s/km, medium; 6% artifact step -> `one_sided`; the observed 13-step ramp -> 2 coupling drops, rho 0.49, `alpha1_unusable`); `spearman` with ties; the combination worked examples (144 / 298 s/km medium; disagreement -> H5 142 low with F1 suggestion at 147; fallback to H3); selection: effort ranking from list rows (race, then `maxHR`, then `averageSpeed`), nearest-rank steady picks with ties and fewer candidates than slots, drift alternation when one side runs out, `select_fit` returns [] when H4 is unreliable.
- [ ] **Step 2: Run** -> fail. **Step 3: Implement**. **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Commit** `feat: DFA alpha1 LT1 and ramp test, method combination and run selection`.

---

### Task 5a: Estimate orchestrator

**Files:** Create `pace/commands/zones.py` (orchestrator part); Modify `pace/commands/_helpers.py` (`cached_call` gains `key_prefix: str | None = None`, so zones reads use their own keys, for example `zones:get_activity:ID`, with the 60-day TTL); Test `tests/test_zones_estimate.py`, `tests/test_helpers.py` (key prefix).

**Interfaces:**

```python
class Fetcher(Protocol):                                    # injected; checks the cache first; counts calls; real one wraps cached_call / get_fit
    # at the cap: a cache hit is still served, a miss makes no call and raises Deferred (listed in "deferred")
    async def list_rows(self, start: date, end: date) -> list[dict]
    async def activity(self, activity_id: int) -> dict      # get_activity, 60-day TTL if older than 1 day
    async def details(self, activity_id: int, *, maxchart: int) -> dict
    async def prs(self) -> list[dict]
    async def sleep(self, start: date, end: date) -> list[dict]
    async def fit(self, activity_id: int) -> dict | None
    calls: int                                              # Garmin calls made (cache hits not counted)
def estimated_seconds(calls: int, *, min_interval: float, per_minute: int, per_hour: int) -> int   # methods doc 14.7 formula, pace.ratelimit limits
async def run_estimate(fetcher: Fetcher, data: dict, *, today: date, weeks: int = 8, max_calls: int = 40, include_wrist: bool = False,
                       retry_h4: bool = False) -> dict
    # {"lt1", "lt2", "methods": {name: {"status", "reasons", ...}}, "runs_used", "runs_skipped", "deferred",
    #  "calls", "estimated_seconds", "hr_max", "rest_hr", "summary"}; updates hr_max, h4 streak, estimates log (partial) in data;
    #  owns mark_no_rr(id, "rr_rejected") when aligned_rr raises
```

- [ ] **Step 1: Write failing tests** with a fake fetcher on synthetic list rows and tables: the 14.7 worked example (29 candidates, 6 + 6 history picks, 4 new drift runs, 4 FIT downloads, 40 calls, `estimated_seconds` 198); the same with `max_calls=30` (30 calls, 3 drift runs and 4 FIT downloads in `deferred`, log entry `partial: true`) and a capped rerun on the caching fake with `max_calls=30` (the 30 cached reads served at no cost, 10 more calls, same result, not partial); `estimated_seconds` for 0, 1, 40 and 101 calls (101 -> 3600); `--weeks 4` reads 12 weeks of list rows and extends to 8; a wrist steady pick costs its `get_activity` call and is replaced by the next candidate; `aligned_rr` raising -> `rr_rejected`; a cached run evaluated again does not raise the H4 streak; the 16-week extension stays within the cap; wrist steady/drift picks are not fetched in detail; all-wrist candidates -> H2/H4/H5 `insufficient_data` with reasons (Review Focus 4); H4 unreliable -> H4 `skipped`, no FIT call; two not-monotonic runs mark it unreliable; `hr_max` updated; the summary is appended to `estimates` with or without save.
- [ ] **Step 2: Run** -> fail. **Step 3: Implement**. **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Commit** `feat: zones estimate orchestrator with call budget`.

---

### Task 5b: `zones` commands, schema, docs

**Files:** Modify `pace/commands/zones.py`, `pace/cli.py`, `pace/commands/schema.py` (entries, group, `known`, manifest block `zones` with `rule`, `lab_valid_days`, `sources`, `due` rules, `max_calls` default and the budget rule), `docs/CONTRACT.md`, `README.md`, the spec (post-hoc notes); Test `tests/test_zones_cmd.py`, `tests/test_schema.py`.

| command | kind | `data` |
|---|---|---|
| `zones show [--no-cache]` | read (local + zones read + list rows since the last estimate for `due`) | `status(...)` |
| `zones estimate [--weeks 8] [--max-calls 40] [--include-wrist] [--retry-h4] [--save] [--no-cache]` | local_write (`true`: estimates log, hr_max, H4 status; `--save` adds a record) | `run_estimate(...)` + `"record"`? |
| `zones analyze ACTIVITY_ID --test drift\|time_trial\|ramp [--treadmill] [--save]` | local_write (`true`: hr_max, H4 gate outcome for `ramp`; `--save` also stores a record) | analysis + `"record"`? (calls: `get_activity`, `get_activity_details`, `get_activity_splits`, FIT for `ramp` unless cached) |
| `zones test-workout drift\|time_trial\|ramp` | pure (local read of the current records and `estimates`) | `{"workout": DSL}` |
| `zones add --source S [--date] [--lt1-hr] [--lt1-pace] [--lt2-hr] [--lt2-pace] [--max-hr] [--rest-hr] [--method] [--confidence] [--activity ID]... [--notes]` | local_write | `{"record", "current"}` |
| `zones accept ID [--only lt1\|lt2]` | local_write | `{"current"}` |
| `zones remove ID [--force]` | local_write | `{"removed", "current"}` |

  `required_flags`: `zones add` `["--source"]`, `zones analyze` `["--test"]`.
  Paces accept `m:ss` per km (`pace.workout.parse_pace` semantics), stored as s/km.
  `zones analyze --save` refusals (`bad_args`, nothing stored, the analysis still in the error detail): F1 other than "at LT1" with even pace (with the verdict and the next-test suggestion); F2 rejected tests; F3 `alpha1_unusable` or not bracketed (steps with artifacts > 5% are already dropped).
  F2 saves with confidence low if faded or stops; F1 with confidence low if the newest F3 of the last 28 days disagrees by more than 5 bpm (methods doc 9a).
  CONTRACT and the skill note: a cold `zones estimate` takes up to about 4 minutes (40 calls at 10 per minute plus response times), near-instant from cache; callers use a 10-minute Bash timeout; `pace cache clear` does not clear `$PACE_HOME/fit/`.

- [ ] **Step 1: Write failing tests** with `fake_api` and synthetic tables/FIT fixtures: show combines local + Garmin + `in_sync` + `due` + `records` with `current_for` + per-threshold provenance; estimate without `--save` appends to `estimates` and stores no record, with `--save` stores a full record; schema markers `zones estimate` and `zones analyze` both `"local_write": true` (the analyze help notes that `--save` also stores a record); `zones analyze --test ramp` without `--save` records the gate outcome and stores no record; analyze drift/time_trial/ramp with laps (+ `--save` and each refusal); add lab -> current per threshold (and older lab not current); accept with and without `--only` (refused when it would pair LT1 HR >= LT2 HR); remove.
- [ ] **Step 2: Run** -> fail. **Step 3: Implement**. **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Live check (controller, read-only on Garmin)**: `pace zones show`, `pace zones estimate` on the user's account (it logs the estimate locally); look at the candidates with the user before anything is saved, accepted or pushed.
- [ ] **Step 6: Docs and commit** `feat: zones commands`.

---

### Task 5c: Garmin zone sync (after Task 0 Step 4)

**Files:** Modify `pace/commands/zones.py`, `pace/writes.py` (resource `hr_zones`), `pace/commands/schema.py` (writes set in `tests/test_schema.py` gains `zones push`), `docs/CONTRACT.md` (writes section: the `zones push --dry-run` exception and `hr_zones` in the `resource` list); Test `tests/test_zones_cmd.py`.

| command | kind | `data` |
|---|---|---|
| `zones push [--dry-run]` | Garmin write (Task 0 contract) | `run_write` result, resource `hr_zones`; dry-run payload `{"before", "after", "body"}` |

  `--dry-run` makes exactly one live read (never the cache) to build `before` and `body`, and no write; its option help says so instead of the shared "make no network call" text.
  With HRmax unknown the body keeps Garmin's `maxHeartRateUsed`, and zone5Floor must be below it.

- [ ] **Step 1: Write failing tests**: push dry-run shows live `before`, makes one read and no write (Review Focus 6); push sends the Task 0 body, invalidates the zones read, refused without a complete current LT1 + LT2 or with invalid floors (Review Focus 5); HRmax unknown keeps Garmin's `maxHeartRateUsed` and refuses zone5Floor >= it.
- [ ] **Step 2: Run** -> fail. **Step 3: Implement**. **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Docs and commit** `feat: Garmin zone sync`.
