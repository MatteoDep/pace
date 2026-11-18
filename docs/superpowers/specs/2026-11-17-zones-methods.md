# Threshold and zone methods (research notes for coach plan 5)

Date: 2026-11-17.
Scope: how `pace` estimates LT1 and LT2 (HR and pace) for one runner, analyzes the three field tests, accepts lab values, and derives three zones and five Garmin HR zone floors.
This file is the reference for `pace/thresholds.py`, `pace/zones.py` and the "Thresholds and zones" section of the coaching skill.

## 0. Terms, athlete constraints and evidence grades

LT1 is the first threshold: the boundary between the moderate and heavy intensity domains (also called aerobic threshold, VT1, GET, T1).
LT2 is the second threshold: the boundary between the heavy and severe domains; in running the best supported marker is critical speed (CS), with MLSS, VT2/RCP and 4 mmol/L as related but not identical markers.
CS is the asymptote of the speed-duration relation; D' (metres) is the distance that can be covered above CS.
Athlete: Garmin Forerunner 55, chest strap on almost all runs, no Garmin lactate threshold detection, no running power.
Beat-to-beat RR intervals: checked on the user's account, the original FIT file of chest-strap runs contains `hrv` messages (field `time`, seconds) since about September 2026; a 3 h run held 30,232 beats, with artifacts such as 0.267 s and 3.891 s.
Data and where it comes from (one Garmin call per item unless stated):
- activity list rows (one read for the whole range): type, start, duration, `averageHR`, `maxHR`, `averageSpeed`, elevation gain, `eventType`, `aerobicTrainingEffect`, `averageRunningCadenceInStepsPerMinute`, `hrTimeInZone_*`;
- `get_activity` per run: HR sensors (`metadataDTO.sensors`), RPE and feel; these exist only here, not in the list rows nor in the details;
- `get_activity_details` per run: per-sample HR, speed, distance, elevation, cadence (section 1 for the resolution);
- `get_activity_splits` per run: laps (field tests only);
- Garmin personal records; daily resting HR from the sleep-stats range read.
RR intervals come only from the original FIT file: one extra Garmin API call per run (about 200 KB zipped), parsed with the `fitdecode` library; only derived data is kept (plan 5).
Max HR is known only from data.

Evidence grades used below:
- strong: consistent findings in systematic reviews or meta-analyses, or several independent validation studies.
- moderate: one or two peer-reviewed validation studies, or a review whose underlying studies are small or heterogeneous.
- weak/expert opinion: practitioner protocols, or peer-reviewed evidence that only indirectly supports the use made here.

Rules marked "pace heuristic" are engineering choices made for this tool; no source supports their exact numbers.

## 1. Common data rules (apply to every method)

Strap only: an activity is used only if its HR came from a chest strap (plan 5 sensor check on `get_activity`'s `metadataDTO.sensors`); wrist runs need `--include-wrist` and lower the confidence to low.
Outdoor only for speed: treadmill runs on the Forerunner 55 get speed from the wrist accelerometer, so they are excluded from every speed or pace calculation; a treadmill drift test is analyzed on HR only (section 8).
Sample resolution: `get_activity_details` returns at most `maxchart` samples (default 2000, which gave 4 s spacing on an 8348 s run), so `pace` requests `maxchart` = 2 x the run's elapsed seconds, capped at 20000, and reads the samples by their raw epoch timestamps (not the local-time table of `pace activity details`); plan 5 Task 0 checks that Garmin honours `maxchart`.
Resampling: convert samples to a 1 s grid by linear interpolation of cumulative distance and HR, but only inside gaps of at most 5 s.
Stops and auto-pause: a timestamp gap of more than 5 s at the fetched resolution, or a stretch of more than 10 s with speed below 1.0 m/s, is a stop; no analysis window may contain a stop.
This is the only stop rule: every method and field test uses it, with no other speed threshold.
Window clock: windows, segments and halves are measured in elapsed time on the sample timestamps; an auto-pause leaves a timestamp gap of more than 5 s, so it is a stop and no window spans it (controller ruling).
Speed from distance: window speed is (distance at window end minus distance at window start) / window seconds; never average the instantaneous GPS speed (pace heuristic, GPS speed samples are noisy).
Warm-up: the first 600 s of every run are excluded from the history methods; field tests use the lap the user pressed or, without it, the protocol warm-up time.
Grade: a window is "flat" if abs(elevation end minus elevation start) / window distance <= 0.02 and total ascent inside it <= 10 m per km (pace heuristic; Forerunner 55 elevation is GPS-based and noisy, so only coarse limits make sense).
Altitude smoothing: the Forerunner 55 altitude is GPS-derived; on the athlete's flat city runs it wanders 10-25 m per km and 1 s samples jitter by metres.
Observed 2026-11-18 on the athlete's runs: on raw altitude, 5-min windows of flat runs read 13-18 m/km of climb and 80-93% of them failed the 10 m/km limit; with 61 s smoothing 1-11% failed, while hilly runs still failed 35-100%.
Rule: before any grade or ascent check, altitude is replaced by its centered moving mean over 61 s (`ALT_SMOOTH_S`; the samples with |t - t_i| <= 30.5 s, missing samples skipped and kept missing, truncated at the ends of the series) (pace heuristic).
The mean is taken once over a whole series (each stop-free block of the 1 s grid, or a run's beats by beat time for section 6), never per window, so window edges do not truncate it; ascent then uses the 2 m hysteresis of 14.9 on the smoothed series.
Smoothing removes jitter, not slow wander: GPS wander steeper than the limits still fails, by design.
Bridges and underpasses (rises of 4-8 m) are below this noise and are not detected (user decision 2026-11-18: accepted).
Walking: windows with mean cadence below 140 steps/min are excluded (pace heuristic).
Cadence is always in steps/min: running FIT `cadence` is in strides/min, so steps/min = 2 x (cadence + fractional_cadence), with fractional_cadence as decoded (FIT scale 1/128 already applied); the unit of the Garmin details `directRunCadence` is pinned in plan 5 Task 0.
HR artefacts: HR samples outside 30..230 are dropped; the first 300 s of a run are ignored for max HR because dry-strap spikes are common there (pace heuristic).
Max HR (HRmax): a running maximum kept in `zones.json`: the highest 5-s rolling median HR (first 300 s excluded) among the strap runs `pace` has examined, updated by every `zones estimate` and every `zones analyze` (saved or not); it never decreases on its own, and a record entered with a higher `max_hr` (for example a lab test) raises it (controller ruling).
When HRmax is unknown (no strap run examined yet), zone 5 uses the fallback of section 14.5.
Using the highest HR recorded in training as HRmax follows Smyth et al. 2022, who found it higher and more plausible than the age-predicted value (187 +/- 8 vs 178 +/- 18 bpm).
Resting HR: median of `restingHeartRate` over the last 28 days from the sleep-stats range read (`pace wellness sleep-range`, plan 3); it is stored with the record and does not enter any formula here.

## 2. What the evidence says about thresholds, field markers and rules of thumb

Fixed percentages of maximum are poor anchors.
In 100 adults LT occurred anywhere between 60% and 90% of HRmax and MLSS between 75% and 97% of HRmax, so %HRmax zones put different people in different domains (Iannetta et al. 2020).
Jamnick et al. 2020 reach the same conclusion and find support for LT1/GET/VT to mark the moderate-heavy boundary, and for CP/CS (not most other methods) to mark the heavy-severe boundary.
CS sits above the lactate markers.
CS is the best supported maximal metabolic steady state; MLSS falls at a lower speed and work at CP can be sustained for only about 20-30 min (Jones et al. 2019).
In a meta-analysis MLSS underestimated CP by about 11%, VT1 by about 30%, and RCP and VT2 overestimated it by about 6% and 21% (Galan-Rioja et al. 2020).
In 31 trained to world-class runners CS was 0.36 to 0.47 km/h faster than the lactate thresholds (OBLA, modified D-max), with wide limits of agreement (about -1.4 to +2.0 km/h for OBLA), so the methods are not interchangeable (Rainsberger et al. 2026).
LT1 relative to CS: across 26 studies (527 participants) T1 occurred at 82.3% of CS (95% CI 81.1-83.6), slightly lower in slower runners (80.6% at a mean CS of 11.5 km/h) (Hunter et al. 2024).
The same fraction (82.3% CS) is used to split zone 1 from zone 2 in a 151,813-marathon training analysis (Muniz-Pumares et al. 2025).
Consequence for `pace`: a lab LT2 at 4 mmol/L or D-max will usually be slower than a CS-based LT2 from history; records keep their `method` so the coach does not compare them as if they were the same marker.
Grade of these statements: strong for "fixed %HRmax is unreliable" and "CS marks heavy-severe"; moderate for the 82.3% fraction (review of heterogeneous methods, CI is for the mean, not for an individual).

Sources:
- Iannetta D, Inglis EC, Mattu AT, Fontana FY, Pogliaghi S, Keir DA, Murias JM. 2020. A Critical Evaluation of Current Methods for Exercise Prescription in Women and Men. Med Sci Sports Exerc 52(2):466-473. https://doi.org/10.1249/MSS.0000000000002147 (abstract)
- Jamnick NA, Pettitt RW, Granata C, Pyne DB, Bishop DJ. 2020. An Examination and Critique of Current Methods to Determine Exercise Intensity. Sports Med 50(10):1729-1756. https://doi.org/10.1007/s40279-020-01322-8 (abstract)
- Jones AM, Burnley M, Black MI, Poole DC, Vanhatalo A. 2019. The maximal metabolic steady state: redefining the 'gold standard'. Physiol Rep 7(10):e14098. https://doi.org/10.14814/phy2.14098 (abstract)
- Galan-Rioja MA, Gonzalez-Mohino F, Poole DC, Gonzalez-Rave JM. 2020. Relative Proximity of Critical Power and Metabolic/Ventilatory Thresholds: Systematic Review and Meta-Analysis. Sports Med 50(10):1771-1783. https://doi.org/10.1007/s40279-020-01314-8 (abstract)
- Rainsberger KL, Subudhi AW, Pearson J, Baranauskas MN. 2026. Agreement between lactate threshold and critical speed in trained, highly trained, and World Class runners. J Sci Med Sport 29(11):1286-1294. https://doi.org/10.1016/j.jsams.2026.04.020 (abstract)
- Hunter B, Meyler S, Maunder E, Cox TH, Muniz-Pumares D. 2024. The Relationship Between the Moderate-Heavy Boundary and Critical Speed in Running. Int J Sports Physiol Perform 19(9):963-972. https://doi.org/10.1123/ijspp.2024-0101 (abstract)
- Muniz-Pumares D, Hunter B, Meyler S, Maunder E, Smyth B. 2025. The Training Intensity Distribution of Marathon Runners Across Performance Levels. Sports Med 55(4):1023-1035. https://doi.org/10.1007/s40279-024-02137-7 (abstract)
- Smyth B, Maunder E, Meyler S, Hunter B, Muniz-Pumares D. 2022. Decoupling of Internal and External Workload During a Marathon: An Analysis of Durability in 82,303 Recreational Runners. Sports Med 52(9):2283-2295. https://doi.org/10.1007/s40279-022-01680-5 (full text)

## 3. Method H1: critical speed from best efforts in training history (LT2 pace)

Protocol: no test; `pace` scans the examined runs (section 14.7) for the fastest flat, stop-free rolling windows of fixed duration.
Durations: 180 s, 420 s and 720 s, as in Hunter et al. 2023 (best 3-, 7- and 12-min segments of habitual training).
Formula (linear distance-time model):
```
for T in (180, 420, 720):
    d_T = max over all valid windows of length T s in the examined runs of
          (cum_distance[t+T] - cum_distance[t])          # metres
fit d = CS * T + D' by ordinary least squares on the 3 points
CS  in m/s (slope), D' in m (intercept)
LT2 pace (s/km) = 1000 / CS
```
Optional extra points: Garmin personal records (1 km, 1 mile, 5 km) whose record date is inside the analysis window and whose duration is between 120 s and 1200 s may be added as (duration, distance) points (pace heuristic; Anderson et al. 2026 advise excluding race efforts of 800 m or less and 10 km or more).
Race efforts stored by section 9b inside the window are added the same way; a record set in a stored effort's activity is dropped (user decision 2026-11-18).
The run of a stored effort gives no training windows: it counts through its effort point only (controller ruling).
Inputs: per-sample distance, time, elevation, cadence; Garmin PR list (one call); run dates.
Window: the last 8 weeks (`--weeks 8`); Hunter et al. used 6 weeks, Smyth and Muniz-Pumares 16 weeks.
Validity gates (pace heuristic):
- reject the fit if D' is outside 50..500 m or CS is not below the 720 s window speed;
- confidence medium if the three windows come from at least 2 different runs and the 180 s speed is at least 1.08 x the 720 s speed (a sign that the short effort was near maximal); otherwise low;
- never high: training data cannot prove the efforts were maximal (Smyth and Muniz-Pumares 2020 name this as their main limitation).
Known error and validity:
- In 23 recreational runners, CS from habitual training (3.44 +/- 0.63 m/s) did not differ from CS from three time trials (3.42 +/- 0.53 m/s), r = 0.669; D' agreed poorly (Hunter et al. 2023).
- In more than 25,000 runners, CS from the fastest 400 m to 5 km in 16 weeks of training predicted marathon time with about 7.7% error; the distance-time fit was very linear (R2 0.9999) (Smyth and Muniz-Pumares 2020).
- Model choice matters little for CS: the linear distance-time, inverse-time and 2-parameter hyperbolic models give comparable CS, typical standard error 1-2.8%; D' error 5-28% (Anderson et al. 2026).
- CS overestimates lactate-based LT2 (section 2).
Evidence grade: moderate (two peer-reviewed studies on training data; the efforts are not verified maximal).

Sources:
- Hunter B, Ledger A, Muniz-Pumares D. 2023. Remote Determination of Critical Speed and Critical Power in Recreational Runners. Int J Sports Physiol Perform 18(12):1449-1456. https://doi.org/10.1123/ijspp.2023-0276 (abstract)
- Smyth B, Muniz-Pumares D. 2020. Calculation of Critical Speed from Raw Training Data in Recreational Marathon Runners. Med Sci Sports Exerc 52(12):2637-2645. https://doi.org/10.1249/MSS.0000000000002412 (full text)
- Anderson M, Sabapathy S, Arnold B, Bellenger C, Kolodziej F, Bellinger P. 2026. The Measurement and Application of Critical Speed and D' in Running: A Scoping Review. Sports Med 56(7):1637-1674. https://doi.org/10.1007/s40279-026-02410-x (full text)
- Lipkova L, Struhar I, Krajnak J, Puda D, Kumstat M. 2025. Field-based tests for determining critical speed among runners and its practical application: a systematic review. Front Sports Act Living 7:1520914. https://doi.org/10.3389/fspor.2025.1520914 (full text)

## 4. Method H2: HR-speed regression from steady segments (HR at LT1 and LT2)

Protocol: no test; steady segments from the examined runs give the runner's submaximal HR-speed line, which converts threshold speeds into threshold HRs.
Formula:
```
segments: non-overlapping 360 s windows, each starting at t >= 600 s and ending at t <= 3600 s
          (excludes warm-up and most cardiac drift), flat, stop-free, cadence >= 140
steady:   speed of each 60 s sub-window within +/-5% of the window speed
v_i  = window speed (m/s), from distance
hr_i = mean HR over the last 240 s of the window (first 120 s absorb HR kinetics)
fit hr = a + b * v by ordinary least squares
require: n >= 8 segments, from >= 3 runs, max(v) - min(v) >= 0.5 m/s, b > 0
HR at speed v: hr(v) = a + b * v, rounded to integer (floor(x + 0.5))
LT2 HR = hr(CS)      only if max(v_i) >= 0.90 * CS   (limit extrapolation)
LT1 HR = hr(LT1 speed)
speed at HR h: v(h) = (h - a) / b
SEE = sqrt(sum((hr_i - a - b*v_i)^2) / (n - 2))   reported with the estimate
```
Inputs: per-sample HR, distance, time, elevation, cadence.
Known error and validity:
- HR rises linearly with running speed in the submaximal range, and an index built from exercise HR and speed in everyday continuous runs tracked changes in peak speed (r 0.43-0.61) and speed at the respiratory compensation threshold (r 0.35-0.39) over 28 weeks (Vesterinen et al. 2014).
- Day-to-day noise: a 3-4 bpm rise at the same speed was treated as normal variation in the HR-running speed index (Nuuttila et al. 2022).
- HR keeps drifting during constant runs even in the moderate domain (section 8), which is why segments after 60 min are excluded.
- Heat, dehydration and fatigue raise HR at a given speed (Hunter et al. 2025 list environment and nutrition as factors to control); the coach should prefer runs in normal conditions.
Evidence grade: moderate for the HR-speed relation as a monitoring tool; weak/expert opinion for reading threshold HR off it (no study validates LT HR from this regression).

Sources:
- Vesterinen V, Hokka L, Hynynen E, Mikkola J, Hakkinen K, Nummela A. 2014. Heart rate-running speed index may be an efficient method of monitoring endurance training adaptation. J Strength Cond Res 28(4):902-908. https://doi.org/10.1519/JSC.0000000000000349 (abstract)
- Nuuttila OP, Nummela A, Korhonen E, Hakkinen K, Kyrolainen H. 2022. Individualized Endurance Training Based on Recovery and Training Status in Recreational Runners. Med Sci Sports Exerc 54(10):1690-1701. https://doi.org/10.1249/MSS.0000000000002968 (full text)
- Teso M, Colosio AL, Loi M, Boone J, Pogliaghi S. 2025. Predicting Heart Rate Slow Component Dynamics: A Model Across Exercise Intensities, Age, and Sex. Sports 13(2):45. https://doi.org/10.3390/sports13020045 (abstract)
- Hunter B, Maunder E, Jones AM, Gallo G, Muniz-Pumares D. 2025. Durability as an index of endurance exercise performance: Methodological considerations. Exp Physiol 110(11):1612-1624. https://doi.org/10.1113/EP092120 (full text)

## 5. Method H3: LT1 speed as a fraction of CS (LT1 from history)

Protocol: no test; LT1 speed is taken as a fixed fraction of CS from H1, then converted to HR with H2.
Formula:
```
LT1 speed (m/s) = 0.823 * CS
LT1 pace (s/km) = 1000 / LT1 speed
LT1 HR          = hr(LT1 speed) from H2
```
Inputs: CS from H1, regression from H2.
Known error and validity: 82.3% (95% CI 81.1-83.6) is a pooled mean of 26 studies with mixed T1 methods; slower runners sat lower (80.6%), faster ones higher (84.2%); the authors call it a basis for remote analysis and recommend testing for precise use (Hunter et al. 2024).
Individual scatter around the mean is not reported in the abstract, so the LT1 record from history is always confidence low until a drift test confirms it.
Evidence grade: moderate for the group mean, weak for an individual.

Sources:
- Hunter B, Meyler S, Maunder E, Cox TH, Muniz-Pumares D. 2024. (as in section 2) (abstract)
- Muniz-Pumares D, Hunter B, Meyler S, Maunder E, Smyth B. 2025. (as in section 2) (abstract)

## 6. Method H4: DFA alpha1 from RR intervals (LT1 HR and pace)

Protocol: no test; `pace` downloads the original FIT file of selected strap runs, computes DFA alpha1 in rolling 2-min windows, and reads LT1 as the HR and speed at which alpha1 crosses 0.75.
Background: alpha1 is the short-term scaling exponent of detrended fluctuation analysis (DFA) of the RR series (Peng et al. 1995).
During exercise it falls from about 1.0 (correlated) at low intensity, passes 0.75 at moderate loads and drops to 0.5 (uncorrelated) and below at high intensity (Rogers and Gronwald 2022).
Rogers et al. 2020 defined the HRV threshold (HRVT) as alpha1 = 0.75 on an incremental treadmill run: 2-min windows recomputed every 5 s, box sizes 4..16 beats, smoothness priors detrending with lambda 500, Kubios "automatic" artifact correction, and a linear regression over the near-linear decline from about 1.0 to 0.5 to find the HR at 0.75.
Hoos and Gronwald 2025 list the same settings as the standard to report (lambda 500, 4 <= n <= 16, 2-min window, 5-s grid).
`pace` keeps those settings; what differs is the input (training runs, not a ramp test) and the artifact correction (its own rule, below, not Kubios).

RR extraction (pace heuristic; the FIT `hrv` message has no timestamp of its own, so the alignment must be checked on a real file during implementation):
```
read all messages in file order with fitdecode
hrv message: field `time` = up to 5 RR values in s; drop padding (None, or >= 65.5 s = FIT invalid 0xFFFF / 1000)
beat times: tb_0 = timestamp of the last `record` message before the first `hrv` message
            tb_k = tb_(k-1) + rr_k          (raw RR, artifacts included: their sum follows real time)
resync:     when an `hrv` message is reached (before its beats are added), if abs(tb_current - t of the
            preceding `record`) > 3 s, set tb_current = that record's t and mark a gap
            (verified 2026-11-18 on the athlete's files: 44 resyncs in a 3 h run, 95% of offsets within 2.6 s;
            no `hrv` messages are logged during an auto-pause, so one resync follows each pause)
gap:        also any raw rr_k > 2.0 s (signal dropout, for example the 3.891 s value); no window may contain a gap
sanity:     reject the run's RR data if the sum of RR differs by more than 2% from the record time span it covers
            (the span excludes pause gaps, i.e. record intervals > 5 s, since no beats are logged during a pause),
            or if 60 / mean(RR) differs from the mean `record` HR over the same span by more than 5 bpm
```
Artifact correction (pace heuristic, modelled on the Kubios "automatic" behaviour described by Rogers et al. 2021a: a missed beat is replaced by a beat inserted halfway):
```
m_k = median of rr_(k-10) .. rr_(k+10)   (21 beats, as Kanniainen et al. 2025; truncated at the ends)
rr_k is valid if abs(rr_k - m_k) <= 0.20 * m_k
otherwise rr_k is flagged and corrected, first rule that applies:
  missed beat(s): j = round(rr_k / m_k) >= 2 and abs(rr_k - j * m_k) <= 0.20 * m_k  -> j values rr_k / j
  extra beat:     rr_k < m_k and abs(rr_k + rr_(k+1) - m_k) <= 0.20 * m_k            -> one value rr_k + rr_(k+1)
                  (both raw beats count as flagged)
  other:          replace rr_k by m_k   (for example 0.267 s among values near 0.400 s)
artifact share of a window = flagged raw beats / raw beats in the window
```
Kanniainen et al. 2025 used a tighter rule (discard RR more than 5% from a 21-beat median or more than 200 ms from the previous RR, and drop recordings with 5% or more removed).
`pace` uses 20% because its rule targets missed and extra beats, which Rogers et al. 2021a name as the most common artifact during dynamic exercise; a 5% band would probably also flag normal beat-to-beat variation at low intensity (reasoning, not tested).

Windows and alpha1:
```
window: 120 s of beat time, step 5 s; each value is time-stamped at the window centre
        (Kubios time-varying output is shifted by half a window; Hoos and Gronwald 2025)
skip the window if: it contains a gap or a stop (section 1); artifact share > 5%; fewer than 200 corrected beats
x = corrected RR in the window (s), N values
detrend (smoothness priors, Tarvainen et al. 2002):
        z = x - (I + 500^2 * D2' D2)^-1 x     D2 = (N-2) x N second-difference matrix (rows 1, -2, 1)
        computed by solving (I + 500^2 * D2' D2) s = x with a banded (pentadiagonal) LDL' solver, O(N);
        never a dense inverse (O(N^3)); pure Python, no numpy
DFA (Peng et al. 1995):
        y_i = cumulative sum of (z_j - mean(z)) for j <= i
        for n in 4..16: split y into floor(N / n) non-overlapping boxes from the start (the tail is dropped);
                        fit a least-squares line in each box;
                        F(n) = sqrt(mean of the squared residuals over all boxed points)
        alpha1 = OLS slope of log10 F(n) on log10 n over the 13 box sizes; null if any F(n) = 0
window HR    = 60 / mean(x)
window speed = (distance at window end - distance at window start) / 120   (section 1)
```
Measured cost of this pure-Python implementation: about 1.7 ms per 330-beat window, so about 1.3 s per run with windows limited to 600-4500 s (780 windows at a 5 s step).
Qualifying windows in a training run:
- the whole window lies between 600 s and 4500 s of the run (warm-up excluded; fatigue lowers alpha1 at a given load later in long sessions: from 0.71 to 0.32 at 3 m/s after a 6 h run in Rogers et al. 2021c, and a bias of -12.3 bpm on a second ramp in Van Hooren et al. 2025);
- outdoor, flat, stop-free, mean cadence >= 140 (section 1);
- no cardio-locomotor coupling risk: the window is dropped when abs(HR - c) / c <= 0.05 or abs(HR - c / 2) / (c / 2) <= 0.05, with c the window's mean cadence in steps/min (pace heuristic, see "Observed on this athlete" below); the number of windows dropped by this rule is reported;
- no step interference: after the coupling check, the window is dropped when its step share (below) is above 0.25 (pace heuristic, see "Step interference" below); the count is reported as `dropped_step`, counted like the coupling drops (only windows that passed every earlier check);
- speed of each 30 s sub-window within +/-10% of the window speed (pace heuristic);
- the 120 s before the window have a mean speed within +/-10% of the window speed, and the 180 s before the window contain no 30 s stretch faster than 1.10 x the window speed (pace heuristic: alpha1 lags changes in load, and Andriolo et al. 2024 found raw load-alpha1 pairs from everyday workouts poorly correlated for this reason).

Per-run gate (pace heuristic, see "Observed on this athlete" below):
```
Q   = all qualifying windows of the run (any alpha1)
rho = Spearman rank correlation of window HR and alpha1 over Q (average ranks for ties)
usable only if: rho <= -0.6 and >= 3 windows of Q with alpha1 > 0.75 and >= 3 with alpha1 < 0.75
otherwise:      status "alpha1_unusable" with the reasons ("not monotonic, rho = x" and/or "one-sided";
                "not monotonic" takes precedence as the outcome), never an estimate
```
Per-run crossing (alpha1-vs-HR and alpha1-vs-speed regressions), only on usable runs:
```
P = qualifying windows with 0.5 <= alpha1 <= 1.0   (the near-linear part; Rogers et al. 2020)
need: >= 60 points (5 min of window centres) with alpha1 > 0.75 and >= 60 points with alpha1 < 0.75;
      max HR - min HR over P >= 10 bpm
fit HR = c + d * alpha1 by OLS on P; need d < 0 and Pearson r <= -0.5
fit v  = e + f * alpha1 by OLS on P; need f < 0 and r <= -0.5, otherwise the run gives HR only
run LT1 HR    = c + d * 0.75, only if inside [min HR, max HR] of P (no extrapolation)
run LT1 speed = e + f * 0.75
```
Combining runs:
```
runs: strap runs of the last 12 weeks with a valid crossing
H4 LT1 HR   = R(median of run LT1 HR)                (R as in section 14.5)
H4 LT1 pace = R(1000 / median of run LT1 speed)
spread      = max - min of run LT1 HR
confidence  = medium if >= 2 runs, spread <= 6 bpm, and median artifact share of the used windows <= 3%;
              otherwise low; never high
```
The 5-min, 10 bpm, r and spread limits are pace heuristics; the 3% and 5% artifact limits follow the sources below.
The 200-beat minimum follows the alphaHRV Garmin data field, which computes alpha1 from the prior 200 beats (Rogers and Gronwald 2022).
Which runs qualify: runs that pass through the threshold at steady, flat speeds, for example progression runs that start easy and finish at tempo, an easy block followed by a steady tempo block, or interval sessions with a gradual warm-up and reps of at least about 5 min.
Steady easy runs (all alpha1 > 0.75) or steady tempo runs (all < 0.75) give only one side, so they produce no H4 value; `pace` reports them as notes (for example "alpha1 above 0.75 up to 138 bpm").
Short reps (under about 4 min) give no qualifying windows because of the lag rule.
LT2 at alpha1 0.5: computed only as a note, never stored as LT2.
Reasons: individual limits of agreement with VT2 were -24 to +16 bpm (Rogers et al. 2021b); during 30 min of constant running at MLSS alpha1 stayed above 0.5 (0.64 after about 18 min; van Rassel et al. 2025); 6 of 21 runners could not finish 20 min at the alpha1 0.5 speed (Gronwald et al. 2024); chest-strap alpha1 disagrees most with ECG at high intensity and low alpha1 (Schaffarczyk et al. 2022); artifact correction pushes low alpha1 values upward (Rogers et al. 2021a).
LT2 stays with H1 + H2 and F2.
Per-athlete memory (controller ruling on the details): `zones.json` keeps an H4 status with a streak of consecutive runs or F3 ramps that failed the gate as "not monotonic", and the ids of the runs and ramps already counted.
Runs and ramps are counted in start-time order, each at most once: re-evaluating a counted run (for example from cache in a later estimate) never changes the streak again.
A usable run or ramp resets the streak to 0; "one-sided" runs, such as steady easy runs, do not count either way; when both reasons apply, "not monotonic" takes precedence (the run counts).
`zones analyze --test ramp` always records its gate outcome here, with or without `--save`.
At a streak of 2, H4 is marked "unreliable for this athlete": `zones estimate` skips H4 and makes no FIT downloads, reports H4 as status "skipped" with that reason, and the coach suggests F1 instead of F3, until the user asks to retry (`zones estimate --retry-h4`, which clears the mark and the streak).

Observed on this athlete (one ramp test on 2026-09-09: 13 steps of 3 min, chest strap, RR artifacts 0.1%, alpha1 of the last 120 s of each step after smoothness-priors detrending; running cadence 166-188 steps/min):
```
step HR  126   133   136   139   142   148   151   156   159   164   170   176   180
alpha1   0.54  0.65  0.45  0.35  0.37  0.35  0.46  0.61  0.63  1.03  1.13  0.99  0.35
```
alpha1 did not fall with intensity: it was already below 0.75 at the easiest steps, and rose above 1.0 at 164-176 bpm, where HR approached the step rate.
A likely explanation is cardio-locomotor coupling (the heart rate locking to the step rate, which imposes a periodic structure on the RR series); this is uncited reasoning, no study on coupling and DFA alpha1 in running was opened for this note, so the grade of the coupling claim is weak.
Step interference (observed on the same ramp, its warm-up and its cool-down, Polar H10 chest strap, 2026-09-09; controller analysis 2026-11-18):
- The dominant RR oscillation sat within 0.05 Hz of abs(step rate - HR) in 98% of the ramp's 2-min windows (57% of the warm-up windows, 85% of the cool-down windows).
- Its period in beats, HR / abs(step rate - HR), ran from about 3 to over 20 beats, and alpha1 followed it: about 0.2-0.5 at 3-4 beats, above 1.0 past 16 beats.
  A likely reading (reasoning, not tested): a periodic component of 3-4 beats looks anti-correlated at the DFA box sizes 4..16 (alpha1 low), one of 16 or more beats looks like a trend inside the boxes (alpha1 high).
- Breathing locked at 2:2 or 3:3 (step rate / 4 or / 6) did not match the oscillation.
- Windows whose step share was 0.25 or lower read alpha1 0.83-1.17 at easy effort (about 125-130 bpm), close to the value of about 1.0 expected below LT1.
- The recording used a Polar H10 chest strap (compared with ECG during incremental exercise by Schaffarczyk et al. 2022), which makes a measurement artifact unlikely; the oscillation is most likely a real beat-to-beat modulation at the step rate, seen through the beat sampling.
Rule (pace heuristic; no published validation of such a gate was found):
```
f_s = step rate / 60, f_h = HR / 60, k = round(f_s / f_h)
step frequency f0 = abs(f_s - k * f_h)    (the step component as seen in a beat-sampled series: k = 1 gives
                                           abs(step rate - HR), k = 2 covers HR near half the step rate)
tachogram: the window's corrected RR, linearly interpolated at 4 Hz
spectrum:  64 s Hann segments (as many as needed to cover the window, evenly spaced starts from 0 to n - seg),
           each linearly detrended; power summed over the segments
step share = power within 0.05 Hz of f0 / power in 0.04 Hz .. min(1.9 Hz, HR / 120)   (HR / 120 = the beat-rate
           Nyquist; the step band is clipped to that range)
null when f0 - 0.05 < 0.04 Hz (HR that close to the step rate or its half is already a coupling drop),
           the window is shorter than one segment, or the RR values are constant
drop the window if step share > 0.25
```
The 0.25 limit, the 0.05 Hz band and the spectral settings are pace heuristics chosen on this athlete's ramp; for scale, white RR noise gave a share of about 0.14 at 0.65 Hz in pace's unit test (section 14.2).
Measured cost: about 0.6 ms per window in pure Python (DFT over the needed bins only), less than one alpha1.
Consequence for this athlete: H5 (history) and F1 (field test) are the primary LT1 methods; H4 and F3 contribute only when their gates pass, and the coupling exclusion and the monotonicity gate exist so that a pattern like this one produces "alpha1_unusable" rather than a wrong LT1.
Inputs: data derived from the FIT file (RR intervals; per-second `record` HR, speed, distance, cadence, altitude); the per-sample data already fetched for the run.
Known error and validity:
- Recreational male runners, incremental treadmill, ECG: HR at alpha1 0.75 vs gas-exchange VT1 152 +/- 21 vs 154 +/- 20 bpm, bias -1.9 bpm, limits of agreement -12 to +8 bpm, n = 15 (Rogers et al. 2020).
- Runners with a consumer chest belt: bias -0.9 bpm, limits of agreement -12.2 to +10.5 bpm on a first ramp; on a second ramp after fatigue the bias was -12.3 bpm (limits -30.4 to +5.9), n = 14 (Van Hooren et al. 2025).
- Systematic review (27 studies, 461 participants, mixed HRV indices): HR bias versus VT1 1 bpm (limits -10.9 to 12.8 bpm), versus LT1 2.7 bpm (limits -20.4 to 25.8 bpm) (Kaufmann et al. 2023).
- 58 runners, a dynamic DFA variant against LT1: mean -2 bpm, limits -27 to +23 bpm (Kanniainen et al. 2025).
- Negative findings: in 56 cyclists, limits of agreement for HR at VT1 were -39.2 to +26.9 bpm (Cassirame et al. 2025); in 17 triathletes alpha1 at VT1 in running was 0.65 +/- 0.20 and the authors found group-level but not individual-level validity (Nieto-Jimenez et al. 2026); in 19 recreational runners, HR at HRV thresholds found by Dmax (one of them from alpha1) often underestimated lactate threshold HR indoors but mostly not outdoors (Lu et al. 2026).
- Artifacts: 1-3% missed beats biased raw alpha1 slightly, at 6% with medium correction proportional bias reached 19%, yet HRVT moved by at most about 1 bpm on average; a Polar H7 gave HRVT 4 bpm lower than ECG (Rogers et al. 2021a); minimal bias below 3% (Rogers and Gronwald 2022).
- Reliability: between-day ICC 0.85 for alpha1 during low-intensity running, smallest worthwhile change 5.5% (Van Hooren et al. 2023).
- Steady state: 20 min at the alpha1 0.75 speed gave alpha1 0.86 +/- 0.23 with small changes (Gronwald et al. 2024), so moderate steady segments from training runs are usable; heavier ones keep drifting down with time (van Rassel et al. 2025).
- Everyday data: only one study used unstructured workouts (cycling power, Garmin or Suunto recordings, unknown straps, at most 5% artifacts, 2-min windows every 5 s); it found load-alpha1 relations but did not validate a threshold (Andriolo et al. 2024).
- No opened study validates the Garmin strap used here, nor a threshold read from training runs instead of a ramp.
- Proponents now recommend alpha1 for consumer zone setting (Rogers et al. 2026); critics question fixed values (Cassirame et al. 2025; reply by Hoos and Gronwald 2025, who also point out that the choice of regression start and end points biases the result).
Evidence grade: moderate for group-level agreement of alpha1 0.75 with VT1 on incremental tests; weak for an individual (limits of agreement about +/-11 bpm at best) and for this implementation (training runs, own artifact rule, unvalidated strap).

Sources:
- Peng CK, Havlin S, Stanley HE, Goldberger AL. 1995. Quantification of scaling exponents and crossover phenomena in nonstationary heartbeat time series. Chaos 5(1):82-87. https://doi.org/10.1063/1.166141 (abstract)
- Tarvainen MP, Ranta-Aho PO, Karjalainen PA. 2002. An advanced detrending method with application to HRV analysis. IEEE Trans Biomed Eng 49(2):172-175. https://doi.org/10.1109/10.979357 (abstract)
- Rogers B, Giles D, Draper N, Hoos O, Gronwald T. 2020. A New Detection Method Defining the Aerobic Threshold for Endurance Exercise and Training Prescription Based on Fractal Correlation Properties of Heart Rate Variability. Front Physiol 11:596567. https://doi.org/10.3389/fphys.2020.596567 (full text)
- Rogers B, Giles D, Draper N, Mourot L, Gronwald T. 2021a. Influence of Artefact Correction and Recording Device Type on the Practical Application of a Non-Linear Heart Rate Variability Biomarker for Aerobic Threshold Determination. Sensors 21(3):821. https://doi.org/10.3390/s21030821 (full text)
- Rogers B, Giles D, Draper N, Mourot L, Gronwald T. 2021b. Detection of the Anaerobic Threshold in Endurance Sports: Validation of a New Method Using Correlation Properties of Heart Rate Variability. J Funct Morphol Kinesiol 6(2):38. https://doi.org/10.3390/jfmk6020038 (full text)
- Rogers B, Mourot L, Doucende G, Gronwald T. 2021c. Fractal correlation properties of heart rate variability as a biomarker of endurance exercise fatigue in ultramarathon runners. Physiol Rep 9(14):e14956. https://doi.org/10.14814/phy2.14956 (abstract)
- Rogers B, Gronwald T. 2022. Fractal Correlation Properties of Heart Rate Variability as a Biomarker for Intensity Distribution and Training Prescription in Endurance Exercise: An Update. Front Physiol 13:879071. https://doi.org/10.3389/fphys.2022.879071 (full text)
- Rogers B, Fleitas-Paniagua PR, Murias JM. 2026. Heart Rate Variability DFA a1: From Lab to Consumer Exercise Prescription and Monitoring. Exerc Sport Sci Rev (ahead of print). https://doi.org/10.1249/JES.0000000000000401 (abstract)
- Van Hooren B, Mennen B, Gronwald T, Bongers BC, Rogers B. 2025. Correlation properties of heart rate variability to assess the first ventilatory threshold and fatigue in runners. J Sports Sci 43(2):125-134. https://doi.org/10.1080/02640414.2023.2277034 (abstract)
- Van Hooren B, Bongers BC, Rogers B, Gronwald T. 2023. The Between-Day Reliability of Correlation Properties of Heart Rate Variability During Running. Appl Psychophysiol Biofeedback 48(4):453-460. https://doi.org/10.1007/s10484-023-09599-x (abstract)
- Kaufmann S, Gronwald T, Herold F, Hoos O. 2023. Heart Rate Variability-Derived Thresholds for Exercise Intensity Prescription in Endurance Sports: A Systematic Review of Interrelations and Agreement with Different Ventilatory and Blood Lactate Thresholds. Sports Med Open 9(1):59. https://doi.org/10.1186/s40798-023-00607-2 (abstract)
- Kanniainen M, Laatikainen-Raussi V, Pukkila T, Vohlakari K, Hynynen E, Ihalainen JK, Rasanen E. 2025. Threshold estimation in running using dynamical correlations of RR intervals. Physiol Rep 13(9):e70241. https://doi.org/10.14814/phy2.70241 (full text)
- Cassirame J, Eustache E, Garbellotto L, Chevrolat S, Gimenez P, Lepretre PM. 2025. Detrended fluctuation analysis to determine physiologic thresholds, investigation and evidence from incremental cycling test. Eur J Appl Physiol 125(2):523-533. https://doi.org/10.1007/s00421-024-05614-z (abstract)
- Hoos O, Gronwald T. 2025. Detrended fluctuation analysis of heart rate variability during exercise: Time to reconsider the theoretical and methodological background (comment on Cassirame et al. 2025). Eur J Appl Physiol 125(9):2637-2639. https://doi.org/10.1007/s00421-025-05859-2 (full text)
- Nieto-Jimenez C, Naranjo-Orellana J, Medina-Porcile CL, Gawn J, Tuesta M, Van Duijn T, Cotter JD. 2026. Ventilatory thresholds are detectable using DFA-alpha1 at group level but not individual level during swimming, cycling and running. Physiol Int (ahead of print). https://doi.org/10.1556/2060.2026.00932 (abstract)
- Lu C, Lu S, Wu Y, Xing Q, Pan B, Shen Y. 2026. Estimation of Runners' Lactate Threshold Heart Rate and Speed by Heart Rate Variability. Int J Sports Med 47(4):304-314. https://doi.org/10.1055/a-2721-9979 (abstract)
- van Rassel CR, Ajayi OO, Sales KM, Clermont CA, Rummel M, MacInnis MJ. 2025. Quantifying exercise intensity with fractal correlation properties of heart rate variability: a study on incremental and constant-speed running. Eur J Appl Physiol 125(1):91-102. https://doi.org/10.1007/s00421-024-05592-2 (abstract)
- Schaffarczyk M, Rogers B, Reer R, Gronwald T. 2022. Validity of the Polar H10 Sensor for Heart Rate Variability Analysis during Resting State and Incremental Exercise in Recreational Men and Women. Sensors 22(17):6536. https://doi.org/10.3390/s22176536 (abstract)
- Andriolo S, Rummel M, Gronwald T. 2024. Relationship of Cycling Power and Non-Linear Heart Rate Variability from Everyday Workout Data: Potential for Intensity Zone Estimation and Monitoring. Sensors 24(14):4468. https://doi.org/10.3390/s24144468 (full text)
- Gronwald T et al. 2024. (as in section 8) (full text)

Gate refinements (controller rulings during implementation, 2026-11-18):
- `not_monotonic` (which counts toward the H4 streak) needs at least 6 qualifying points spanning at least 10 bpm and at least max(2, ceil(10% of the points)) on each side of 0.75; otherwise the outcome is `one_sided` or `insufficient_data`, and neither touches the streak (their activity ids are still recorded as counted).
  A steady easy run whose windows all sit above 0.75 therefore never marks H4 unreliable.
- The same gate applies to F3 ramps; a ramp with fewer than 6 kept steps is `insufficient_data`.
- When H4 and H5 agree within 5 bpm, their mutual agreement counts as "another method agrees" for medium confidence (H3 agreement not required).

## 7. Method H5: LT1 bounds from HR drift in normal runs

Protocol: no test; `pace` looks in the strap runs of the last 12 weeks for long, steady, flat segments, computes decoupling as in F1 (section 8), and treats each run as evidence that LT1 HR lies above (low drift) or below (high drift) the HR of that run.
Logic: F1 reads drift <= 5% at constant pace as "at or below LT1" and > 5% as "above LT1"; a normal run that happens to meet F1's conditions carries the same information, but only as a bound, because the runner did not aim at LT1.
Segment and drift:
```
candidate run: strap, outdoor (not treadmill, not trail), in the last 12 weeks;
               not starting within 24 h after the start of a race (list-row `eventType` race)
               or of a run with list-row `aerobicTrainingEffect` >= 4.0 (pace heuristic; list rows only, no extra call)
for each start s = 900, 960, 1020, ... s (15 min warm-up, 60 s steps):
    extend the segment in 60 s steps while all hold:
      stop-free (section 1); mean cadence >= 140 in each 60 s sub-window;
      speed of each 60 s sub-window within +/-5% of the segment mean speed;
      the grade rule of section 1 holds on each 300 s sub-window; length <= 4500 s
keep the longest segment over all s (tie: earliest); need length >= 2700 s
recheck the +/-5% speed rule against the final segment mean speed
reject if: total ascent of the segment > 10 m per km;
           mean temperature >= 24 C, only when the run's cached FIT data carries the `record` field
           temperature (pace heuristic; no weather calls are made); the FIT records are matched to the
           segment by elapsed seconds since the activity start (FIT `t`, and detail epoch minus start)
halves H1, H2 of equal time; EF_k = speed_k / HR_k; drift % = (EF_1 / EF_2 - 1) * 100   (as F1)
h = R(mean HR of H1); label = "below" if drift <= 5.0 else "above"
```
Which runs are examined: 14.7 phase 2 picks runs of 60-150 min (`H5_RUN_S`) whose list-row elevation gain is <= 15 m per km (`DRIFT_MAX_GAIN`); the pre-filter is laxer than the 10 m/km segment limit because Garmin's whole-run `elevationGain` carries the same GPS noise as the samples, and the segment is then checked on smoothed altitudes (user decision 2026-11-18).
Temperature: the Forerunner 55 records none, so in practice heat is not filtered; every evidence row reports `temperature` in C when the FIT data carries it and `"unknown"` otherwise, and the coach is told that heat is unfiltered for "unknown" rows.
The method has two steps, tested separately: segment (one run's samples -> an evidence row `{h, drift, label, segment bounds, length, temperature}` or a rejection with its reason) and bounds (the evidence rows -> bounds, estimate, confidence).
A "below" run says LT1 HR >= h; an "above" run says LT1 HR <= h.
F1's "at LT1" band (3.5-5%) counts as "below" here, because at or below LT1 both give a lower bound.
Combining the bounds:
```
evidence: (h_i, label_i) for every accepted run
errors(c) = #{"below" runs with h_i > c} + #{"above" runs with h_i < c}, for integer c from min(h) - 1 to max(h) + 1
E = min over c of errors(c); C = {c : errors(c) = E}; cL = min C; cU = max C
no "above" run: one-sided result "LT1 HR >= max h of below runs", no estimate
no "below" run: one-sided result "LT1 HR <= min h of above runs", no estimate
otherwise: H5 LT1 HR = R((cL + cU) / 2); gap = cU - cL
confidence: medium if E = 0, gap <= 8 bpm and >= 2 runs with each label;
            low if (E = 0 and gap <= 15 bpm) or (E = 1 and >= 5 runs in total);
            otherwise no estimate, reported as "bounds too wide" (E = 0) or "inconsistent drift evidence" (E >= 1),
            with the runs listed
H5 LT1 pace = R(1000 / v(H5 LT1 HR)) with v from H2; null if H2 failed
```
Contradictions (a "below" run at a higher HR than an "above" run) are absorbed by minimising errors; the interval then covers both runs, so the gap widens and the confidence drops.
All numbers in this method (5% split, 45-75 min, 24 C, the confidence limits) are pace heuristics except where F1 or the sources below give them.
`pace` does not normalise drift for segment length, although drift grows with duration (below); it reports each run's length so the coach can weigh short segments less.
Inputs: per-sample HR, distance, time, elevation, cadence (the detail data already fetched); temperature if the cached FIT data has it; list rows for the previous-day rule.
Known error and validity:
- No study validates LT1 bounds from training-run drift; F1 itself is unvalidated (section 8).
- Drift happens below LT1 too: at 90% of LT1 speed, HR rose 5.5-5.9% over 90 min in 31 recreational runners, and LT1 speed fell 5-6% (Nuuttila et al. 2025); near VT1 speed HR rose 3.7% in 20 min (Gronwald et al. 2024); the HR slow component grows with relative intensity (Teso et al. 2025).
  These are start-to-end rises, not half-versus-half decoupling, so they are larger than the F1 measure of the same run, but they show that a fixed 5% split is soft.
- Cardiovascular drift is a progressive fall in stroke volume with a rise in HR after 10-20 min of exercise (Coyle and Gonzalez-Alonso 2001), which is why the first 15 min are excluded.
- Heat: hyperthermia (core temperature +1 C) and dehydration (4% of body mass) each lowered stroke volume by 7-8% with a compensating HR rise, and much more when combined (Gonzalez-Alonso et al. 1997); speed at VT1 was about 7% lower at 30 C than at 19 C in trained runners (Hunter et al. 2026).
  In heat the same pace is a higher relative intensity and HR rises more, so a hot run can read as "above" for reasons unrelated to the runner's usual LT1.
- Durability tests should control nutrition (carbohydrate availability), hydration and environment, and in cycling terrain is listed as a factor to control (Hunter et al. 2025); `pace` filters hills and stops, heat only when the FIT data carries temperature (not on the Forerunner 55), and not glycogen or hydration, which it cannot see.
- Decoupling of HR from speed is a validated durability measure in large data sets (Smyth et al. 2022), which supports the measurement but not its use as an LT1 detector.
- Expected direction of error: undetected heat, dehydration or low glycogen create false "above" runs and pull the estimate down.
Evidence grade: weak/expert opinion.

Sources:
- Coyle EF, Gonzalez-Alonso J. 2001. Cardiovascular drift during prolonged exercise: new perspectives. Exerc Sport Sci Rev 29(2):88-92. https://doi.org/10.1097/00003677-200104000-00009 (abstract)
- Gonzalez-Alonso J, Mora-Rodriguez R, Below PR, Coyle EF. 1997. Dehydration markedly impairs cardiovascular function in hyperthermic endurance athletes during exercise. J Appl Physiol 82(4):1229-1236. https://doi.org/10.1152/jappl.1997.82.4.1229 (abstract)
- Nuuttila OP, Laatikainen-Raussi V, Vohlakari K, Laatikainen-Raussi I, Ihalainen JK. 2025. Durability in recreational runners: effects of 90-min low-intensity exercise on the running speed at the lactate threshold. Eur J Appl Physiol 125(3):697-705. https://doi.org/10.1007/s00421-024-05631-y (full text)
- Hunter B, Fujii N, Maunder E, Muniz-Pumares D. 2026. Durability of Physiological Determinants of Distance-Running Performance in Warm and Temperate Environments. Scand J Med Sci Sports 36(11):e70394. https://doi.org/10.1111/sms.70394 (full text)
- Gronwald T et al. 2024. (as in section 8) (full text)
- Teso M et al. 2025. (as in section 4) (abstract)
- Hunter B et al. 2025. (as in section 4) (full text)
- Smyth B et al. 2022. (as in section 2) (full text)
- House S. 2026. (as in section 8) (full text)
- Friel J. 2008, updated 2026. (as in section 8) (full text)

## 8. Method F1: HR drift / aerobic decoupling test (LT1 field test)

Protocol (Uphill Athlete, House 2026, adapted to running):
- Chest strap; flat running track, flat loop or treadmill; no hilly out-and-back course, because the two halves would carry different work.
- Warm-up 10-15 min, building gradually to the target and holding it stable for the last 2-3 min; press lap at the end of the warm-up.
- Steady part 60 min (minimum 40 min; tests shorter than 40 min should not be trusted), same pace and incline throughout, no stops; press lap at the end; 10 min cool-down.
- Target: start at the current LT1 HR estimate (first test: conversational, "speak in full sentences").
- Hold pace constant, not HR: HR must be free to drift.
- Avoid heat, a fasted state, and the day after a hard session (pace heuristic, following the conditions Hunter et al. 2025 say affect drift).
Formula:
```
steady part = from warm-up lap end to test lap end (or 900 s .. 900 s + duration if no laps)
reject if: steady duration < 2400 s; total stopped time > 60 s; any stop > 30 s;
           total ascent > 10 m per km (outdoor)
split steady part into two halves of equal time: H1, H2
outdoor:   EF_k = speed_k / HR_k        (speed from distance, HR mean over the half)
           drift % = (EF_1 / EF_2 - 1) * 100
treadmill: drift % = (HR_2 / HR_1 - 1) * 100   (belt speed constant; device speed ignored)
verdict:   drift < 3.5      -> "below LT1": retest with start HR 5 bpm higher
           3.5 <= drift <= 5 -> "at LT1": LT1 HR = HR_1 (rounded), LT1 pace = 1000 / speed_1
           drift > 5        -> "above LT1": retest with start HR 5 bpm lower
also report speed CV across 60 s sub-windows; if > 3%, verdict carries "uneven pace"
```
At constant pace the outdoor and treadmill formulas give the same value, and both equal the Uphill Athlete hand formula ((second-half HR / first-half HR) - 1) x 100.
TrainingPeaks computes Pa:HR decoupling by comparing the first and second half of a steady effort with the same 5% threshold (Friel).
Deviation from Uphill Athlete: they set AeT HR to the starting HR of the test; `pace` uses the first-half mean HR because a single starting value is noisy; with drift at most 5% the two differ by about 1-3 bpm.
Inputs: per-sample HR, distance, time, elevation; laps (one `get_activity_splits` call).
The analysis reports `temperature` as in section 7 (`"unknown"` on the Forerunner 55), so the coach knows heat was not checked.
Known error and validity:
- No peer-reviewed study validates the drift test or the 3.5-5% band against lab LT1/VT1.
- HR drifts at intensities below and around LT1: at a speed close to VT1 HR rose from 148.4 to 153.9 bpm (+3.7%) in 20 min with stable VO2, and at a speed close to VT2 from 166.6 to 175.7 bpm (+5.5%) (Gronwald et al. 2024); at about 89% of VT1 speed over about 2.5 h HR drift was measurable and moderately to well reproducible (Malinen et al. 2026); the HR slow component grows with relative intensity (Teso et al. 2025).
- So drift separates "clearly easy" from "clearly too hard" but the boundary is soft; treat the result as plus or minus about 5 bpm.
- Decoupling of HR from speed is a validated durability marker in large data sets (Smyth et al. 2022), which supports the measurement itself but not its use as an LT1 detector.
Evidence grade: weak/expert opinion.

Sources:
- House S. 2026. Understanding the Heart Rate Drift Test: A Practical Guide for Endurance Athletes. Uphill Athlete. https://uphillathlete.com/heart-rate-drift/ (full text)
- Friel J. 2008, updated 2026. Aerobic Endurance and Decoupling. TrainingPeaks. https://www.trainingpeaks.com/learn/articles/aerobic-endurance-and-decoupling/ (full text)
- Gronwald T, Horn L, Schaffarczyk M, Hoos O. 2024. Correlation properties of heart rate variability for exercise prescription during prolonged running at constant speeds: A randomized cross-over trial. Eur J Sport Sci 24(11):1539-1551. https://doi.org/10.1002/ejsc.12175 (full text)
- Malinen T, Nuuttila OP, Matomaki P, Uusitalo A, Kyrolainen H. 2026. Test-Retest Reliability of Physiological Resilience During and After Prolonged Moderate-Intensity Running in Well-Trained Runners. Eur J Sport Sci 26(5):e70178. https://doi.org/10.1002/ejsc.70178 (abstract)
- Teso M et al. 2025. (as in section 4) (abstract)
- Smyth B et al. 2022. (as in section 2) (full text)
- Hunter B et al. 2025. (as in section 4) (full text)

## 9. Method F2: 30-minute solo time trial (LT2 field test)

Protocol (Friel; McGehee et al. 2005):
- Chest strap; flat course or track, alone (not a race, no partners); calm, cool weather.
- Warm-up 15 min easy plus 3 x 20 s strides, then 3 min easy.
- Run 30 min as hard as can be sustained evenly for the whole 30 min (most people start too fast; even pacing matters).
- Press lap at the start, at 10 min, and at 30 min; cool down 10 min.
Formula:
```
TT = from start lap to end lap (or the fastest 1800 s window if laps are missing)
reject if: duration < 1680 s; any stop > 10 s; total ascent > 10 m per km
LT2 HR   = mean HR over t in [600 s, 1800 s) of the TT   (last 20 min)
LT2 pace = 1800 s / distance_km over the whole 30 min    (s/km)
pacing check: speed of the second 15 min / speed of the first 15 min;
              < 0.95 -> flag "faded, started too fast", confidence low
```
Inputs: per-sample HR, distance, time, elevation; laps (one `get_activity_splits` call); RPE from `get_activity` (expected 9-10, reported only).
Known error and validity:
- In 27 competitive runners and triathletes, speed from a 30-min time trial did not differ from the lab LT (4 mmol/L), SEE 0.21 m/s; the HR estimate also did not differ, SEE 8.0 bpm (McGehee et al. 2005).
- Friel's "last 20 min" HR rule is practitioner guidance described by its author as an approximation.
- A 30-min all-out speed is slightly above CS, because CS can be held for only about 20-30 min (Jones et al. 2019); expect LT2 pace from this test to be equal to or a few s/km faster than CS.
Evidence grade: moderate for LT2 pace (one validation study); weak/expert opinion for LT2 HR (one study, SEE 8 bpm, plus practitioner rule).

Alternative not chosen as default: a two-trial CS test (3 min and 12 min all-out on a track, 30-60 min apart, longest first or random order) gives CS and D' (Lipkova et al. 2025; Anderson et al. 2026 recommend at least three trials, one of about 3 min and one of about 15 min).
It is harder to execute alone and gives no HR anchor, so `pace` offers it only when the user asks for a CS test; the analysis is H1's formula on the two or three trial distances.

Sources:
- McGehee JC, Tanner CJ, Houmard JA. 2005. A comparison of methods for estimating the lactate threshold. J Strength Cond Res 19(3):553-558. https://doi.org/10.1519/15444.1 (abstract)
- Friel J. 2012, updated 2026. Joe Friel's Quick Guide to Setting Zones. TrainingPeaks. https://www.trainingpeaks.com/learn/articles/joe-friel-s-quick-guide-to-setting-zones/ (full text)
- Jones AM et al. 2019. (as in section 2) (abstract)
- Lipkova L et al. 2025. (as in section 3) (full text)
- Anderson M et al. 2026. (as in section 3) (full text)

## 9a. Method F3: DFA alpha1 ramp test (LT1 field test)

Numbered 9a so that the later section numbers, which other documents cite, stay unchanged (controller ruling).
Protocol (adapted from the incremental ramps of Rogers et al. 2020 and Van Hooren et al. 2025 to a stepped outdoor run, pace heuristic):
- Chest strap; flat route or track; no stops; calm, cool weather.
- Warm-up 10 min easy; press lap.
- 8 steps of 3-4 min each at constant pace (the default; at least 6), pressing lap at each step change; the first step about 21 bpm below the current LT1 estimate, each step about 6 bpm higher, the last clearly above LT1.
  At least 6 steps, because the gate below needs 3 usable steps on each side of alpha1 0.75; 8 leave room for steps dropped for artifacts or coupling (controller ruling; the user's first ruling said 5-6 steps).
- Cool-down 5-10 min.
- Hold the pace of each step, not its HR; RPE rises from about 2 to about 6 of 10.
Analysis:
```
steps = the laps between the warm-up lap and the cool-down lap (or none: the test is refused, steps need laps)
per step: one alpha1 window = the last 120 s of the step (the first 1-2 min absorb the alpha1 lag after the
          pace change, section 6), with the section 6 RR extraction, artifact correction, DFA and skip rules
          (gap, stop, < 200 corrected beats); window HR, window speed and artifact share as in section 6
drop a step whose window has artifact share > 5%
drop a step at coupling risk (the section 6 rule on the step's HR and mean cadence); report the count
drop a step whose window has step share > 0.25 (the section 6 step-interference rule, checked after coupling);
          the step row carries its step share; report the count (`dropped_step`)
gate (as section 6): rho = Spearman of step HR and alpha1 over the kept steps;
      need rho <= -0.6 and >= 3 kept steps with alpha1 > 0.75 and >= 3 with alpha1 < 0.75;
      otherwise status "alpha1_unusable" with the reason, no estimate, nothing saved
P = kept steps with 0.5 <= alpha1 <= 1.0
refuse if: no step in P with alpha1 > 0.75 or none with alpha1 < 0.75 (the ramp does not cross 0.75),
           or the HR fit fails (d >= 0)
fit HR = c + d * alpha1 and v = e + f * alpha1 by OLS on P (need d < 0; v only outdoors, need f < 0)
LT1 HR   = R(c + d * 0.75)        LT1 pace = R(1000 / (e + f * 0.75))   (treadmill: HR only)
bracketed = the gate passed and at least 1 step in P on each side of 0.75
confidence: medium if >= 2 steps in P on each side of 0.75 and the median artifact share of the kept steps <= 3%;
            otherwise low; never high
```
Why the confidence is higher than for H4: the protocol sets steady, known paces and the steps cross the threshold by design, so the lag and steadiness problems of training runs (section 6) are controlled.
Recommended pairing (the coach's default LT1 suggestion while H4 is not marked unreliable, section 6): run F3 first, then one F1 drift test at the ramp's LT1 HR.
`zones analyze --test ramp` records the gate outcome in the H4 memory of section 6 on every run of the command (a local write even without `--save`); an "alpha1_unusable" ramp counts toward the streak; on this athlete the one ramp so far would have failed the gate, so F1 is the expected primary LT1 test.
`zones analyze --test drift` compares an "at LT1" F1 result with the newest F3 record of the last 28 days: within 5 bpm the F1 record keeps confidence medium and notes the agreement; more than 5 bpm apart it is saved with confidence low and the note "F3 and F1 disagree by N bpm" (controller ruling).
Inputs: data derived from the FIT file (RR intervals, per-second records), laps (one `get_activity_splits` call), the per-sample data of the run.
Known error and validity:
- HR at alpha1 0.75 agreed with VT1 at group level on continuous incremental ramps (bias -1.9 bpm, limits -12 to +8 bpm, Rogers et al. 2020; bias -0.9 bpm, limits -12.2 to +10.5 bpm with a consumer chest belt, Van Hooren et al. 2025); section 6 lists the negative findings.
- No opened study validates a stepped outdoor ramp of 3-4 min steps with one window per step; the step length follows the alpha1 lag noted in section 6 (pace heuristic).
- On this athlete a 13-step ramp gave alpha1 that did not fall with intensity (section 6, "Observed on this athlete").
Evidence grade: moderate for group-level agreement with VT1 on incremental tests; weak for an individual and for this stepped field protocol.

Sources:
- Rogers B et al. 2020. (as in section 6) (full text)
- Van Hooren B et al. 2025. (as in section 6) (abstract)
- Rogers B, Gronwald T. 2022. (as in section 6) (full text)

## 9b. Method R: race efforts (LT2)

Numbered 9b for the same reason as 9a.
Rules in this section are user decisions of 2026-11-18 unless marked otherwise.
Protocol: none; the user or the coach marks a run as an all-out effort with `zones analyze ID --test race [--laps A-B]` (Garmin rarely tags races, so the list-row event type is not used).
The effort is laps A to B (1-based, inclusive), else the whole activity.
Without `--laps`, the analysis is refused when the first or last lap is slower than 0.85 x the median lap speed, with a hint to pass `--laps`.
The median is taken over the laps between the first and the last; with only two laps each is compared with the faster one (pace heuristic and controller ruling: with three laps, warm-up, effort and cool-down, a plain median is a slow lap and would let the warm-up through).
Any median rule misses warm-ups that make up about half or more of the inner laps; pass `--laps` then.
Without `--laps`, standing before the start and walking after the finish (samples slow by the section 1 stop rule, or without a distance change) are cut from the ends before any analysis, so they are not stops of the effort and do not shift the 600 s HR offset or the pacing halves (controller ruling).
Duration = the moving time of the effort (section 1 stop rules); distance = the distance covered in that moving time (the same pairs of consecutive moving samples, so metres walked during a stop are left out with its time).
Classes by duration:
```
duration < 120 s          refused (bad_args)
120 s .. 1200 s           a critical-speed point (class "cs")
1200 s < duration <= 3600 the F2 rules (class "time_trial")
duration > 3600 s         a check only (status "check_only"): average pace and HR, never an LT2 value
```
Critical-speed point (120..1200 s):
```
reject if the ascent is above 10 m per km (the section 1 flat rule on smoothed altitude, as for the time-trial
          class; controller ruling): status "rejected", and the effort is not stored as a point
points = this effort + stored efforts + Garmin 1 km, 1 mile and 5 km records (section 3), within the last 16 weeks,
         durations 120..1200 s; a record set in a stored effort's activity, or in this activity, is dropped
if the point durations span at least 1.5 x (max / min):
    fit d = CS * T + D' by OLS on the points (as H1)
    reject if D' is outside 50..500 m or CS is not below the speed of the longest point (the H1 gates)
    confidence medium if the points come from >= 2 activities, else low
elif duration >= 600 s:
    single-effort range: CS = (distance - D') / duration for D' in 150..300 m; central value at D' = 225 m
    confidence low; D' is not stored (it was assumed)
else:
    status "point_only": stored as a point, no estimate
LT2 pace = R(1000 / CS)
LT2 HR   = R(a + b * CS) with H2 from the current records (14.5), else from the newest estimate that logged one,
           else null with a reason; flagged "extrapolated" when CS > v_max; null when CS > 1.1 x v_max (the 14.5 cap)
```
`--save` stores the effort as `{"activity", "date", "duration_s", "distance_m"}` in `zones.json` `efforts` (one per activity) whatever the status, and, for an `ok` result, a `field_test` record with method `race_cs`.
Stored efforts inside the history window are also extra H1 points of `zones estimate` (section 3, 14.1).
The run of a stored effort is left out of the H1 training windows: it counts through its effort point, so its own 180/420/720 s sub-windows do not enter the fit next to it (controller ruling).
Time-trial class (1200..3600 s): the F2 analysis (section 9) on the effort, without the 1680 s minimum; LT2 pace = the whole effort (moving time / distance); LT2 HR = mean HR from 600 s to the end; the same stop and terrain rejections and pacing check; `--save` stores a `field_test` record with method `race_tt`.
Check-only class (above 3600 s): "longer than 60 min: below LT2, a check only"; `--save` is refused.
Why these limits:
- The CS model describes efforts of about 2 to 20 min, and its D' is poorly determined (error 5-28%) (Anderson et al. 2026); the training-data methods used 3-12 min (Hunter et al. 2023) and 400 m to 5 km (Smyth and Muniz-Pumares 2020).
- One effort cannot separate CS from D'; the 150..300 m D' range is a pace heuristic for a trained recreational runner, so the single-effort result is a range with confidence low, and it needs at least 600 s so that the D' term is a small share of the distance.
- Worked check (unit test): 1165 s for 5042 m gives CS 4.070 m/s (D' 300 m) to 4.199 m/s (D' 150 m), central 4.135 m/s, LT2 pace 242 s/km (range 246..238).
- An all-out effort of 25-60 min sits near LT2: the 30-min time trial validated against the lab LT (McGehee et al. 2005) is in this class.
  On 50-60 min efforts the mean HR from 600 s reads high because HR keeps drifting (section 8); the result is still a field test, read with that bias in mind.
- Above 60 min the effort is below LT2 (pace heuristic): a pace and HR check only.
Evidence grade: moderate for CS from maximal efforts of 2-20 min; weak for the single-effort range (assumed D'); as section 9 for the time-trial class.

Sources:
- Anderson M et al. 2026. (as in section 3) (full text)
- Hunter B et al. 2023. (as in section 3) (abstract)
- Smyth B, Muniz-Pumares D. 2020. (as in section 3) (full text)
- McGehee JC et al. 2005. (as in section 9) (abstract)

## 10. Lab tests (source "lab")

Protocol: the user enters LT1 and LT2 HR and/or pace from a lab report with `zones add --source lab`; the record becomes current for each threshold it has, if its date is on or after the date of that threshold's current record (plan 5).
A lab record has no H2 regression, so it gives no z1/z2 pace floors (section 14.5).
Store `method` as the lab's own criterion when known (for example "VT1/VT2", "LT1 2 mmol/L / OBLA 4 mmol/L", "D-max").
Do not convert lab values; note that lab lactate LT2 is usually slower than CS (Rainsberger et al. 2026; Galan-Rioja et al. 2020), so a later history estimate with a faster LT2 pace is not by itself evidence of improvement.
Lab treadmill speeds are often at 0-1% grade and treadmill speed differs from outdoor GPS pace; HR values transfer better than pace (pace heuristic).
Evidence grade: strong for the lab measurement itself; the cross-method offsets in section 2 are moderate.

## 11. Zone models

Three-zone model (Seiler): zone 1 below LT1 (about 2 mmol/L, below VT1), zone 2 between LT1 and LT2 (2-4 mmol/L, VT1-VT2), zone 3 above LT2 (4 mmol/L and above, above VT2) (Seiler 2010; Stoggl and Sperlich 2015).
The 82.3% CS / CS split used by Muniz-Pumares et al. 2025 is the same model with CS as the upper anchor.
Five-zone model: the Norwegian Olympic model reported by Tonnessen et al. 2014 (as tabulated in Stoggl and Sperlich 2015) uses lactate bands Z1 below 1.2, Z2 1.3-2.0, Z3 2.1-3.6, Z4 3.7-5.7, Z5 above 5.8 mmol/L, with HR bands of 54-73, 74-83, 84-88, 89-93 and 94% HRmax and above.
So LT1 (about 2 mmol/L) is the Z2/Z3 boundary and LT2 (about 4 mmol/L) is close to the Z3/Z4 boundary; Z5 starts at about the midpoint between LT2 and HRmax in that table (88% to 94% of a 100% HRmax).
Friel's LTHR model instead puts LTHR at the floor of zone 5a (Z1 < 85%, Z2 85-89%, Z3 90-94%, Z4 95-99%, Z5a 100-102% of LTHR); `pace` does not use it because it splits the LT1-LT2 band in a way that does not line up with LT1.
Uphill Athlete sets the top of zone 1 at AeT HR minus 10% (House 2026).
Evidence grade: strong that three-zone models anchored on LT1/LT2 describe how endurance athletes train; weak/expert opinion for the exact five-zone floors (convention, not physiology).

Sources:
- Seiler S. 2010. What is best practice for training intensity and duration distribution in endurance athletes? Int J Sports Physiol Perform 5(3):276-291. https://doi.org/10.1123/ijspp.5.3.276 (abstract)
- Stoggl TL, Sperlich B. 2015. The training intensity distribution among well-trained and elite endurance athletes. Front Physiol 6:295. https://doi.org/10.3389/fphys.2015.00295 (full text)
- Muniz-Pumares D et al. 2025. (as in section 2) (abstract)
- Friel J. 2012, updated 2026. (as in section 9) (full text)
- House S. 2026. (as in section 8) (full text)

## 12. How often thresholds change (retest interval)

Recreational runners improved maximal treadmill speed by 2-3% over a 10-week period of increased intensity or volume (Nuuttila et al. 2021), and 10-km time by 3-6% over a 15-week program (Nuuttila et al. 2022).
Thresholds rise strongly in previously sedentary people and less in already trained ones; detraining lowers them (Londeree 1997, meta-analysis of 34 studies).
Day-to-day HR at a given speed varies by about 3-4 bpm (Nuuttila et al. 2022), so a smaller change in a threshold HR is noise.
Uphill Athlete advises repeating the drift test every 4-6 months or sooner after a clear fitness change (House 2026); Friel advises testing early in the base and build periods.
Evidence grade: moderate that meaningful changes take about 6-12 weeks in recreational runners; weak/expert opinion for any exact interval.

Sources:
- Nuuttila OP, Nummela A, Hakkinen K, Seipajarvi S, Kyrolainen H. 2021. Monitoring Training and Recovery during a Period of Increased Intensity or Volume in Recreational Endurance Athletes. Int J Environ Res Public Health 18(5):2401. https://doi.org/10.3390/ijerph18052401 (abstract)
- Nuuttila OP et al. 2022. (as in section 4) (full text)
- Londeree BR. 1997. Effect of training on lactate/ventilatory thresholds: a meta-analysis. Med Sci Sports Exerc 29(6):837-843. https://doi.org/10.1097/00005768-199706000-00016 (abstract)
- House S. 2026. (as in section 8) (full text)
- Friel J. 2012, updated 2026. (as in section 9) (full text)

## 13. Rejected methods

%HRmax or %HRR rules (for example LT1 = 75% HRmax): rejected; LT ranged 60-90% HRmax across individuals (Iannetta et al. 2020; Jamnick et al. 2020).
`pace` never fills a threshold from %HRmax; with no usable data the method reports status `insufficient_data` with its reasons (not an error) and `pace` suggests a field test.
DFA alpha1 = 0.5 as LT2: rejected; individual agreement with VT2 is poor and alpha1 stays above 0.5 at MLSS in constant runs (section 6).
DFA alpha1 = 0.75 for LT1 is used (H4, F3).
Conconi HR deflection: not studied further; McGehee et al. 2005 found the 30-min time trial better among four field tests.
Garmin lactate threshold detection: not available on the Forerunner 55.

## 14. Chosen set

### 14.1 LT2 from history: H1 + H2

LT2 pace = 1000 / CS, with CS from the linear distance-time fit of the best 180 s, 420 s and 720 s flat stop-free windows in the examined runs of the last 8 weeks.
Extra points: the Garmin records of section 3 and the stored race efforts of section 9b inside the same window (120..1200 s); a record set in a stored effort's activity is dropped, so one effort counts once (user decision 2026-11-18).
A race effort analysed with `zones analyze --test race` gives its own LT2 (method R, section 9b); its record (`race_cs` or `race_tt`) is a `field_test` record and becomes current only through `zones accept`, as any other.
LT2 HR = HR-speed regression (H2) evaluated at CS, only if the fastest steady segment reaches 0.90 x CS; otherwise LT2 HR is null and the coach suggests the 30-min test.
Confidence: medium or low by the H1 gates; low if H2 SEE > 5 bpm.
Worked example (unit test):
- Best windows: 180 s -> 900 m, 420 s -> 1860 m, 720 s -> 3060 m.
- Fit: CS = 4.000 m/s, D' = 180 m, LT2 pace = 250 s/km (4:10/km).
- Steady segments (speed m/s, HR): (2.8, 130), (3.0, 135), (3.2, 140), (3.4, 145), (3.6, 150), (3.8, 155), each pair repeated twice from 3 runs (n = 12).
- Fit: a = 60, b = 25, SEE = 0.
- Max steady speed 3.8 >= 0.9 x 4.0 = 3.6, so LT2 HR = 60 + 25 x 4.0 = 160 bpm.

### 14.2 LT1 from history: H5 and H4, with H3 as fallback

Each method runs on its own data: H5 on the steady strap runs of the last 12 weeks (section 7), H4 on the downloaded FIT files whose RR data qualify (section 6), H3 on H1 + H2 (section 5).
H3 is always computed and reported as a check; it is used only when neither H4 nor H5 gives an estimate, and it is never averaged in.
Combination (pace heuristic):
```
agree(X, Y) = abs(X.HR - Y.HR) <= 5 bpm
if H4 and H5 both give an estimate:
    if agree(H4, H5): LT1 HR = R((H4.HR + H5.HR) / 2); method "H4+H5"; own confidence = the higher of the two
    else:             pick the one with the higher own confidence; tie -> H5;
                      flag "H4 and H5 disagree by N bpm" and suggest F1 starting at R((H4.HR + H5.HR) / 2)
elif only one of H4, H5 gives an estimate: pick it
else: pick H3 (confidence low)
LT1 pace = H3 picked: H3's own pace (1000 / (0.823 x CS));
           H4, H5 or H4+H5 picked: R(1000 / v(LT1 HR)) with v from H2 when H2 exists,
           else H4's own pace (median run speed); H5 alone has no pace without H2
final confidence = medium only if the picked result's own confidence is medium,
                   at least one other method agrees with the final HR,
                   and no method with own confidence medium disagrees with it;
                   otherwise low; never high (no field test)
```
H4 flagged, not hidden (user decision 2026-11-18): when the H4 memory update of the same estimate marks alpha1 unreliable (section 6), the H4 result keeps its values and reasons, gains the flag `alpha1_unreliable` and a reason, and the combination above treats H4 as giving no estimate; `lt1` notes "H4 N bpm not combined".
When the mark was already set before the estimate and `--retry-h4` was not given, H4 stays "skipped" with no values (nothing is computed, so nothing is flagged).
H5 wins a tie because its result is checked by the same measurement the F1 field test uses, and two of its known failure modes (hills, stops) are filtered (heat is not, section 7); H4 wins when its own confidence is higher, because its marker has lab validation at group level.
The record's `method` lists the methods used (for example "H4+H5"), with H3's value and any one-sided H4 or H5 notes in the details.
Worked example, H3 (same data as 14.1):
- LT1 speed = 0.823 x 4.0 = 3.292 m/s; LT1 pace = 303.77 -> 304 s/km (5:04/km).
- LT1 HR = 60 + 25 x 3.292 = 142.3 -> 142 bpm.
Worked example, H5 bounds (unit test; input is evidence rows only):
- Accepted runs (h, drift %): (132, 2.1), (136, 3.0), (138, 4.6) -> "below"; (146, 6.2), (149, 7.5), (152, 9.0) -> "above".
- errors(c) = 0 for c in 138..146 -> cL = 138, cU = 146, E = 0, gap = 8; H5 LT1 HR = R(142.0) = 142; 3 runs per label -> medium.
- H5 LT1 pace with H2 (a = 60, b = 25): v = (142 - 60) / 25 = 3.28 m/s -> 304.9 -> 305 s/km.
- Add (144, 3.2) "below" and (141, 6.0) "above": errors = 1 for c in 138..141 and 144..146, 2 for 142..143 -> E = 1, cL = 138, cU = 146, estimate 142, 8 runs -> low.
- Only the three "below" runs -> one-sided "LT1 HR >= 138", no estimate.
Worked example, H5 segment (unit test; input is one run's samples):
- A run whose steady segment lasts 40 min (2400 s) -> rejection "segment shorter than 2700 s".
- A run whose cached FIT data has mean temperature 27 C over the segment -> rejection "mean temperature >= 24 C".
- A run without temperature data -> evidence row with `temperature: "unknown"`.
Worked example, H5 candidates (unit test; list rows only):
- A run starting 20 h after the start of a race, or of a run with `aerobicTrainingEffect` 4.2 -> not a candidate; the same run 30 h after -> candidate.
Worked example, H4 regression (unit test):
- Run A, qualifying windows (alpha1, HR, speed m/s), cadence 172 steps/min, each repeated 20 times (60 points per side of 0.75; gate: rho = -1, so usable): (1.00, 130, 2.80), (0.90, 136, 3.04), (0.80, 142, 3.28), (0.70, 148, 3.52), (0.60, 154, 3.76), (0.50, 160, 4.00).
- Fits: HR = 190 - 60 x alpha1, v = 5.20 - 2.40 x alpha1, r = -1; run LT1 HR = 145, run LT1 speed = 3.40 m/s.
- Run B: same alpha1 values, HR 2 bpm higher and speed 0.08 m/s higher -> HR = 192 - 60 x alpha1, v = 5.28 - 2.40 x alpha1 -> 147 bpm, 3.48 m/s.
- H4: LT1 HR = median(145, 147) = 146; LT1 pace = R(1000 / 3.44) = 291 s/km; spread 2; with median artifact share 1.5% -> medium.
- A run with windows only at alpha1 1.00, 0.90, 0.80 -> "alpha1_unusable", reason "one-sided", note "alpha1 above 0.75 up to 142 bpm"; the H4 streak is unchanged.
- Step interference (unit test): beats at RR = 60 / 134 s plus a 20 ms sine at 0.65 Hz sampled at the beat times plus 2 ms white noise, step rate 173 -> step frequency 0.65 Hz, share > 0.5; without the sine share < 0.15; HR 176 at 180 steps/min -> null (left to coupling).
  Step frequency: (173, 134) -> 0.65 Hz; (176, 110) -> 44 / 60 Hz (k = 2); (180, 92) -> 4 / 60 Hz.
- Flagged H4 (unit test): one estimate with two not-monotonic runs followed by a usable run (the run A windows above) -> H4 marked unreliable by this estimate, H4 keeps LT1 HR 145 with the flag, and the LT1 method is not H4.
- Gate (unit test): a run whose windows follow the observed pattern of section 6 (the 13 (HR, alpha1) pairs, each repeated 20 times, cadences as in the 14.8 gate test) -> coupling drops reported (40 windows), rho = +0.49 -> "alpha1_unusable", reason "not monotonic"; two such runs in a row (streak 2) -> H4 marked unreliable, and the next `zones estimate` reports H4 "skipped" and makes no FIT download; `--retry-h4` clears the mark.
- A window of 300 raw beats with 12 flagged (4%) is used; with 18 flagged (6%) it is skipped; a window with 190 corrected beats is skipped.
Worked example, artifact correction (unit test), values in s around a local median of 0.400:
- 0.800 -> 0.400, 0.400 (missed beat); 0.150 followed by 0.250 -> 0.400 (extra beat, 2 flags); 0.267 followed by 0.400 -> replaced by 0.400; 3.891 -> gap, no correction; 0.430 -> valid (7.5% from the median).
Test strategy for alpha1 (an exact small example is not practical, so test statistical properties with a seeded generator; the expected values below come from a pace simulation of the algorithm in section 6 with 200 series of N = 300, not from a published source).
The unit tests use 50 seeded series per case with the same tolerances, to keep the pure-Python suite fast; with the simulated SDs the standard error of a 50-series mean is at most 0.012, well inside each tolerance:
- Gaussian white noise: mean alpha1 0.58 +/- 0.03 (simulation: 0.584, SD 0.056).
  The theoretical 0.5 is not reached because DFA with box sizes 4..16 is biased upward for white noise; N = 2000 gave the same 0.585, so the bias comes from the box sizes, not the length.
- 1/f noise by spectral synthesis (random phases, amplitude k^-0.5 at frequency index k): mean 1.03 +/- 0.05 (simulation: 1.033, SD 0.079).
- Brownian noise (cumulative sum of white noise): mean 1.43 +/- 0.05 with the lambda 500 detrending (implementation measured 1.430 over 50 seeded series; the earlier simulation value 1.498 was obtained with ineffective detrending, and 1.49 is what the series gives without detrending; detrending removes part of the low-frequency trend that drives Brownian scaling, so the lower value is the expected one).
- Invariance: alpha1(a * x + b) equals alpha1(x) within 1e-9 for a > 0; a constant series returns null.
- Detrending: white noise (SD 0.01 s around 0.400 s) plus a trend of 0.0005 s per beat: with smoothness priors detrending alpha1 stays within 0.01 of the trend-free value (simulation: max 0.006); without detrending it rises by about 0.05.
- Artifact correction: on 50 seeded 1/f^0.6 series (the simulation used 40) (theoretical alpha1 0.8) mapped to RR around 0.400 s, merging 3% of beats into missed-beat pairs and correcting changes mean alpha1 by at most 0.02 (simulation: mean +0.008 at 3%, +0.017 at 6%, upward on average, as Rogers et al. 2021a report).
- The per-run regression and run combination are tested with alpha1 values injected by replacing the DFA function, as in the H4 regression example above.
Worked example, combination (unit test), H2 a = 60, b = 25:
- H3 142 (low), H5 142 (medium), H4 146 (medium): agree(H4, H5) since 4 <= 5 -> LT1 HR = R(144.0) = 144; LT1 pace = R(1000 / 3.36) = R(297.6) = 298 s/km (4:58/km); H3 142 also agrees -> medium.
- H4 152 (medium), H5 142 (medium): disagree by 10, tie -> H5, LT1 HR 142; H4 (medium) disagrees -> low; flag and suggest F1 at 147.
- H4 none, H5 only one-sided ("LT1 HR >= 138") -> H3: LT1 HR 142, LT1 pace 304 s/km, low; the H5 note is reported and is consistent with 142.

### 14.3 LT1 field test: HR drift test (F1)

Workout: 15 min warm-up building to target (last 3 min at target) + lap; 60 min steady at a constant pace that starts at the current LT1 HR, flat route or treadmill, no stops + lap; 10 min cool-down.
Intensity guidance: conversational, full sentences possible; hold pace, let HR drift; RPE about 3-4 of 10.
Workout DSL (`zones test-workout drift`), one rule shared with the ramp (14.8):
- current LT1 record with a pace: the steady step carries a pace target of that pace +/- 10 s/km, so the watch guides pace, not HR;
- otherwise, the newest `estimates` entry with an `lt1_hr`: HR targets only, on the last 3 min of the warm-up (`lt1_hr` +/- 3 bpm), and a steady step with no target and the note "hold the pace reached at the end of the warm-up; let HR drift" (controller ruling);
- with no estimate either: no targets, the warm-up note "build to conversational effort, RPE 3-4" and the steady note "conversational, constant pace".
Analysis: drift % = (EF_1 / EF_2 - 1) x 100 outdoors, (HR_2 / HR_1 - 1) x 100 on a treadmill; band 3.5-5% sets LT1 HR = HR_1 and LT1 pace = 1000 / speed_1; below 3.5% retest +5 bpm; above 5% retest -5 bpm.
Confidence: medium when the verdict is "at LT1" with even pace, lowered to low when the newest F3 record of the last 28 days disagrees by more than 5 bpm (section 9a); otherwise the record is not created (the verdict only steers the next test).
`zones analyze --save` therefore stores a record only for "at LT1" with even pace; any other verdict, or "at LT1" with "uneven pace", is refused with the verdict and the next-test suggestion.
Worked examples (unit tests):
- Halves (speed m/s, HR): H1 (3.20, 140), H2 (3.18, 146): drift = (3.20/140) / (3.18/146) - 1 = 4.94% -> at LT1; LT1 HR 140, LT1 pace 1000/3.20 = 312.5 -> 313 s/km (5:13/km).
- H1 (3.10, 138), H2 (3.09, 141): drift = 2.50% -> below LT1, retest at start HR + 5.
- H1 (3.30, 146), H2 (3.26, 156): drift = 8.16% -> above LT1, retest at start HR - 5.
- Steady part of 35 min -> rejected (shorter than 40 min).

### 14.4 LT2 field test: 30-minute solo time trial (F2)

Workout: 15 min easy + 3 x 20 s strides + 3 min easy; lap; 30 min even, maximal sustainable effort, flat, alone; laps at 10 min and 30 min; 10 min cool-down.
Intensity guidance: race effort for 30 min, start controlled (first 5 min should feel slightly too easy), RPE 9-10 at the end.
Analysis: LT2 HR = mean HR from 600 s to 1800 s; LT2 pace = 1800 / distance_km; fade flag if second-half speed < 0.95 x first-half speed.
Confidence: medium; low if faded or if stops occurred.
`zones analyze --save` stores a record for every test that is not rejected, with that confidence; a rejected test is not saved.
A race effort of 1200..3600 s is analysed with these rules, without the 1680 s minimum, and saved as a `race_tt` record (section 9b, user decision 2026-11-18).
Workout DSL (`zones test-workout time_trial`): the steps of the workout line above (warm-up, strides, recovery, 30 min at maximal sustainable effort with no target, cool-down).
Worked example (unit test):
- 30 min, distance 7200 m, mean HR 0-600 s = 158, mean HR 600-1800 s = 168; first 15 min 3600 m, second 15 min 3600 m.
- LT2 HR = 168 bpm; LT2 pace = 1800 / 7.2 = 250 s/km (4:10/km); speed ratio 1.00 -> no fade flag.

### 14.5 Zone mapping (rule name "lt-5z-v1")

Rounding: R(x) = floor(x + 0.5) on bpm and s/km.
This differs from Python's `round()`, which rounds halves to even: R(312.5) = 313 but `round(312.5)` = 312 (unit test).
Inputs: the zones come from the current LT1 record (LT1 HR and pace) and the current LT2 record (LT2 HR and pace, CS, D'), which may be different records (plan 5).
The H2 regression (a, b and its fitted speed range v_min..v_max) is taken from the current LT1 record's details, else from the current LT2 record's details (controller ruling); a record without one (for example a lab record) gives null z1 and z2 pace floors.
HRmax is the running maximum of section 1.
Three zones (HR): Z1 HR < LT1; Z2 LT1 <= HR < LT2; Z3 HR >= LT2.
Three zones (pace, s/km): Z1 slower than LT1 pace; Z2 from LT1 pace down to LT2 pace (exclusive); Z3 at or faster than LT2 pace.
Five Garmin HR zone floors:
```
zone1Floor = R(0.80 * LT1)
zone2Floor = R(0.90 * LT1)                 # Uphill: top of Z1 = AeT - 10%
zone3Floor = LT1                           # Tonnessen Z2/Z3 ~ 2 mmol/L
zone4Floor = LT2                           # Tonnessen Z3/Z4 ~ 4 mmol/L
zone5Floor = LT2 + R(0.5 * (HRmax - LT2))  # Tonnessen Z5 ~ midway LT2..HRmax
             if HRmax unknown: LT2 + R(0.5 * (LT2 - LT1))
maxHeartRateUsed = HRmax (if known)
                   if HRmax unknown: the push keeps Garmin's current maxHeartRateUsed
```
Watch zones sum to the three-zone model as Z1+Z2 | Z3 | Z4+Z5.
Five pace floors (s/km): z1, z2 = 1000 / v(floor HR) from the H2 regression (null if no regression, or if v is outside the fitted speed range by more than 10%, that is v < 0.9 x v_min or v > 1.1 x v_max); z3 = LT1 pace; z4 = LT2 pace; z5 = 1000 / (CS + D'/720) when D' is known (speed for a 12-min maximal effort), else null.
Validation: floors must be strictly increasing and zone5Floor < HRmax; with HRmax unknown, `zones push` checks zone5Floor < Garmin's current `maxHeartRateUsed` instead; otherwise `bad_args` and no push.
The 0.80 and 0.90 factors and the zone 5 midpoint are conventions (weak/expert opinion); only zone3Floor and zone4Floor carry physiological meaning.
Worked example (unit test), LT1 = 142, LT2 = 160, HRmax = 186, H2 a = 60, b = 25 fitted on speeds 2.0..4.0 m/s, CS = 4.0, D' = 180.
The H2 speed range here (2.0..4.0) is wider than the 14.1 example's (2.8..3.8) on purpose: with 2.8..3.8, z1 would be null because 2.16 < 0.9 x 2.8 = 2.52.
- floors 114, 128, 142, 160, 173.
- three zones: Z1 < 142, Z2 142-159, Z3 >= 160.
- pace floors: z1 1000/2.16 = 463, z2 1000/2.72 = 368, z3 304, z4 250, z5 1000/4.25 = 235 s/km.
- With HRmax unknown: zone5Floor = 160 + R(9.0) = 169.

### 14.6 When a new estimate or retest is due

The rule reads the `estimates` log of `zones.json`: every `zones estimate`, saved or not, appends a summary `{date, lt1_hr, lt2_hr, lt2_pace, methods, confidence, partial}` (plan 5); `partial` is true when the call cap deferred work, and the rule skips partial entries.
Run the history estimate (`zones estimate`) when any of these holds (pace heuristic built on section 12):
- 28 days or more since the last entry in `estimates` (or none yet);
- a race (list-row `eventType` race) or a run with list-row `aerobicTrainingEffect` >= 4.0 since the last estimate (new best-effort data; list rows carry no RPE, controller ruling);
- 14 or more days without runs, then 2 weeks of training after the break (detraining).
A field test is due per threshold (LT1 and LT2 separately, each against its own current record) when any of these holds:
- the current record for that threshold is a history estimate (confidence low) and no field test exists for that threshold (for LT1, F1 or F3; for LT2, F2 or a `race_tt` race effort); a current `race_cs` record counts as an estimate here;
- the latest field test for that threshold is 84 days (12 weeks) old or older; a `race_tt` record counts as an LT2 field test, a `race_cs` record does not reset this clock (it is an estimate from a short effort; user decision 2026-11-18);
- the last two entries in `estimates` both differ from the current record for that threshold by >= 4 bpm in HR (above the 3-4 bpm daily noise), or, for LT2, by >= 3% in pace.
A lab record stays current for its thresholds until another is accepted, and is flagged `expired` per threshold after 183 days (plan 5).
Worked example (unit test): today 2026-11-17, current LT2 record field_test dated 2026-08-01 -> 108 days >= 84 -> LT2 "retest due"; current LT2 HR 160, last two estimates 165 and 164 -> both differ by >= 4 -> LT2 "retest due"; estimates 163 and 165 -> the first differs by 3 only -> not due on that rule; the LT1 result is computed separately from the current LT1 record.

### 14.7 Choosing which runs to examine and the call budget (hard cap `--max-calls`, default 40)

`--weeks N` (default 8) sets the history window; the extension doubles it to 2N weeks; the drift window stays 12 weeks.
One list read covers the last max(2N, 12) weeks (1 call; the history, drift and extension windows are cut from it, controller ruling).
Pre-filter from list rows only (no extra calls): activity type running (not treadmill, not trail), within the last N weeks, duration >= 20 min, average HR present, elevation gain <= 15 m per km.
Selection runs in two phases, because the drift picks need a reference HR that may come from H3, which needs the history details first.
Phase 1, history picks (8-week candidates):
- up to 6 "effort" runs for H1, ranked by list rows only: races (`eventType` race) first, then list-row `maxHR` (highest first), then `averageSpeed` (fastest first); RPE is not used for ranking (it is only in `get_activity`);
- up to 6 "steady" runs for H2, 30-90 min long, among the candidates not picked as effort runs: for p in 10, 30, 50, 70, 90, 100, the target is the nearest-rank percentile of `averageSpeed` (the value at rank ceil(p / 100 x n) in ascending order of the n candidates), and the pick is the not yet picked candidate whose `averageSpeed` is nearest the target; effort runs also contribute steady segments if any pass the filters.
Phase 2, drift picks for H5 (12-week list rows: running, not treadmill, not trail, 60-150 min, average HR present, elevation gain <= 15 m per km, the previous-day rule of section 7; window and gain limit are a user decision 2026-11-18, section 7):
- ref = the HR of the current LT1 record if one exists, else this estimate's H3 LT1 HR, else none;
- with a ref, up to 6 picks alternating, starting below: the not yet picked run with the nearest `averageHR` below ref, then the nearest at or above ref; when one side runs out, continue on the other side;
- without a ref, the nearest-rank percentiles of `averageHR` as for the steady picks.
Ties in every ranking and nearest match: newest first, then lowest activity id.
With fewer candidates than slots every candidate is picked once; a run is never picked twice within a group, and a run picked in more than one group is fetched once.
Calls per picked run: one `get_activity` (sensors for the strap check, RPE for the notes), then one `get_activity_details` (with `maxchart`, section 1).
A wrist run picked only as a steady or drift run is not fetched in detail unless `--include-wrist` (it could not be used): its `get_activity` call is counted, and it is replaced by the next candidate of the same ranking; effort runs are always fetched, since H1 uses speed only.
Other calls: the Garmin personal-records call (1 call) for H1's optional extra points; the sleep-stats read for the resting HR (section 1; 1 or 2 calls, because its 28-day chunks are fixed on the calendar and the last 28 days usually span two of them).
If H1 has fewer than 3 valid durations or H2 fails its requirements, extend once to 2N weeks: phase 1 runs again on the candidates of weeks N+1..2N not yet picked, with the same per-group caps; if it still fails, that method reports status `insufficient_data` with the runs used and skipped, and the matching field test is suggested.
FIT downloads for H4 (one call each, about 200 KB zipped), at most 6 per estimate including the extension, and none while H4 is marked unreliable for the athlete (section 6):
- candidates: strap runs already fetched in detail, dated on or after the first date with RR data (2026-09-01 until `pace` has seen an earlier one);
- span score = min(minutes of qualifying 2-min windows (section 6, without the RR rules) with HR in [ref - 15, ref), minutes with HR in [ref, ref + 15)); without a ref, the minutes with HR spread over the run's middle 80% of HR;
- download in order of span score (ties: newest first, then lowest id), only runs with a span score >= 5 min, skipping runs whose derived FIT data is already cached (a cached run costs no call; FIT files do not change, so the derived data is kept);
- a downloaded file without `hrv` messages, or whose RR data fail the section 6 sanity check, is recorded as "no RR" so it is never downloaded again for H4.
Budget: `--max-calls N` (default 40) is a hard cap on all Garmin calls of one `zones estimate`, the extension included.
Reads are made in this order: list read, personal records, sleep-stats, phase 1 picks in rank order, extension picks, phase 2 picks, FIT downloads by span score.
Every read checks the cache first; a cache hit costs no call and is served even when the cap is reached; a read that needs a call once the cap is reached is not made and is listed as `deferred`; the methods run on what was read, and the estimate is logged with `partial: true`.
Garmin reads already cached cost no call, so a rerun continues where the capped run stopped: zones reads of `get_activity` and `get_activity_details` for runs that started more than 1 day ago are cached for 60 days (under their own cache keys), and the derived FIT data is kept with no expiry.
The result reports `calls` (calls made) and `estimated_seconds`, the minimum time the rate limiter imposes on that many calls with the `pace.ratelimit` limits (2 s minimum interval, 10 calls per minute, 100 per hour), assuming no earlier calls in the process: for c calls and k = c - 1, 3600 x floor(k / 100) + 60 x floor((k mod 100) / 10) + 2 x (k mod 10), and 0 for c = 0; response times come on top, so a cold estimate takes up to about 4 minutes.
Worked example (unit test, today 2026-11-17, all picked runs strap, nothing cached):
- 1 list read; 40 list rows in 8 weeks, of which 3 treadmill, 2 trail, 5 under 20 min, 1 without HR -> 29 candidates; 2 races and the 4 non-race runs with the highest `maxHR` fill the 6 effort slots; 6 steady runs chosen by speed percentile -> 12 runs x 2 calls = 24 calls.
- 1 personal-records call; the sleep-stats read for 2026-10-21..2026-11-17 spans two fixed chunks -> 2 calls.
- H3 gives ref = 142; 60 list rows in 12 weeks give 14 drift candidates; 6 picked around 142 (3 below, 3 above), 2 of them already among the steady runs -> 4 new runs x 2 calls = 8 calls.
- 13 detailed strap runs date from 2026-09-01 or later; 5 have a span score >= 5 min; 1 of them is cached -> 4 FIT downloads.
- Total Garmin calls = 1 + 24 + 1 + 2 + 8 + 4 = 40 <= 40 (the default cap is reached exactly, nothing deferred); estimated_seconds = 60 x 3 + 2 x 9 = 198.
- The same estimate with `--max-calls 30`: the calls stop after the list read, the PR and sleep-stats calls, the 12 phase 1 runs (28 calls) and the `get_activity` and `get_activity_details` of the first of the 4 new drift runs (30 calls); the other 3 new drift runs and the 4 FIT downloads are listed as `deferred` and the log entry is `partial: true`; a rerun with the same cap finds the 30 reads cached (served at no cost) and makes the remaining 10 calls (the list read is cached for 600 s and the sleep-stats chunk ending today for 1 h, so a later rerun may add those back).

### 14.8 LT1 field test: DFA alpha1 ramp (F3)

The field tests of the chosen set are F1 and F3 for LT1 and F2 for LT2; the coach's default LT1 suggestion is F3 followed by one F1 at the ramp's LT1 HR (section 9a), or F1 alone while H4 is marked unreliable for the athlete (section 6).
Workout (`zones test-workout ramp`): 10 min easy + lap; 8 steps of 4 min, each followed by a lap; 10 min cool-down.
Step k (k = 1..8) aims at HR_k = L - 21 + 6 x (k - 1), where L is the current LT1 HR, else the `lt1_hr` of the newest `estimates` entry (the same fallback as 14.3).
Targets: with the current LT1 record and an H2 chosen as in 14.5 (the LT1 record's, else the LT2 record's), each step carries a pace target R(1000 / v(HR_k)) +/- 5 s/km if v(HR_k) is inside the fitted range by the 14.5 rule (0.9 x v_min <= v <= 1.1 x v_max); otherwise, and always when L comes from an estimate, an HR target HR_k +/- 3 bpm and the note "hold the pace steady within the step" (controller ruling).
With no LT1 record and no estimate, the steps have no targets and RPE notes rising from 2 to 6 of 10 over the 8 steps (RPE 2, 2-3, 3, 3-4, 4, 4-5, 5, 6).
Example: L = 142 -> step HR 121, 127, 133, 139, 145, 151, 157, 163.
Analysis and confidence: section 9a; `zones analyze --test ramp` always records the gate outcome in the H4 memory (section 6), and with `--save` it also stores a record when the ramp is bracketed (refusals of section 9a are not saved), with the section 9a confidence.
Worked example (unit test, alpha1 injected per step, artifact share 1% and cadence 172 steps/min on every step):
- Steps (alpha1, HR, speed m/s): (1.00, 130, 2.80), (0.90, 136, 3.04), (0.80, 142, 3.28), (0.70, 148, 3.52), (0.60, 154, 3.76), (0.50, 160, 4.00).
- Gate: no coupling drop (160 vs 172 is 7.0% apart), rho = -1, 3 steps on each side -> usable.
- Fits: HR = 190 - 60 x alpha1, v = 5.20 - 2.40 x alpha1; LT1 HR = R(145.0) = 145; LT1 pace = R(1000 / 3.40) = R(294.1) = 294 s/km; 3 steps on each side, median artifact share 1% -> medium.
- The step at alpha1 0.70 with artifact share 6% -> dropped; 3 steps above and 2 below -> "alpha1_unusable", reason "one-sided" (fewer than 3 steps below 0.75), nothing saved.
- Only the first three steps (all alpha1 > 0.75) -> "alpha1_unusable", reason "one-sided".
- Step interference (unit test): the worked-example steps with 2 ms white RR noise, and a 20 ms sine at the step frequency (abs(172 - 148) / 60 = 0.4 Hz) on the step at alpha1 0.70 -> that step is dropped with reason "step interference" and its share in the row (`dropped_step` 1); the other steps keep shares below 0.25; 2 steps below 0.75 -> "alpha1_unusable", "one-sided".
- Gate test with the pattern observed on this athlete (unit test): the 13 (HR, alpha1) steps of section 6 with cadences 166, 168, 169, 170, 171, 172, 174, 176, 178, 180, 182, 185, 188 -> the steps at 176 bpm (4.9% from 185) and 180 bpm (4.3% from 188) are dropped for coupling (count 2 reported); over the 11 kept steps rho = +0.49 and only 2 steps lie above 0.75 -> "alpha1_unusable", reasons "not monotonic, rho = 0.49" and "one-sided"; the H4 streak rises by 1.

### 14.9 Implementation rulings (controller, 2026-11-18)

- Ascent is computed with a 2 m hysteresis (a climb counts once the rise since the last turning point exceeds `ASCENT_HYSTERESIS_M` = 2.0 m) in one shared helper, because 1 s altitude jitter (sigma 0.1-0.3 m) otherwise reads as 19-57 m/km on flat ground; all rule numbers live in `pace/rules.py`.
- The 16-week extension also runs when the H1 fit is rejected (D' outside 50..500 m or CS not below the 720 s speed), not only when fewer than 3 durations are valid.
- `insufficient_data` outcomes do not enter the H4 `counted` list; `--retry-h4` clears the streak and `counted`.
- F3 drops laps that look automatic (all laps <= 1100 m or <= 480 s) and is refused with fewer than 3 steps; F2 without laps accepts short stops within the stop rule and reports them.
- `derive` reports `floor_errors`; `zones push` (Task 5c) refuses while it is non-empty.
- Observed on the athlete's data after these fixes (read-only run): H2 fits 33 segments from 11 runs (HR = 22.1 + 37.39 v, SEE 4.0 bpm); H1 is rejected (best 3/7/12-min windows 4.15/4.00/3.97 m/s are not maximal); H5 finds only 3 candidate runs in the 60-120 min list window and none with a 45-min steady segment; H4 finds no run passing the gate.
  Pending user decision: whether to relax the H5 run window or the flat limit; until then LT1 needs F1 and LT2 needs F2.
- Decided 2026-11-18 (user decision): the H5 run window becomes 60-150 min and the drift list-row pre-filter 15 m per km (sections 7 and 14.7); the flat limits stay, but altitude is smoothed over 61 s before grade and ascent (section 1).
  Observed effect on the athlete's runs: on raw altitude 80-93% of the 5-min windows of flat runs failed the 10 m/km limit (13-18 m/km of apparent climb); with 61 s smoothing 1-11% failed, and hilly runs still failed 35-100%.
  The hysteresis ruling above was tuned on sub-metre jitter (sigma 0.1-0.3 m); the GPS jitter of metres seen on these runs needs the smoothing as well.
- Step-interference gate (section 6, pace heuristic): the step share uses the corrected beats of the window; segments are detrended one by one (as Welch with linear detrending), the Hann window is the periodic one, and the step band is clipped to the analysed range so the share never exceeds 1.
  A window or F3 step whose share is null keeps its place; `step_share` is reported on kept windows and on every F3 step row past the coupling check.
  The H4 per-run report re-filters given windows by coupling and then by step share, adding both drops to the counts from `hrv.windows`.
- The `alpha1_unreliable` flag is added whenever the same estimate marks alpha1 unreliable, also when H4 has no value, so the result always says why later estimates will skip H4.

## 15. Source log

Verified and cited (opened, supports the claim): 49.
Full text read: Smyth and Muniz-Pumares 2020; Smyth et al. 2022; Anderson et al. 2026; Lipkova et al. 2025; Gronwald et al. 2024; Nuuttila et al. 2022; Stoggl and Sperlich 2015; House 2026 (Uphill Athlete); Friel decoupling and Friel zones (TrainingPeaks); Hunter 2025; Rogers et al. 2020; Rogers et al. 2021a; Rogers et al. 2021b; Rogers and Gronwald 2022; Kanniainen et al. 2025; Hoos and Gronwald 2025; Andriolo et al. 2024; Nuuttila et al. 2025; Hunter et al. 2026.
Abstract only: Iannetta 2020; Jamnick 2020; Jones 2019; Galan-Rioja 2020; Rainsberger 2026; Hunter 2024; Hunter 2023; Muniz-Pumares 2025; Vesterinen 2014; Teso 2025; Malinen 2026; McGehee 2005; Seiler 2010; Nuuttila 2021; Londeree 1997; Peng 1995; Tarvainen 2002; Rogers 2021c; Rogers 2026; Van Hooren 2025; Van Hooren 2023; Kaufmann 2023; Cassirame 2025; Nieto-Jimenez 2026; Lu 2026; van Rassel 2025; Schaffarczyk 2022; Coyle and Gonzalez-Alonso 2001; Gonzalez-Alonso 1997.
Dropped (not opened, or did not support a claim made here): Muniz-Pumares et al. 2019 (CP methodology, not opened); Jones and Vanhatalo 2017 (not opened); Poole et al. 2016 (not opened); Galbraith et al. 2014 (single-visit CS test, not opened); Jones and Carter 2000 (abstract gives no time course); Gronwald et al. 2021 (single-case real-time DFA alpha1 in a triathlete, opened, adds nothing used here); Coyle 1998 (cardiovascular drift and dehydration, opened, covered by Coyle and Gonzalez-Alonso 2001 and Gonzalez-Alonso et al. 1997); Chen et al. 2002 (cited by Rogers and Gronwald 2022 as the basis for 2-min windows, not opened); Lipponen and Tarvainen 2019 (the Kubios automatic artifact algorithm, not opened, so `pace` does not claim to reproduce it); Sempere-Ruiz et al. 2026 (DFA alpha1 in constant-load cycling, not opened); De Maeseneer et al. 2026 (DFA alpha1 window length, preprint, not opened); Barsumyan et al. 2026 (decoupling in cycling, not opened).
Not a source: the alpha1 values for synthetic noise in section 14.2 come from a pace simulation of the section 6 algorithm, and are test expectations only.
