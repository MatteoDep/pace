# Threshold and zone methods (research notes for coach plan 5)

Date: 2026-11-17.
Scope: how `pace` estimates LT1 and LT2 (HR and pace) for one runner, analyzes the two field tests, accepts lab values, and derives three zones and five Garmin HR zone floors.
This file is the reference for `pace/thresholds.py`, `pace/zones.py` and the "Thresholds and zones" section of the coaching skill.

## 0. Terms, athlete constraints and evidence grades

LT1 is the first threshold: the boundary between the moderate and heavy intensity domains (also called aerobic threshold, VT1, GET, T1).
LT2 is the second threshold: the boundary between the heavy and severe domains; in running the best supported marker is critical speed (CS), with MLSS, VT2/RCP and 4 mmol/L as related but not identical markers.
CS is the asymptote of the speed-duration relation; D' (metres) is the distance that can be covered above CS.
Athlete: Garmin Forerunner 55, chest strap on almost all runs, no RR intervals (so DFA alpha1 and any HRV threshold method is out), no Garmin lactate threshold detection, no running power.
Data per run: per-sample HR, speed, distance, elevation, cadence at about 1-5 s; laps; summary; Garmin personal records; RPE and feel; daily resting HR.
Max HR is known only from data.

Evidence grades used below:
- strong: consistent findings in systematic reviews or meta-analyses, or several independent validation studies.
- moderate: one or two peer-reviewed validation studies, or a review whose underlying studies are small or heterogeneous.
- weak/expert opinion: practitioner protocols, or peer-reviewed evidence that only indirectly supports the use made here.

Rules marked "pace heuristic" are engineering choices made for this tool; no source supports their exact numbers.

## 1. Common data rules (apply to every method)

Strap only: an activity is used only if its HR came from a chest strap (plan 5 sensor check); wrist runs need `--include-wrist` and lower the confidence to low.
Outdoor only for speed: treadmill runs on the Forerunner 55 get speed from the wrist accelerometer, so they are excluded from every speed or pace calculation; a treadmill drift test is analyzed on HR only (section 5).
Resampling: convert samples to a 1 s grid by linear interpolation of cumulative distance and HR, but only inside gaps of at most 5 s.
Stops and auto-pause: a timestamp gap of more than 5 s, or a stretch of more than 10 s with speed below 1.0 m/s, is a stop; no analysis window may contain a stop.
Speed from distance: window speed is (distance at window end minus distance at window start) / window seconds; never average the instantaneous GPS speed (pace heuristic, GPS speed samples are noisy).
Warm-up: the first 600 s of every run are excluded from the history methods; field tests use the lap the user pressed or, without it, the protocol warm-up time.
Grade: a window is "flat" if abs(elevation end minus elevation start) / window distance <= 0.02 and total ascent inside it <= 10 m per km (pace heuristic; Forerunner 55 elevation is GPS-based and noisy, so only coarse limits make sense).
Walking: windows with mean cadence below 140 steps/min are excluded (pace heuristic).
HR artefacts: HR samples outside 30..230 are dropped; the first 300 s of a run are ignored for max HR because dry-strap spikes are common there (pace heuristic).
Max HR (HRmax): highest 5-s rolling median HR across strap runs in the last 365 days, excluding the first 300 s of each run.
Using the highest HR recorded in training as HRmax follows Smyth et al. 2022, who found it higher and more plausible than the age-predicted value (187 +/- 8 vs 178 +/- 18 bpm).

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

Protocol: no test; `pace` scans the examined runs (section 10) for the fastest flat, stop-free rolling windows of fixed duration.
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
- HR keeps drifting during constant runs even in the moderate domain (section 5), which is why segments after 60 min are excluded.
- Heat, dehydration and fatigue raise HR at a given speed (Hunter et al. 2025 list environment and nutrition as factors to control); the coach should prefer runs in normal conditions.
Evidence grade: moderate for the HR-speed relation as a monitoring tool; weak/expert opinion for reading threshold HR off it (no study validates LT HR from this regression).

Sources:
- Vesterinen V, Hokka L, Hynynen E, Mikkola J, Hakkinen K, Nummela A. 2014. Heart rate-running speed index may be an efficient method of monitoring endurance training adaptation. J Strength Cond Res 28(4):902-908. https://doi.org/10.1519/JSC.0000000000000349 (abstract)
- Nuuttila OP, Nummela A, Korhonen E, Hakkinen K, Kyrolainen H. 2022. Individualized Endurance Training Based on Recovery and Training Status in Recreational Runners. Med Sci Sports Exerc 54(10):1690-1701. https://doi.org/10.1249/MSS.0000000000002968 (full text)
- Teso M, Colosio AL, Loi M, Boone J, Pogliaghi S. 2025. Predicting Heart Rate Slow Component Dynamics: A Model Across Exercise Intensities, Age, and Sex. Sports 13(2):45. https://doi.org/10.3390/sports13020045 (abstract)
- Hunter B, Maunder E, Jones AM, Gallo G, Muniz-Pumares D. 2025. Durability as an index of endurance exercise performance: Methodological considerations. Exp Physiol 110(11):1612-1624. https://doi.org/10.1113/EP092120 (abstract)

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

## 6. Method F1: HR drift / aerobic decoupling test (LT1 field test)

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
Inputs: per-sample HR, distance, time, elevation; laps.
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
- Hunter B et al. 2025. (as in section 4) (abstract)

## 7. Method F2: 30-minute solo time trial (LT2 field test)

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
Inputs: per-sample HR, distance, time, elevation; laps; RPE (expected 9-10).
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

## 8. Lab tests (source "lab")

Protocol: the user enters LT1 and LT2 HR and/or pace from a lab report with `zones add --source lab`; the record becomes current (plan 5).
Store `method` as the lab's own criterion when known (for example "VT1/VT2", "LT1 2 mmol/L / OBLA 4 mmol/L", "D-max").
Do not convert lab values; note that lab lactate LT2 is usually slower than CS (Rainsberger et al. 2026; Galan-Rioja et al. 2020), so a later history estimate with a faster LT2 pace is not by itself evidence of improvement.
Lab treadmill speeds are often at 0-1% grade and treadmill speed differs from outdoor GPS pace; HR values transfer better than pace (pace heuristic).
Evidence grade: strong for the lab measurement itself; the cross-method offsets in section 2 are moderate.

## 9. Zone models

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
- Friel J. 2012, updated 2026. (as in section 7) (full text)
- House S. 2026. (as in section 6) (full text)

## 10. How often thresholds change (retest interval)

Recreational runners improved maximal treadmill speed by 2-3% over a 10-week period of increased intensity or volume (Nuuttila et al. 2021), and 10-km time by 3-6% over a 15-week program (Nuuttila et al. 2022).
Thresholds rise strongly in previously sedentary people and less in already trained ones; detraining lowers them (Londeree 1997, meta-analysis of 34 studies).
Day-to-day HR at a given speed varies by about 3-4 bpm (Nuuttila et al. 2022), so a smaller change in a threshold HR is noise.
Uphill Athlete advises repeating the drift test every 4-6 months or sooner after a clear fitness change (House 2026); Friel advises testing early in the base and build periods.
Evidence grade: moderate that meaningful changes take about 6-12 weeks in recreational runners; weak/expert opinion for any exact interval.

Sources:
- Nuuttila OP, Nummela A, Hakkinen K, Seipajarvi S, Kyrolainen H. 2021. Monitoring Training and Recovery during a Period of Increased Intensity or Volume in Recreational Endurance Athletes. Int J Environ Res Public Health 18(5):2401. https://doi.org/10.3390/ijerph18052401 (abstract)
- Nuuttila OP et al. 2022. (as in section 4) (full text)
- Londeree BR. 1997. Effect of training on lactate/ventilatory thresholds: a meta-analysis. Med Sci Sports Exerc 29(6):837-843. https://doi.org/10.1097/00005768-199706000-00016 (abstract)
- House S. 2026. (as in section 6) (full text)
- Friel J. 2012, updated 2026. (as in section 7) (full text)

## 11. Rejected methods

%HRmax or %HRR rules (for example LT1 = 75% HRmax): rejected; LT ranged 60-90% HRmax across individuals (Iannetta et al. 2020; Jamnick et al. 2020).
`pace` never fills a threshold from %HRmax; with no usable data it reports `insufficient_data` and suggests a field test.
DFA alpha1 HRV thresholds: rejected; the Forerunner 55 records no RR intervals.
Conconi HR deflection: not studied further; McGehee et al. 2005 found the 30-min time trial better among four field tests.
Garmin lactate threshold detection: not available on the Forerunner 55.

## 12. Chosen set

### 12.1 LT2 from history: H1 + H2

LT2 pace = 1000 / CS, with CS from the linear distance-time fit of the best 180 s, 420 s and 720 s flat stop-free windows in the examined runs of the last 8 weeks.
LT2 HR = HR-speed regression (H2) evaluated at CS, only if the fastest steady segment reaches 0.90 x CS; otherwise LT2 HR is null and the coach suggests the 30-min test.
Confidence: medium or low by the H1 gates; low if H2 SEE > 5 bpm.
Worked example (unit test):
- Best windows: 180 s -> 900 m, 420 s -> 1860 m, 720 s -> 3060 m.
- Fit: CS = 4.000 m/s, D' = 180 m, LT2 pace = 250 s/km (4:10/km).
- Steady segments (speed m/s, HR): (2.8, 130), (3.0, 135), (3.2, 140), (3.4, 145), (3.6, 150), (3.8, 155), each pair repeated twice from 3 runs (n = 12).
- Fit: a = 60, b = 25, SEE = 0.
- Max steady speed 3.8 >= 0.9 x 4.0 = 3.6, so LT2 HR = 60 + 25 x 4.0 = 160 bpm.

### 12.2 LT1 from history: H3 + H2

LT1 speed = 0.823 x CS; LT1 pace = 1000 / LT1 speed; LT1 HR = HR-speed regression at LT1 speed.
Confidence: always low (individual scatter of the 82.3% fraction is unknown).
Worked example (same data as 12.1):
- LT1 speed = 0.823 x 4.0 = 3.292 m/s; LT1 pace = 303.77 -> 304 s/km (5:04/km).
- LT1 HR = 60 + 25 x 3.292 = 142.3 -> 142 bpm.

### 12.3 LT1 field test: HR drift test (F1)

Workout: 15 min warm-up building to target (last 3 min at target) + lap; 60 min steady at a constant pace that starts at the current LT1 HR, flat route or treadmill, no stops + lap; 10 min cool-down.
Intensity guidance: conversational, full sentences possible; hold pace, let HR drift; RPE about 3-4 of 10.
Analysis: drift % = (EF_1 / EF_2 - 1) x 100 outdoors, (HR_2 / HR_1 - 1) x 100 on a treadmill; band 3.5-5% sets LT1 HR = HR_1 and LT1 pace = 1000 / speed_1; below 3.5% retest +5 bpm; above 5% retest -5 bpm.
Confidence: medium when the verdict is "at LT1" with even pace, otherwise the record is not created (the verdict only steers the next test).
Worked examples (unit tests):
- Halves (speed m/s, HR): H1 (3.20, 140), H2 (3.18, 146): drift = (3.20/140) / (3.18/146) - 1 = 4.94% -> at LT1; LT1 HR 140, LT1 pace 1000/3.20 = 312.5 -> 313 s/km (5:13/km).
- H1 (3.10, 138), H2 (3.09, 141): drift = 2.50% -> below LT1, retest at start HR + 5.
- H1 (3.30, 146), H2 (3.26, 156): drift = 8.16% -> above LT1, retest at start HR - 5.
- Steady part of 35 min -> rejected (shorter than 40 min).

### 12.4 LT2 field test: 30-minute solo time trial (F2)

Workout: 15 min easy + 3 x 20 s strides + 3 min easy; lap; 30 min even, maximal sustainable effort, flat, alone; laps at 10 min and 30 min; 10 min cool-down.
Intensity guidance: race effort for 30 min, start controlled (first 5 min should feel slightly too easy), RPE 9-10 at the end.
Analysis: LT2 HR = mean HR from 600 s to 1800 s; LT2 pace = 1800 / distance_km; fade flag if second-half speed < 0.95 x first-half speed.
Confidence: medium; low if faded or if stops occurred.
Worked example (unit test):
- 30 min, distance 7200 m, mean HR 0-600 s = 158, mean HR 600-1800 s = 168; first 15 min 3600 m, second 15 min 3600 m.
- LT2 HR = 168 bpm; LT2 pace = 1800 / 7.2 = 250 s/km (4:10/km); speed ratio 1.00 -> no fade flag.

### 12.5 Zone mapping (rule name "lt-5z-v1")

Rounding: R(x) = floor(x + 0.5) on bpm and s/km.
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
```
Watch zones sum to the three-zone model as Z1+Z2 | Z3 | Z4+Z5.
Five pace floors (s/km): z1, z2 = 1000 / v(floor HR) from the H2 regression (null if no regression or outside the fitted speed range by more than 10%); z3 = LT1 pace; z4 = LT2 pace; z5 = 1000 / (CS + D'/720) when D' is known (speed for a 12-min maximal effort), else null.
Validation: floors must be strictly increasing and zone5Floor < HRmax; otherwise `bad_args` and no push.
The 0.80 and 0.90 factors and the zone 5 midpoint are conventions (weak/expert opinion); only zone3Floor and zone4Floor carry physiological meaning.
Worked example (unit test), LT1 = 142, LT2 = 160, HRmax = 186, H2 a = 60, b = 25, CS = 4.0, D' = 180:
- floors 114, 128, 142, 160, 173.
- three zones: Z1 < 142, Z2 142-159, Z3 >= 160.
- pace floors: z1 1000/2.16 = 463, z2 1000/2.72 = 368, z3 304, z4 250, z5 1000/4.25 = 235 s/km.
- With HRmax unknown: zone5Floor = 160 + R(9.0) = 169.

### 12.6 When a new estimate or retest is due

Run the history estimate (`zones estimate`) when any of these holds (pace heuristic built on section 10):
- 28 days or more since the last estimate;
- a new run with RPE >= 8 or a race since the last estimate (new best-effort data);
- 14 or more days without runs, then 2 weeks of training after the break (detraining).
A field test is due when any of these holds:
- the current record is a history estimate (confidence low) and no field test exists for that threshold;
- the latest field test for that threshold is 84 days (12 weeks) old or older;
- a new history estimate differs from the current record by >= 4 bpm in LT1 or LT2 HR (above the 3-4 bpm daily noise) or by >= 3% in LT2 pace, on two consecutive estimates.
A lab record stays current until another is accepted and is flagged `expired` after 183 days (plan 5).
Worked example (unit test): today 2026-11-17, current record field_test dated 2026-08-01 -> 108 days >= 84 -> "retest due"; current LT2 HR 160, last two estimates 165 and 164 -> both differ by >= 4 -> "retest due"; estimates 163 and 165 -> first differs by 3 only -> not due on that rule.

### 12.7 Choosing which runs to examine (about 12 detail calls)

Pre-filter from list rows only (no extra calls): activity type running (not treadmill, not trail), within the last 8 weeks, duration >= 20 min, average HR present, elevation gain <= 15 m per km.
Pick up to 6 "effort" runs, ranked by: races first, then RPE >= 7, then max HR / HRmax, then average speed; these feed H1.
Pick up to 6 "steady" runs, 30-90 min long, spread over average speed by taking the run nearest each of the 10th, 30th, 50th, 70th, 90th and 100th percentiles of average speed among the remaining candidates; these feed H2 (effort runs also contribute steady segments if any pass the filters).
Add the Garmin personal-records call (1 call) for H1's optional extra points.
If H1 has fewer than 3 valid durations or H2 fails its requirements, extend the window once to 16 weeks with the same caps; if it still fails, return `insufficient_data` with the runs used and skipped, and suggest the matching field test.
Worked example (unit test): 40 list rows in 8 weeks, of which 3 treadmill, 2 trail, 5 under 20 min, 1 without HR -> 29 candidates; 2 races and 4 runs with RPE >= 7 fill the 6 effort slots; 6 steady runs chosen by speed percentile; total detail calls = 12, plus 1 PR call.

## 13. Source log

Verified and cited (opened, supports the claim): 26.
Full text read: Smyth and Muniz-Pumares 2020; Smyth et al. 2022; Anderson et al. 2026; Lipkova et al. 2025; Gronwald et al. 2024; Nuuttila et al. 2022; Stoggl and Sperlich 2015; House 2026 (Uphill Athlete); Friel decoupling and Friel zones (TrainingPeaks).
Abstract only: Iannetta 2020; Jamnick 2020; Jones 2019; Galan-Rioja 2020; Rainsberger 2026; Hunter 2024; Hunter 2023; Muniz-Pumares 2025; Vesterinen 2014; Teso 2025; Hunter 2025; Malinen 2026; McGehee 2005; Seiler 2010; Nuuttila 2021; Londeree 1997.
Dropped (not opened, or did not support a claim made here): Muniz-Pumares et al. 2019 (CP methodology, not opened); Jones and Vanhatalo 2017 (not opened); Poole et al. 2016 (not opened); Galbraith et al. 2014 (single-visit CS test, not opened); Jones and Carter 2000 (abstract gives no time course); DFA alpha1 studies (method not usable on this device).
