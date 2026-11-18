"""Common data rules shared by the threshold methods: the numbers of methods doc section 1 and their neighbours.

docs/superpowers/specs/2026-11-17-zones-methods.md. Every module that applies these rules imports them from here,
so a value changes in one place; user-facing reasons are built from these names, never from literals.
Most limits are pace heuristics (section 1 says which); the evidence grades live in the methods doc.
"""

# --- section 1: stops and auto-pause ---
STOP_GAP_S = 5               # a timestamp gap above this is a stop
STOP_SPEED_MPS = 1.0         # a stretch below this speed ...
STOP_SLOW_S = 10             # ... lasting more than this is a stop; F2 also rejects any stop above it (section 9)

# --- section 1: flat windows (Forerunner 55 elevation is GPS-based and noisy) ---
ALT_SMOOTH_S = 61            # centered moving mean of altitude before any grade or ascent check (pace heuristic)
MAX_GRADE = 0.02             # abs(elevation end - start) / distance
MAX_ASCENT_M_PER_KM = 10.0   # total ascent, counted by ascent_profile on smoothed altitudes
ASCENT_HYSTERESIS_M = 2.0    # a rise counts only once it exceeds this since the last turning point (pace heuristic)

# --- section 1: walking, warm-up, HR artefacts and max HR ---
MIN_CADENCE = 140            # steps/min; windows below are walking
WARMUP_S = 600               # first seconds of a run excluded from the history methods
HR_MIN, HR_MAX = 30, 230     # HR samples outside are dropped
MAX_HR_SKIP_S = 300          # dry-strap spikes: the first 5 minutes are ignored for max HR

# --- section 6: RR intervals ---
GAP_RR_S = 2.0               # a raw RR above this is a signal dropout (a gap, never corrected)

# --- sections 7 and 14.6: a hard effort (as for a race) ---
HARD_EFFORT_TE = 4.0         # list-row aerobicTrainingEffect at or above this

# --- section 7 (H5 segment) and 14.7 phase 2 (drift list rows) ---
# These three move together: a run whose list-row duration is below H5_START_S + H5_MIN_S cannot hold a
# qualifying segment, so the lower end of H5_RUN_S must stay >= H5_START_S + H5_MIN_S (tests/test_rules.py checks
# it). Raising H5_MIN_S or H5_START_S without raising H5_RUN_S[0] spends calls on runs that can never qualify.
H5_START_S = 900             # segments start at 900 s (15 min warm-up), 60 s steps
H5_MIN_S = 2700              # shortest accepted segment
H5_RUN_S = (3600, 9000)      # drift picks: list-row duration 60-150 min (user decision 2026-11-18)

# --- 14.7 list-row pre-filters (elevation gain per km of the whole run) ---
SEL_MAX_GAIN = 15.0          # history picks: laxer than the window limit, a hilly run can hold flat windows
# drift picks: Garmin's whole-run elevationGain carries the same GPS noise as the samples (section 1), so this is
# laxer than MAX_ASCENT_M_PER_KM; H5 then checks the segment on smoothed altitudes (user decision 2026-11-18)
DRIFT_MAX_GAIN = 15.0


def smooth_altitude(times: list[float], alts: list[float | None], *,
                    width_s: float = ALT_SMOOTH_S) -> list[float | None]:
    """Centered moving mean of altitude over the samples with |t - t_i| <= width_s / 2 (methods doc section 1).

    `times` must be non-decreasing and as long as `alts`. None samples are skipped in the means and stay None in
    the output. The window is a time span, not a sample count, and is truncated at the ends of the series.
    O(n): two pointers and a running sum. Smooth a whole series once, never a window of it (that would truncate
    the mean at the window edges).
    """
    if len(times) != len(alts):
        raise ValueError("times and alts differ in length")
    half = width_s / 2
    out: list[float | None] = []
    lo = hi = 0                      # the window is samples lo..hi-1
    total, count = 0.0, 0
    for t, a in zip(times, alts):
        while hi < len(times) and times[hi] - t <= half:
            if alts[hi] is not None:
                total += alts[hi]
                count += 1
            hi += 1
        while t - times[lo] > half:
            if alts[lo] is not None:
                total -= alts[lo]
                count -= 1
            lo += 1
        if not count:
            total = 0.0              # drop the rounding residue of the running sum
        out.append(None if a is None else total / count)
    return out


def ascent_profile(alts: list[float | None], *, threshold_m: float = ASCENT_HYSTERESIS_M) -> list[float]:
    """Cumulative ascent (m) at each sample, with hysteresis against altitude noise.

    Callers pass altitudes already smoothed with `smooth_altitude` (methods doc section 1); this stays pure
    hysteresis.

    A climb counts only once the rise since the last low turning point exceeds `threshold_m`; it then counts in
    full (from that low point), and every further rise counts until a drop of more than `threshold_m` ends it.
    None samples are skipped (the cumulative value repeats). Window ascent = profile[j] - profile[i]; inside a
    longer series the hysteresis state carries over from before the window, so a window's value can differ from
    `ascent_m` of its samples alone by at most `threshold_m`.
    """
    out: list[float] = []
    total = 0.0
    climbing = False
    ext: float | None = None      # the extreme since the last turning point: the low when not climbing, else the high
    for a in alts:
        if a is not None:
            if ext is None:
                ext = a
            elif climbing:
                if a > ext:
                    total += a - ext
                    ext = a
                elif ext - a > threshold_m:
                    climbing, ext = False, a
            elif a < ext:
                ext = a
            elif a - ext > threshold_m:
                climbing = True
                total += a - ext
                ext = a
        out.append(total)
    return out


def ascent_m(alts: list[float | None], *, threshold_m: float = ASCENT_HYSTERESIS_M) -> float:
    """Total ascent (m) of a series of altitudes, with the hysteresis of `ascent_profile`."""
    prof = ascent_profile(alts, threshold_m=threshold_m)
    return prof[-1] if prof else 0.0
