# Zones data quality and race efforts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the history threshold methods usable on GPS-only watch data, gate DFA alpha1 against step interference, keep every method's result visible with its reliability, and add race efforts as an LT2 source.

**Architecture:** Rule values and shared signal helpers stay in `pace/rules.py`; `pace/thresholds.py` and `pace/hrv.py` consume them; the race method is a fourth `zones analyze --test` kind plus maximal-effort points in the H1 fit; the methods doc stays normative and is updated in the same task as the code it describes.

**Tech Stack:** Python 3.14, numpy-free pure Python in `pace/` (as today), pytest, ruff.

**Spec:** docs/superpowers/specs/2026-11-17-zones-methods.md (normative for methods) and docs/superpowers/specs/2026-11-17-coach-plan-design.md section 7. The changes below were agreed with the user on 2026-11-18 (chat); each task writes them into the methods doc, marked as user decisions or pace heuristics.

## Global Constraints

- Every limit is a named constant (rule values in `pace/rules.py`, method constants at the top of their module); user-facing reasons are built from the names, never from literals.
- `pace` modules stay free of numpy/scipy (pure Python, as `pace/hrv.py` today).
- Machine-first CLI: one JSON envelope per call, `bad_args` exit 2, no network in tests, synthetic activity ids only (never real Garmin ids).
- pace provides methods and data; the coach and the athlete decide the zones. Every method result stays visible with its reliability; a method judged unreliable is flagged and kept out of the suggested value, never hidden.
- Methods doc: one sentence per line; each new rule cites its source or is marked "pace heuristic"; record user decisions as "user decision 2026-11-18".
- Commits: no Claude attribution.

## Review Focus

1. A flat run with GPS altitude noise (random walk of +/-10 m over a km, 1 s jitter of 1-3 m) must pass the flat checks; a real hill (30 m over 1 km) must still fail.
2. Altitude samples with None and series shorter than the smoothing window must not crash or shift the result.
3. A window whose RR oscillation at |step rate - HR| holds more than STEP_SHARE_MAX of the power is dropped; when |step rate - HR| is below the analysed band, the existing coupling rule applies.
4. A race shorter than 120 s, between the two duration classes, or longer than 3600 s gets a clear refusal or "check only" result, never a silent wrong estimate.
5. An H4 value produced in the same estimate that marks alpha1 unreliable is visible but flagged and not combined.

---

### Task 1: Altitude smoothing and drift run window

**Files:** Modify `pace/rules.py`, `pace/thresholds.py`, `pace/hrv.py`, `tests/test_rules.py`, `tests/test_thresholds.py` (or the existing test file that covers windows), `docs/superpowers/specs/2026-11-17-zones-methods.md` (sections 1, 7, 14.7, 14.9).

**Interfaces:**
- Produces: `rules.ALT_SMOOTH_S = 61`; `rules.smooth_altitude(times: list[float], alts: list[float | None], *, width_s: float = ALT_SMOOTH_S) -> list[float | None]` (centered moving mean over samples with |t - t_i| <= width_s / 2, None samples skipped in the mean and kept None in the output); `rules.ascent_profile` and `rules.ascent_m` unchanged in signature (they stay pure hysteresis); every caller that measures ascent or grade passes smoothed altitudes.
- Values: `H5_RUN_S = (3600, 9000)`; `DRIFT_MAX_GAIN = 15.0` (no longer tied to `MAX_ASCENT_M_PER_KM`; it pre-filters on Garmin's whole-run `elevationGain`, which carries the same noise); `MAX_ASCENT_M_PER_KM` stays 10.0, `ASCENT_HYSTERESIS_M` stays 2.0, `MAX_GRADE` stays 0.02, `SEL_MAX_GAIN` stays 15.0.

- [ ] **Step 1: Failing tests.**
  `test_smooth_altitude_centered_mean` (constant series unchanged; a single 10 m spike in a 1 Hz series is divided by 61; None stays None and is skipped in neighbours' means; non-uniform times use the time window, not a sample count).
  `test_noisy_flat_block_passes`: a 1 Hz synthetic 20-min run at 3 m/s whose altitude is a seeded random walk (step sd 0.5 m) plus 1 s jitter (sd 1.5 m), total range about 20 m, passes `_Block.window_ok` on at least 90% of 300 s windows and yields an H5 segment of 45 min when speed is steady.
  `test_real_hill_fails`: same run with a 30 m rise over 1 km fails the windows that contain the rise.
  `test_beats_flat_uses_smoothing` for `hrv._flat` (beats about 0.4 s apart: the time window, not a sample count, decides).
  `tests/test_rules.py`: keep the H5 consistency check (`H5_RUN_S[0] >= H5_START_S + H5_MIN_S`) and assert `H5_RUN_S == (3600, 9000)`.
- [ ] **Step 2: Run** -> fail.
- [ ] **Step 3: Implement.** `_Block.__init__` smooths `alt` on its 1 s grid before `ascent_profile` and before the net-grade check (store the smoothed series and use it for both); `hrv._flat` smooths by beat time; `_ascent_per_km` and the F1/F2 terrain checks use the same smoothed series (find every `ascent_m`/`ascent_profile`/`MAX_GRADE` use and route it through smoothed altitudes).
- [ ] **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Methods doc.** Section 1: the Forerunner 55 altitude is GPS-derived: on flat Padua runs it wanders 10-25 m per km and 1 s samples jitter by metres (observed 2026-11-18: 5-min windows read 13-18 m/km of climb, 80-93% failed the 10 m/km limit; with 61 s smoothing 1-11% failed, hilly runs still failed 35-100%); rule: centered 61 s moving mean before grade and ascent (pace heuristic); bridges (4-8 m) are below this noise and are not detected (user decision 2026-11-18: accepted). Sections 7 / 14.7: H5 run window 60-150 min and the 15 m/km list-row pre-filter (user decision 2026-11-18). 14.9: note the observed effect.
- [ ] **Step 6: Commit** `fix: smooth GPS altitude before terrain checks`.

### Task 2: Step-interference gate for DFA alpha1, and flagged-not-hidden H4

**Files:** Modify `pace/hrv.py`, `pace/thresholds.py` (H4 combination, F3 step analysis), `pace/commands/zones.py` (`_h4` and the estimate merge), `tests/test_hrv.py`, `tests/test_thresholds.py`, `tests/test_zones_cmd.py`, methods doc sections 6, 9a, 14.2, 14.8, 14.9.

**Interfaces:**
- Produces in `pace/hrv.py`: `STEP_SHARE_MAX = 0.25`, `STEP_BAND_HZ = 0.05`, `SPECTRUM_FS = 4.0`, `SPECTRUM_SEG_S = 64`, `SPECTRUM_MIN_HZ = 0.04`, `SPECTRUM_MAX_HZ = 1.9`;
  `step_share(times: list[float], rr: list[float], *, step_rate_spm: float, hr_bpm: float) -> float | None` = power of the RR tachogram (linear interpolation at SPECTRUM_FS, linear detrend, Welch with Hann segments of SPECTRUM_SEG_S and 50% overlap) within STEP_BAND_HZ of |step_rate - hr| / 60 Hz, divided by the power in SPECTRUM_MIN_HZ..SPECTRUM_MAX_HZ; None when |step_rate - hr| / 60 - STEP_BAND_HZ < SPECTRUM_MIN_HZ (the existing coupling rule covers that case) or the window is shorter than one segment.
  Pure Python DFT is fine (segments of 256 points, at most 1.9 Hz of bins needed).
- `windows(...)` result gains `dropped_step: int` and each kept window gains `step_share`; a window with `step_share > STEP_SHARE_MAX` is dropped after the coupling check (same counting convention as `dropped_coupling`).
- F3 ramp steps: a step whose analysed span has `step_share > STEP_SHARE_MAX` is excluded like a coupled step, with its share in the step row.
- H4 in an estimate: when the H4 memory update of this same estimate marks alpha1 unreliable, the H4 method result keeps its values but gains `flags: ["alpha1_unreliable"]` and a reason, and the LT1 combination ignores it; the same flag applies when the memory was already unreliable and `--retry-h4` was not given (today that path returns `skipped` with no values; keep `skipped`, it has no values to show).

- [ ] **Step 1: Failing tests.**
  `test_step_share_synthetic`: tachogram = 0.6 s + 20 ms sine at 0.65 Hz + small white noise, step 173 spm, HR 134 bpm (|diff| = 0.65 Hz) -> share > 0.5; same without the sine -> share < 0.15; HR 176 vs 180 spm -> None.
  `test_windows_drop_step_dominated` and `dropped_step` count.
  `test_f3_step_excluded_on_step_share`.
  `test_h4_flagged_when_marked_in_same_call` (estimate with two not_monotonic runs then a usable run: H4 keeps `hr`, has the flag, `lt1.method != "H4"`).
- [ ] **Step 2: Run** -> fail. **Step 3: Implement.** **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Methods doc.** Section 6: observation on the athlete's ramp, warm-up and cool-down (Polar H10, 2026-09-09): the dominant RR oscillation sat within 0.05 Hz of |step rate - HR| in 98% of ramp windows (57% warm-up, 85% cool-down), its period in beats (HR / |step rate - HR|) ran from about 3 to over 20 beats and alpha1 followed it (low at 3-4 beats, above 1.0 past 16 beats), breathing at 2:2 or 3:3 did not match; windows with share <= 0.25 read alpha1 0.83-1.17 at easy effort; the gate and its threshold are a pace heuristic (no published validation found); the strap (Polar H10) makes a measurement artifact unlikely. 14.2: H4 flagged-not-hidden rule. 9a/14.8: F3 step exclusion.
- [ ] **Step 6: Commit** `feat: step-interference gate for alpha1`.

### Task 3: Race efforts as an LT2 source

**Files:** Modify `pace/thresholds.py`, `pace/zones.py` (store `efforts`), `pace/commands/zones.py` (`analyze --test race`, H1 points), `pace/commands/schema.py`, `tests/test_thresholds.py`, `tests/test_zones.py`, `tests/test_zones_cmd.py`, `docs/CONTRACT.md`, methods doc (new section 9b "Method R: race efforts", 14.1, 14.4).

**Interfaces and rules (user decision 2026-11-18):**
- `TESTS = ("drift", "time_trial", "ramp", "race")`; `pace zones analyze ID --test race [--save]`. The user or the coach marks a run as an all-out effort this way (Garmin rarely tags races).
- Duration = moving time of the run (laps ignored; the whole activity is the effort). Constants: `RACE_CS_S = (120, 1200)`, `RACE_TT_S = (1200, 3600)` (lower bound exclusive), `RACE_D_PRIME_M = (150, 300)`.
  - 120..1200 s: a critical-speed point. `--save` stores `{"activity", "date", "duration_s", "distance_m"}` in `zones.json` `efforts` (deduplicated by activity). The analysis returns: if the H1 window (estimate rules) holds at least one other maximal point of a different duration (another stored effort, or a Garmin PR point as today), the CS fit over the maximal points only (efforts + PRs, not training windows), with the H1 gates; otherwise the single-effort range CS = (distance - D') / duration for D' in RACE_D_PRIME_M (central value at the mean D'), confidence low. LT2 HR = H2 HR at the CS speed when H2 is available, flagged `extrapolated` when the CS speed is above H2's `v_max`.
  - 1200..3600 s: analysed with the F2 rules except: no 1680 s minimum, LT2 pace = whole effort, LT2 HR = mean HR from 600 s to the end, same stop/terrain rejections and pacing check.
  - Above 3600 s: `status: "check_only"` with average pace and HR, reason "longer than 60 min: below LT2, a check only"; `--save` is refused.
  - Below 120 s: refused (`bad_args`).
- `zones estimate`: H1 adds stored efforts inside the history window as extra points next to the PR points (same 120..1200 s rule).
- A saved race record is a `field_test` record with `method` `race_cs` or `race_tt`, so precedence and acceptance work as today (the user accepts it).

- [ ] **Step 1: Failing tests** for each duration class, the single-effort range on (1165 s, 5042 m) -> CS 4.07..4.20 m/s (D' 300..150 m, check the arithmetic in the test), the two-effort fit, the `extrapolated` flag, `check_only`, the refusal below 120 s, dedupe of efforts, and H1 using a stored effort.
- [ ] **Step 2: Run** -> fail. **Step 3: Implement.** **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Docs.** Methods doc section 9b with sources already in the doc (Hunter 2023, Smyth and Muniz-Pumares 2020, Anderson 2026 for the 2-20 min model domain and D' error; McGehee 2005 for the 25-60 min class) and the D' range marked as pace heuristic; CONTRACT `zones analyze` gains `race`.
- [ ] **Step 6: Commit** `feat: race efforts for LT2`.

### Task 4: Skill and contract wording

**Files:** Modify `.claude/skills/pace/SKILL.md`, `.claude/skills/pace/reference.md`, `.claude/skills/pace/coaching.md` if it covers zones, `tests/test_skill.py` if flags change, `README.md`.

- [ ] **Step 1:** SKILL zones section: pace offers methods and data; the coach lays out each method's result with its reliability (confidence, flags such as `alpha1_unreliable`, `extrapolated`, GPS-only terrain), says which it would trust and why, and the athlete decides what to record and accept. When the athlete mentions a race or an all-out effort, offer `pace zones analyze ID --test race --save`. Keep the existing LT1 advice (drift test F1 for this athlete) and add one line on why alpha1 fails for them (step interference, see methods doc 6).
- [ ] **Step 2:** Run `tests/test_skill.py` and the full suite -> green; ruff.
- [ ] **Step 3: Commit** `docs: skill lets the athlete decide zones; race efforts`.
