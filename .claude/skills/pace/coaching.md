# Coaching principles

Read before proposing plan structure, a load change, or a test.
Each section: the principle, what it means for this athlete, the evidence grade, and the sources that were opened and checked when this file was written.
Grades: strong (consistent systematic reviews or several independent studies), moderate (one or two studies, or a review of small or mixed studies), weak/expert opinion (practitioner guidance or indirect evidence).
"(abstract)" means only the abstract was read, "(full text)" the paper; cite a source only for what is written next to it here.
A principle with "Sources: none verified" is stated without a citation.

## Progressive overload and gradual volume increase

Fitness comes from load the body has to adapt to, so load rises over weeks, with easy weeks between.
The "10 % rule" (never add more than 10 % per week) is a convention, not a tested threshold: a graded 13-week program built on it did not reduce injuries in novice runners compared with an 8-week program.
A large jump may still matter: in novice runners, increasing weekly distance by more than 30 % over two weeks went with more distance-related injuries than less than 10 % (exploratory, not significant).
For this athlete: raise weekly volume gradually and avoid big jumps (more than about 30 %) after a lighter period; judge from their own history (`pace load`, feedback, injuries) rather than a fixed percentage.
Evidence: weak/expert opinion for any specific rate; the 10 % rule itself was not supported in one RCT.
Sources:
- Buist I, Bredeweg SW, van Mechelen W, Lemmink KA, Pepping GJ, Diercks RL. 2008. No effect of a graded training program on the number of running-related injuries in novice runners: a randomized controlled trial. Am J Sports Med 36(1):33-39. https://doi.org/10.1177/0363546507307505 (abstract)
- Nielsen RO, Parner ET, Nohr EA, Sorensen H, Lind M, Rasmussen S. 2014. Excessive progression in weekly running distance and risk of running-related injuries: an association which varies according to type of injury. J Orthop Sports Phys Ther 44(10):739-747. https://doi.org/10.2519/jospt.2014.5164 (abstract)

## Intensity distribution

Most endurance volume is easy (below LT1), with a smaller share of hard work; competitive endurance athletes converge on about 80 % of sessions at low intensity.
In a 9-week study of well-trained athletes, a polarized program (mostly easy, some very hard, little in between) improved VO2peak and time to exhaustion more than threshold-heavy, high-intensity-only or high-volume-only programs.
In 151,813 marathons, faster runners did more volume, mostly in zone 1, and most used a pyramidal distribution (easy > moderate > hard).
For this athlete: keep most running below LT1 (check with `pace chart intensity` and `hr_zones_s`); polarized and pyramidal are both reasonable, let the user choose; the common error is running easy days too hard.
Evidence: moderate (descriptive studies and small trials; the polarized vs pyramidal question is open).
Sources:
- Seiler S. 2010. What is best practice for training intensity and duration distribution in endurance athletes? Int J Sports Physiol Perform 5(3):276-291. https://doi.org/10.1123/ijspp.5.3.276 (abstract)
- Stoggl T, Sperlich B. 2014. Polarized training has greater impact on key endurance variables than threshold, high intensity, or high volume training. Front Physiol 5:33. https://doi.org/10.3389/fphys.2014.00033 (abstract)
- Muniz-Pumares D, Hunter B, Meyler S, Maunder E, Smyth B. 2025. The Training Intensity Distribution of Marathon Runners Across Performance Levels. Sports Med 55(4):1023-1035. https://doi.org/10.1007/s40279-024-02137-7 (abstract)

## Specificity toward the event

As the event nears, training should look more like it: race pace, terrain (D+ for a hilly or trail race), duration and fuelling for long events.
For this athlete: read `event.name` and `event.date` from the diary; in the last blocks before the event include sessions at goal pace and on similar terrain, and long runs that rehearse race-day fuelling.
Evidence: weak/expert opinion (coaching consensus; no source opened for this file).
Sources: none verified

## Periodization and recovery weeks

Plans are usually organised in blocks (base, build, peak, taper, recovery) with lighter weeks every few weeks.
The classic periodization models rest partly on assumptions that are no longer scientifically justified, and responses to training vary widely between people, so a plan should be adjusted to the athlete's responses rather than follow a fixed template.
For this athlete: use blocks (`pace plan block add`) and a lighter week when feedback, sleep, resting HR or RPE say so, or every 3-4 weeks by default; treat the cycle length as a starting point, not a rule.
Evidence: weak/expert opinion for any specific block or recovery-week pattern.
Sources:
- Kiely J. 2012. Periodization paradigms in the 21st century: evidence-led or tradition-driven? Int J Sports Physiol Perform 7(3):242-250. https://doi.org/10.1123/ijspp.7.3.242 (abstract)

## Taper

Before a key race, cut volume and keep intensity and frequency.
A meta-analysis of 27 studies found the best effect for a taper of about 2 weeks with volume reduced by 41-60 % (exponentially), without reducing intensity or frequency.
For this athlete: for a goal race, plan about 2 weeks of reduced volume (40-60 % less by race week), keep short sessions at race intensity, and keep the usual number of runs.
Evidence: strong (meta-analysis of competitive athletes; the exact numbers are group averages).
Sources:
- Bosquet L, Montpetit J, Arvisais D, Mujika I. 2007. Effects of tapering on performance: a meta-analysis. Med Sci Sports Exerc 39(8):1358-1365. https://doi.org/10.1249/mss.0b013e31806010e0 (abstract)

## Long-run progression

The long run builds durability for longer events; it grows gradually, mostly at easy intensity, and is shortened in recovery weeks.
For this athlete: grow the long run step by step toward what the event needs, keep it below LT1 unless a race-specific segment is planned, and watch HR drift and feedback after it.
Evidence: weak/expert opinion (no source opened for this file).
Sources: none verified

## Strength training for runners

Adding strength work (heavy, explosive or plyometric) to running usually improves running economy (by 2-8 % in most of the 20 studies of 24 that measured it) and time-trial performance without hurting body composition; the review suggests 2-3 sessions per week.
Across sports, strength training reduced sports injuries in a meta-analysis of randomised trials (risk ratio about 0.3), while stretching did not.
For this athlete: read `strength` from the profile; keep or add 2 strength sessions per week as text sessions (`sport` strength), lighter in race weeks and not just before key runs.
Evidence: moderate (systematic review of small trials for economy; injury evidence is from mixed sports, not runners only).
Sources:
- Blagrove RC, Howatson G, Hayes PR. 2018. Effects of Strength Training on the Physiological Determinants of Middle- and Long-Distance Running Performance: A Systematic Review. Sports Med 48(5):1117-1149. https://doi.org/10.1007/s40279-017-0835-7 (abstract)
- Lauersen JB, Bertelsen DM, Andersen LB. 2014. The effectiveness of exercise interventions to prevent sports injuries: a systematic review and meta-analysis of randomised controlled trials. Br J Sports Med 48(11):871-877. https://doi.org/10.1136/bjsports-2013-092538 (abstract)

## Sleep and recovery markers

The few studies in athletes report lower sport-specific performance after sleep loss, cognitive performance suffers, and poor sleep may mimic overtraining symptoms; findings on maximal physical efforts are mixed.
Adjusting training load to recovery markers (nocturnal HRV, perceived recovery, HR at a given running speed) gave larger 10-km improvements than a fixed plan in recreational runners.
Markers are context, not verdicts: one bad night or one high resting HR is noise; a trend over several days, together with feedback, is a signal.
For this athlete: the Forerunner 55 records no HRV and no sleep score, so use sleep duration and stages, resting HR trend (`pace chart recovery`, `pace wellness sleep-range`), RPE and feel; when several point the same way, propose an easier day.
Evidence: moderate (narrative review for sleep; one controlled study for load adjustment, using HRV this athlete does not have).
Sources:
- Fullagar HH, Skorski S, Duffield R, Hammes D, Coutts AJ, Meyer T. 2015. Sleep and athletic performance: the effects of sleep loss on exercise performance, and physiological and cognitive responses to exercise. Sports Med 45(2):161-186. https://doi.org/10.1007/s40279-014-0260-0 (abstract)
- Nuuttila OP, Nummela A, Korhonen E, Hakkinen K, Kyrolainen H. 2022. Individualized Endurance Training Based on Recovery and Training Status in Recreational Runners. Med Sci Sports Exerc 54(10):1690-1701. https://doi.org/10.1249/MSS.0000000000002968 (full text)

## Low energy availability and REDs

Low energy availability (intake too low for the energy training costs) harms health and performance in both women and men (Relative Energy Deficiency in Sport, REDs).
For this athlete: never set or push weight-loss targets during heavy training blocks; give only general guidance on eating enough to fuel training; signs such as persistent fatigue, falling performance, frequent illness or injury, or (in women) menstrual changes are a reason to suggest seeing a professional.
Evidence: strong for the harm of low energy availability (IOC consensus over a large literature); weak/expert opinion for the coaching rule of no weight-loss targets in heavy blocks.
Sources:
- Mountjoy M, Ackerman KE, Bailey DM, Burke LM, Constantini N, Hackney AC, Heikura IA, Melin A, et al. 2023. 2023 International Olympic Committee's (IOC) consensus statement on Relative Energy Deficiency in Sport (REDs). Br J Sports Med 57(17):1073-1097. https://doi.org/10.1136/bjsports-2023-106994 (abstract)

## Load ratios (ACWR) and their critiques

The acute:chronic workload ratio (for example last 7 days over the last 28) is popular for injury risk, but no study has estimated a causal effect, the ratio is statistically flawed and not consistently related to injury, so it should not drive training decisions.
For this athlete: show the rolling 7-day load (`pace load`, `pace chart volume`) as a description of what changed, not as a risk score; never say a ratio puts them at risk.
Evidence: moderate (methodological critique; no study supports ACWR for load management).
Sources:
- Impellizzeri FM, Tenan MS, Kempton T, Novak A, Coutts AJ. 2020. Acute:Chronic Workload Ratio: Conceptual Issues and Fundamental Pitfalls. Int J Sports Physiol Perform 15(6):907-913. https://doi.org/10.1123/ijspp.2019-0864 (abstract)

## Thresholds and zones

Summary of `docs/superpowers/specs/2026-11-17-zones-methods.md`, the normative source for formulas, gates and the full source log.
pace does all the arithmetic; the coach explains it.

### What the thresholds are

LT1 is the boundary between moderate and heavy intensity (aerobic threshold, about 2 mmol/L, VT1); LT2 is the boundary between heavy and severe (best marked by critical speed, CS, in running).
Fixed percentages of max HR are poor anchors: LT occurred anywhere between 60 % and 90 % of HRmax across 100 adults, so pace never sets a threshold from %HRmax.
Methods are not interchangeable: CS sits above lactate markers (MLSS, 4 mmol/L), so a lab LT2 is usually slower than an LT2 from history; compare records only with the same `method`.
On average LT1 falls at about 82 % of CS (group mean; individual scatter is unknown).
Evidence: strong that %HRmax is unreliable and that CS marks heavy-severe; moderate for the 82 % fraction.

### How pace estimates them (`pace zones estimate`)

- LT2 pace from critical speed (H1): the best 3, 7 and 12-minute flat efforts in 8 weeks of training; CS from training agreed with CS from time trials in recreational runners; confidence never high (efforts are not proven maximal).
- LT2 and LT1 HR from the HR-speed line of steady segments (H2); the HR-speed relation is a validated monitoring tool, reading threshold HR off it is not validated.
- LT1 from HR drift in normal long steady runs (H5): low drift (5 % or less) says LT1 is above that run's HR, high drift says below; weak, but primary for this athlete.
- LT1 from DFA alpha1 crossing 0.75 in strap runs (H4): agrees with VT1 at group level on lab ramps (limits about +/-11 bpm at best); only runs that pass a monotonicity gate count.
- LT1 as 82.3 % of CS (H3): fallback only, always low confidence.
Strap runs only (wrist runs only with `--include-wrist`, confidence low); treadmill runs give no pace.
Evidence: moderate for CS from training data and for alpha1 at group level; weak/expert opinion for drift bounds and for this implementation.

### Field tests (`pace zones test-workout`, then `pace zones analyze`)

- F1 drift test for LT1: 15 min warm-up building to the target, 60 min (at least 40) at constant pace on flat ground, no stops; drift 3.5-5 % means "at LT1" (LT1 HR = first-half HR), below 3.5 % retest 5 bpm higher, above 5 % retest 5 bpm lower; avoid heat, a fasted state and the day after a hard session; read as +/-5 bpm.
- F2 30-minute solo time trial for LT2: LT2 pace from the whole 30 min, LT2 HR from the last 20 min; one validation study (SEE 8 bpm for HR); starting too fast flags the result.
- F3 DFA alpha1 ramp for LT1: 8 steps of 4 min, each about 6 bpm higher; usable only when alpha1 falls with HR.
Evidence: weak/expert opinion for F1; moderate for F2 pace, weak for F2 HR; moderate at group level and weak for an individual for F3.

### What was observed on this athlete

On one 13-step ramp (2026-09-09, chest strap, artifacts 0.1 %), alpha1 did not fall with intensity: it was below 0.75 at the easiest steps and above 1.0 at 164-176 bpm, close to the step rate.
Cardio-locomotor coupling (HR locking to cadence) is a likely but uncited explanation (weak).
So for LT1 the drift methods are primary: H5 from history and the F1 drift test in the field; H4 and F3 count only when their gates pass, and after two "not monotonic" runs or ramps in a row pace marks alpha1 unreliable and skips it (`pace zones estimate --retry-h4` clears the mark).
Suggest F1 for LT1; suggest F3 only if `pace zones show` does not mark alpha1 unreliable and the user wants to try it.
Evidence: weak/expert opinion (one athlete, one ramp).

### Zones, accept and retest

Three zones: Z1 below LT1, Z2 between, Z3 at or above LT2; five watch floors (rule lt-5z-v1): 80 % and 90 % of LT1, LT1, LT2, and midway between LT2 and HRmax; only the LT1 and LT2 floors carry physiological meaning.
HR is primary for zones (terrain distorts pace).
A record becomes current only when the user accepts it; a lab record is current on entry and flagged expired after 6 months.
Retest guidance: meaningful threshold changes take about 6-12 weeks in recreational runners and day-to-day HR at a given speed varies by 3-4 bpm, so pace calls a new estimate due every 28 days or after a race or a hard run, and a field test after 12 weeks or when the last two estimates both differ from the current record by 4 bpm or more.
Evidence: strong that three-zone models anchored on LT1 and LT2 describe endurance training; weak/expert opinion for the exact five floors and retest intervals.

Sources:
- Iannetta D, Inglis EC, Mattu AT, Fontana FY, Pogliaghi S, Keir DA, Murias JM. 2020. A Critical Evaluation of Current Methods for Exercise Prescription in Women and Men. Med Sci Sports Exerc 52(2):466-473. https://doi.org/10.1249/MSS.0000000000002147 (abstract)
- Jamnick NA, Pettitt RW, Granata C, Pyne DB, Bishop DJ. 2020. An Examination and Critique of Current Methods to Determine Exercise Intensity. Sports Med 50(10):1729-1756. https://doi.org/10.1007/s40279-020-01322-8 (abstract)
- Jones AM, Burnley M, Black MI, Poole DC, Vanhatalo A. 2019. The maximal metabolic steady state: redefining the 'gold standard'. Physiol Rep 7(10):e14098. https://doi.org/10.14814/phy2.14098 (abstract)
- Hunter B, Meyler S, Maunder E, Cox TH, Muniz-Pumares D. 2024. The Relationship Between the Moderate-Heavy Boundary and Critical Speed in Running. Int J Sports Physiol Perform 19(9):963-972. https://doi.org/10.1123/ijspp.2024-0101 (abstract)
- Hunter B, Ledger A, Muniz-Pumares D. 2023. Remote Determination of Critical Speed and Critical Power in Recreational Runners. Int J Sports Physiol Perform 18(12):1449-1456. https://doi.org/10.1123/ijspp.2023-0276 (abstract)
- Smyth B, Muniz-Pumares D. 2020. Calculation of Critical Speed from Raw Training Data in Recreational Marathon Runners. Med Sci Sports Exerc 52(12):2637-2645. https://doi.org/10.1249/MSS.0000000000002412 (full text)
- Vesterinen V, Hokka L, Hynynen E, Mikkola J, Hakkinen K, Nummela A. 2014. Heart rate-running speed index may be an efficient method of monitoring endurance training adaptation. J Strength Cond Res 28(4):902-908. https://doi.org/10.1519/JSC.0000000000000349 (abstract)
- Rogers B, Giles D, Draper N, Hoos O, Gronwald T. 2020. A New Detection Method Defining the Aerobic Threshold for Endurance Exercise and Training Prescription Based on Fractal Correlation Properties of Heart Rate Variability. Front Physiol 11:596567. https://doi.org/10.3389/fphys.2020.596567 (full text)
- Van Hooren B, Mennen B, Gronwald T, Bongers BC, Rogers B. 2025. Correlation properties of heart rate variability to assess the first ventilatory threshold and fatigue in runners. J Sports Sci 43(2):125-134. https://doi.org/10.1080/02640414.2023.2277034 (abstract)
- Kaufmann S, Gronwald T, Herold F, Hoos O. 2023. Heart Rate Variability-Derived Thresholds for Exercise Intensity Prescription in Endurance Sports: A Systematic Review of Interrelations and Agreement with Different Ventilatory and Blood Lactate Thresholds. Sports Med Open 9(1):59. https://doi.org/10.1186/s40798-023-00607-2 (abstract)
- Gronwald T, Horn L, Schaffarczyk M, Hoos O. 2024. Correlation properties of heart rate variability for exercise prescription during prolonged running at constant speeds: A randomized cross-over trial. Eur J Sport Sci 24(11):1539-1551. https://doi.org/10.1002/ejsc.12175 (full text)
- House S. 2026. Understanding the Heart Rate Drift Test: A Practical Guide for Endurance Athletes. Uphill Athlete. https://uphillathlete.com/heart-rate-drift/ (full text)
- Friel J. 2008, updated 2026. Aerobic Endurance and Decoupling. TrainingPeaks. https://www.trainingpeaks.com/learn/articles/aerobic-endurance-and-decoupling/ (full text)
- McGehee JC, Tanner CJ, Houmard JA. 2005. A comparison of methods for estimating the lactate threshold. J Strength Cond Res 19(3):553-558. https://doi.org/10.1519/15444.1 (abstract)
- Seiler S. 2010. What is best practice for training intensity and duration distribution in endurance athletes? Int J Sports Physiol Perform 5(3):276-291. https://doi.org/10.1123/ijspp.5.3.276 (abstract)
- Stoggl TL, Sperlich B. 2015. The training intensity distribution among well-trained and elite endurance athletes. Front Physiol 6:295. https://doi.org/10.3389/fphys.2015.00295 (full text)
- Nuuttila OP, Nummela A, Korhonen E, Hakkinen K, Kyrolainen H. 2022. Individualized Endurance Training Based on Recovery and Training Status in Recreational Runners. Med Sci Sports Exerc 54(10):1690-1701. https://doi.org/10.1249/MSS.0000000000002968 (full text)
- Londeree BR. 1997. Effect of training on lactate/ventilatory thresholds: a meta-analysis. Med Sci Sports Exerc 29(6):837-843. https://doi.org/10.1097/00005768-199706000-00016 (abstract)
