"""Threshold methods H1-H5, field tests F1-F3, the LT1 combination and run selection, max HR.

Pure functions over list rows, per-second sample tables and aligned RR beats (no Garmin calls). Methods:
docs/superpowers/specs/2026-11-17-zones-methods.md sections 1, 3-9a and 14.
A sample row is {"t" (elapsed s), "hr", "speed", "dist", "alt", "cadence" (steps/min)}.
"""

import bisect
import math
import statistics
from datetime import date, datetime
from typing import Any

from pace import hrv
from pace.envelope import PaceError
from pace.rules import (
    DRIFT_MAX_GAIN,
    H5_MIN_S,
    H5_RUN_S,
    H5_START_S,
    HARD_EFFORT_TE,
    HR_MAX,
    HR_MIN,
    MAX_ASCENT_M_PER_KM,
    MAX_GRADE,
    MAX_HR_SKIP_S,
    MIN_CADENCE,
    SEL_MAX_GAIN,
    STOP_GAP_S,
    STOP_SLOW_S,
    STOP_SPEED_MPS,
    WARMUP_S,
    ascent_profile,
    smooth_altitude,
)
from pace.zones import R

MAX_HR_WINDOW = 5
ASCENT_REASON = f"ascent above {MAX_ASCENT_M_PER_KM:g} m per km"
NO_WINDOW_REASON = f"no valid window (flat, stop-free, cadence >= {MIN_CADENCE}, after {WARMUP_S} s)"

H1_WINDOWS = (180, 420, 720)
H1_PR_RANGE = (120, 1200)
D_PRIME_RANGE = (50, 500)
H1_NEAR_MAX = 1.08

H2_SEGMENT_S = 360
H2_END_S = 3600
H2_SUB_S = 60
H2_STEADY = 0.05
H2_HR_TAIL_S = 240
H2_HR_COVERAGE = 0.9
H2_MIN_SEGMENTS = 8
H2_MIN_RUNS = 3
H2_MIN_SPAN = 0.5
LT2_MIN_FRACTION = 0.90
SEE_LIMIT = 5.0
LT1_FRACTION = 0.823

WRIST_REASON = "wrist HR (use --include-wrist)"

H5_STEP_S = 60             # H5 segments grow from H5_START_S in 60 s steps (methods doc 7; see pace.rules)
H5_MAX_S = 4500
H5_SPEED_TOL = 0.05
H5_GRADE_WINDOW_S = 300
H5_MAX_TEMP_C = 24
H5_PREVIOUS_RACE_H = 24
H5_SPLIT = 5.0             # drift % above which a run counts as "above" LT1
H5_MAX_GAP = 15
H5_MEDIUM_GAP = 8
F1_MIN_S = 2400
F1_MAX_STOP_TOTAL_S = 60
F1_MAX_STOP_S = 30
F1_BAND = (3.5, 5.0)
F1_CV_LIMIT = 0.03
F1_WARMUP_S = 900          # no-laps fallback: steady part 900 s .. 900 s + 3600 s
F1_STEADY_S = 3600
F1_RETEST_STEP = 5
AUTO_LAP_M = 1100
AUTO_LAP_S = 480
F3_DISAGREE = 5
F2_MIN_S = 1680
F2_S = 1800
F2_MAX_LAP_S = 1920
F2_FADE = 0.95
HR_COVERAGE = 0.9


# --- sources and samples ---

def hr_source(activity: dict) -> str:
    """"strap" if get_activity's sensors list a heart-rate device (BLE or ANT+), else "wrist"."""
    sensors = ((activity or {}).get("metadataDTO") or {}).get("sensors") or []
    for s in sensors:
        if isinstance(s, dict) and "HEART_RATE" in (s.get("bleDeviceType"), s.get("antplusDeviceType")):
            return "strap"
    return "wrist"


def samples(details: dict) -> list[dict]:
    """Per-second rows from get_activity_details, read by raw epoch timestamps. Cadence in steps/min."""
    index = {
        d.get("key"): d.get("metricsIndex")
        for d in (details or {}).get("metricDescriptors", [])
        if isinstance(d.get("metricsIndex"), int)
    }
    if "directTimestamp" not in index:
        return []

    def pick(m: list, key: str) -> Any:
        i = index.get(key)
        v = m[i] if i is not None and i < len(m) else None
        return v if isinstance(v, (int, float)) else None

    raw = []
    for row in details.get("activityDetailMetrics", []):
        m = row.get("metrics") or []
        ts = pick(m, "directTimestamp")
        if ts is not None:
            raw.append((ts, m))
    raw.sort(key=lambda x: x[0])
    out = []
    t0 = raw[0][0] if raw else 0
    for ts, m in raw:
        t = (ts - t0) / 1000
        cad = pick(m, "directRunCadence")
        out.append({
            "t": int(t) if t == int(t) else t,
            "hr": pick(m, "directHeartRate"),
            "speed": pick(m, "directSpeed"),
            "dist": pick(m, "sumDistance"),
            "alt": pick(m, "directElevation"),
            "cadence": cad * 2 if cad is not None else None,   # directRunCadence is strides/min
        })
    return out


def moving_samples(rows: list[dict]) -> tuple[list[dict], int]:
    """Methods doc 1 stop rule: drop stretches above STOP_SLOW_S below STOP_SPEED_MPS; a timestamp gap above
    STOP_GAP_S is a stop.

    Returns the kept rows and the excluded seconds (dropped stretches plus the timestamp gaps).
    The rows around a stop are left with a time hole; windows never span a hole above 5 s.
    """
    rows = sorted(rows, key=lambda r: r["t"])
    kept: list[dict] = []
    excluded = 0
    i, n = 0, len(rows)
    while i < n:
        if _slow(rows[i]):
            j = i
            while j + 1 < n and _slow(rows[j + 1]) and rows[j + 1]["t"] - rows[j]["t"] <= STOP_GAP_S:
                j += 1
            span = rows[j]["t"] - rows[i]["t"] + 1
            if span > STOP_SLOW_S:
                excluded += round(span)
            else:
                kept.extend(rows[i:j + 1])
            i = j + 1
        else:
            kept.append(rows[i])
            i += 1
    for a, b in zip(rows, rows[1:]):
        if b["t"] - a["t"] > STOP_GAP_S:
            excluded += round(b["t"] - a["t"] - 1)
    return kept, excluded


def _slow(row: dict) -> bool:
    s = row.get("speed")
    return s is not None and s < STOP_SPEED_MPS


def max_hr(table: list[dict]) -> int | None:
    """Highest 5-s rolling median HR, first 300 s excluded, HR 30..230; None without data."""
    pts = [(r["t"], r["hr"]) for r in sorted(table, key=lambda r: r["t"])
           if r["t"] >= MAX_HR_SKIP_S and isinstance(r.get("hr"), (int, float)) and HR_MIN <= r["hr"] <= HR_MAX]
    best = None
    for i in range(len(pts) - MAX_HR_WINDOW + 1):
        chunk = pts[i:i + MAX_HR_WINDOW]
        if chunk[-1][0] - chunk[0][0] > MAX_HR_WINDOW:
            continue
        m = statistics.median(h for _, h in chunk)
        if best is None or m > best:
            best = m
    return None if best is None else R(best)


# --- windows over stop-free blocks ---

class _Block:
    """A stop-free stretch on a 1 s grid with prefix sums for O(1) window checks."""

    def __init__(self, t0: int, dist: list, hr: list, alt: list, cad: list):
        self.t0, self.n = t0, len(dist)
        self.dist, self.hr, self.cad = dist, hr, cad
        # section 1: GPS altitude smoothed once per block; grade and ascent both read this series (None stays None)
        self.alt = smooth_altitude(list(range(self.n)), alt)
        self.bad_alt = _prefix_bad(alt)
        self.bad_cad = _prefix_bad(cad)
        self.cad_sum = _prefix_sum(cad)
        self.hr_sum = _prefix_sum(hr)
        self.bad_hr = _prefix_bad(hr)
        self.asc = ascent_profile(self.alt)   # section 1 ascent with hysteresis, cumulative per grid index

    def start_index(self, from_t: float) -> int:
        return max(0, math.ceil(from_t - self.t0))

    def window_ok(self, i: int, j: int) -> bool:
        """Flat, not walking, complete elevation and cadence on grid indices i..j."""
        d = self.dist[j] - self.dist[i]
        if d <= 0 or self.bad_alt[j + 1] - self.bad_alt[i] or self.bad_cad[j + 1] - self.bad_cad[i]:
            return False
        if abs(self.alt[j] - self.alt[i]) / d > MAX_GRADE or (self.asc[j] - self.asc[i]) > MAX_ASCENT_M_PER_KM * d / 1000:
            return False
        return (self.cad_sum[j + 1] - self.cad_sum[i + 1]) / (j - i) >= MIN_CADENCE

    def speed(self, i: int, j: int) -> float:
        return (self.dist[j] - self.dist[i]) / (j - i)


def _prefix_bad(xs: list) -> list[int]:
    out = [0]
    for x in xs:
        out.append(out[-1] + (x is None))
    return out


def _prefix_sum(xs: list) -> list[float]:
    out = [0.0]
    for x in xs:
        out.append(out[-1] + (x or 0.0))
    return out


def _lerp(a: float | None, b: float | None, f: float) -> float | None:
    return None if a is None or b is None else a + (b - a) * f


def _clean_hr(v: Any) -> float | None:
    return v if isinstance(v, (int, float)) and HR_MIN <= v <= HR_MAX else None


def _blocks(table: list[dict]) -> tuple[list[_Block], int]:
    """Moving samples split at gaps above 5 s, resampled to a 1 s grid (interpolating inside gaps up to 5 s)."""
    kept, excluded = moving_samples([r for r in table if isinstance(r.get("dist"), (int, float))])
    groups: list[list[dict]] = []
    for r in kept:
        if groups and r["t"] - groups[-1][-1]["t"] <= STOP_GAP_S:
            groups[-1].append(r)
        else:
            groups.append([r])
    blocks = []
    for g in groups:
        first, last = math.ceil(g[0]["t"]), math.floor(g[-1]["t"])
        if last <= first:
            continue
        dist, hr, alt, cad = [], [], [], []
        k = 0
        for t in range(first, last + 1):
            while k + 1 < len(g) - 1 and g[k + 1]["t"] <= t:
                k += 1
            a, b = g[k], g[min(k + 1, len(g) - 1)]
            span = b["t"] - a["t"]
            f = 0.0 if span <= 0 or t <= a["t"] else min((t - a["t"]) / span, 1.0)
            dist.append(_lerp(a["dist"], b["dist"], f))
            hr.append(_lerp(_clean_hr(a.get("hr")), _clean_hr(b.get("hr")), f))
            alt.append(_lerp(a.get("alt"), b.get("alt"), f))
            cad.append(_lerp(a.get("cadence"), b.get("cadence"), f))
        blocks.append(_Block(first, dist, hr, alt, cad))
    return blocks, excluded


def _ols(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Intercept and slope of y = a + b x."""
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my - b * mx, b


def _insufficient(reasons: list[str], runs: list[dict], **extra: Any) -> dict:
    return {"status": "insufficient_data", "reasons": reasons, "runs": runs, **extra}


# --- H1 ---

def critical_speed(runs: list[dict], *, prs: list[dict] | None) -> dict:
    """H1: critical speed from the best flat stop-free 180/420/720 s windows (linear distance-time fit).

    prs: optional [{"duration_s", "distance_m"}] points; only durations 120..1200 s are used.
    """
    best: dict[int, dict] = {}
    report = []
    for r in runs:
        blocks, excluded = _blocks(r["table"])
        entry: dict[str, Any] = {"id": r["id"], "used": False, "excluded_s": excluded}
        for T in H1_WINDOWS:
            for blk in blocks:
                for i in range(blk.start_index(WARMUP_S), blk.n - T):
                    if not blk.window_ok(i, i + T):
                        continue
                    entry["used"] = True
                    d = blk.dist[i + T] - blk.dist[i]
                    if T not in best or d > best[T]["distance_m"]:
                        best[T] = {"T": T, "distance_m": d, "speed_mps": d / T, "run": r["id"], "start_t": blk.t0 + i}
        if not entry["used"]:
            entry["reason"] = NO_WINDOW_REASON
        report.append(entry)
    for T, w in best.items():
        for entry in report:
            if entry["id"] == w["run"]:
                entry.setdefault("best_for", []).append(T)
    windows = [best[T] for T in H1_WINDOWS if T in best]
    missing = [T for T in H1_WINDOWS if T not in best]
    if missing:
        return _insufficient([f"no valid {T} s window" for T in missing], report,
                             cs_mps=None, d_prime_m=None, windows=windows)
    points = [(w["T"], w["distance_m"]) for w in windows]
    for p in prs or []:
        if H1_PR_RANGE[0] <= p["duration_s"] <= H1_PR_RANGE[1]:
            points.append((p["duration_s"], p["distance_m"]))
    dprime, cs = _ols([p[0] for p in points], [p[1] for p in points])
    base = {"cs_mps": None, "d_prime_m": None, "windows": windows, "points": len(points),
            "rejected_fit": {"cs_mps": cs, "d_prime_m": dprime}}
    v720 = best[720]["speed_mps"]
    reasons = []
    if not D_PRIME_RANGE[0] <= dprime <= D_PRIME_RANGE[1]:
        reasons.append(f"D' {dprime:.0f} m outside {D_PRIME_RANGE[0]}..{D_PRIME_RANGE[1]} m")
    if cs >= v720:
        reasons.append("CS not below the 720 s window speed")
    if reasons:
        return _insufficient(reasons, report, **base)
    base.pop("rejected_fit")
    reasons = []
    if len({w["run"] for w in windows}) < 2:
        reasons.append("windows come from fewer than 2 different runs")
    if best[180]["speed_mps"] < H1_NEAR_MAX * v720:
        reasons.append(f"180 s speed below {H1_NEAR_MAX} x the 720 s speed (short effort not near maximal)")
    return {"status": "ok", "cs_mps": cs, "d_prime_m": dprime, "pace_s_per_km": R(1000 / cs),
            "windows": windows, "points": len(points), "confidence": "low" if reasons else "medium",
            "reasons": reasons, "runs": report}


# --- H2 ---

def hr_speed(runs: list[dict], *, include_wrist: bool = False) -> dict:
    """H2: HR = a + b * v from steady 360 s segments (methods doc 4)."""
    segs: list[dict] = []
    report = []
    for r in runs:
        if r.get("hr_source") != "strap" and not include_wrist:
            report.append({"id": r["id"], "used": False, "reason": WRIST_REASON})
            continue
        blocks, excluded = _blocks(r["table"])
        found = [dict(s, run=r["id"]) for blk in blocks for s in _segments(blk)]
        segs += found
        entry: dict[str, Any] = {"id": r["id"], "used": bool(found), "excluded_s": excluded}
        if not found:
            entry["reason"] = "no steady segment"
        report.append(entry)
    n, n_runs = len(segs), len({s["run"] for s in segs})
    extra: dict[str, Any] = {"n": n, "n_runs": n_runs, "segments": segs, "wrist_included": include_wrist}
    reasons = []
    if n < H2_MIN_SEGMENTS:
        reasons.append(f"{n} steady segments, need {H2_MIN_SEGMENTS}")
    if n_runs < H2_MIN_RUNS:
        reasons.append(f"segments from {n_runs} different runs, need {H2_MIN_RUNS}")
    if n and max(s["v"] for s in segs) - min(s["v"] for s in segs) < H2_MIN_SPAN:
        reasons.append(f"speed range below {H2_MIN_SPAN} m/s")
    if reasons:
        return _insufficient(reasons, report, **extra)
    a, b = _ols([s["v"] for s in segs], [s["hr"] for s in segs])
    if b <= 0:
        return _insufficient(["HR does not rise with speed (b <= 0)"], report, **extra)
    see = (sum((s["hr"] - a - b * s["v"]) ** 2 for s in segs) / (n - 2)) ** 0.5
    return {"status": "ok", "a": a, "b": b, "see": see, "v_min": min(s["v"] for s in segs),
            "v_max": max(s["v"] for s in segs), "reasons": [], "runs": report, **extra}


def _segments(blk: _Block) -> list[dict]:
    """Greedy non-overlapping 360 s steady segments with t in 600..3600 s."""
    out = []
    i = blk.start_index(WARMUP_S)
    last_start = int(H2_END_S - blk.t0) - H2_SEGMENT_S
    while i <= last_start and i + H2_SEGMENT_S < blk.n:
        j = i + H2_SEGMENT_S
        tail = j - H2_HR_TAIL_S
        n_hr = H2_HR_TAIL_S - (blk.bad_hr[j + 1] - blk.bad_hr[tail + 1])
        if blk.window_ok(i, j) and n_hr >= H2_HR_COVERAGE * H2_HR_TAIL_S and _steady(blk, i, j):
            hr = (blk.hr_sum[j + 1] - blk.hr_sum[tail + 1]) / n_hr
            out.append({"start_t": blk.t0 + i, "v": blk.speed(i, j), "hr": hr})
            i = j
        else:
            i += 1
    return out


def _steady(blk: _Block, i: int, j: int) -> bool:
    v = blk.speed(i, j)
    return all(abs(blk.speed(k, k + H2_SUB_S) - v) <= H2_STEADY * v for k in range(i, j, H2_SUB_S))


def hr_at(h2: dict, v: float) -> int:
    return R(h2["a"] + h2["b"] * v)


def lt2_from_cs(cs: dict, h2: dict | None) -> dict:
    """LT2 from H1 (+ H2), methods doc 14.1: pace 1000/CS; HR = hr(CS) only if the fastest segment reaches 0.90 x CS."""
    if cs.get("status") != "ok":
        return {"status": "insufficient_data", "reasons": ["no critical speed"] + list(cs.get("reasons", []))}
    out: dict[str, Any] = {"status": "ok", "pace_s_per_km": cs["pace_s_per_km"], "hr": None,
                           "confidence": cs["confidence"], "reasons": []}
    if not h2 or h2.get("status") != "ok":
        out["reasons"].append("no HR-speed regression")
    elif h2["v_max"] < LT2_MIN_FRACTION * cs["cs_mps"]:
        out["reasons"].append(f"fastest steady segment below {LT2_MIN_FRACTION:.2f} x CS: LT2 HR not extrapolated "
                              "(suggest the 30-min test)")
    else:
        out["hr"] = hr_at(h2, cs["cs_mps"])
        if h2["see"] > SEE_LIMIT:
            out["confidence"] = "low"
            out["reasons"].append(f"H2 SEE {h2['see']:.1f} bpm above {SEE_LIMIT:.0f}")
    return out


# --- H3 ---

def lt1_from_cs(cs: dict, h2: dict | None) -> dict:
    """H3: LT1 speed = 0.823 x CS; HR from H2 when available. Always confidence low."""
    if cs.get("status") != "ok":
        return {"status": "insufficient_data", "reasons": ["no critical speed"] + list(cs.get("reasons", []))}
    v = LT1_FRACTION * cs["cs_mps"]
    out: dict[str, Any] = {"status": "ok", "speed_mps": v, "pace_s_per_km": R(1000 / v), "hr": None,
                           "confidence": "low", "reasons": []}
    if h2 and h2.get("status") == "ok":
        out["hr"] = hr_at(h2, v)
    else:
        out["reasons"].append("no HR-speed regression: LT1 HR unknown")
    return out


# --- H5, F1, F2 shared helpers ---

def _half_stats(blocks: list[_Block], a: float, b: float) -> dict[str, Any]:
    """Moving distance, seconds, speed and mean HR over elapsed time a..b (stop-free blocks only)."""
    dist = secs = n_hr = 0
    hr_total = 0.0
    for blk in blocks:
        lo, hi = math.ceil(max(a, blk.t0)), math.floor(min(b, blk.t0 + blk.n - 1))
        if hi <= lo:
            continue
        i, j = lo - blk.t0, hi - blk.t0
        dist += blk.dist[j] - blk.dist[i]
        secs += j - i
        n_hr += (j - i) - (blk.bad_hr[j + 1] - blk.bad_hr[i + 1])
        hr_total += blk.hr_sum[j + 1] - blk.hr_sum[i + 1]
    return {"dist": dist, "secs": secs, "speed": dist / secs if secs else None,
            "hr": hr_total / n_hr if n_hr >= HR_COVERAGE * secs > 0 else None}


def _drift_pct(h1: dict, h2: dict, treadmill: bool = False) -> float | None:
    """F1 drift: outdoor (EF_1 / EF_2 - 1) x 100 with EF = speed / HR; treadmill (HR_2 / HR_1 - 1) x 100."""
    if h1["hr"] is None or h2["hr"] is None:
        return None
    if treadmill:
        return (h2["hr"] / h1["hr"] - 1) * 100
    if not h1["speed"] or not h2["speed"]:
        return None
    return ((h1["speed"] / h1["hr"]) / (h2["speed"] / h2["hr"]) - 1) * 100


def _mean_temp(fit_records: list[dict] | None, a: float, b: float) -> float | str:
    """Mean FIT record temperature over elapsed t in a..b, else "unknown"."""
    vals = [r["temp"] for r in fit_records or []
            if a <= r.get("t", -1) <= b and isinstance(r.get("temp"), (int, float))]
    return round(statistics.fmean(vals), 1) if vals else "unknown"


def _ascent_per_km(blocks: list[_Block], a: float, b: float) -> float:
    """Section 1 ascent per km within a..b on the blocks' smoothed altitudes. Pass blocks of the whole run, not
    of the a..b rows only, so the smoothing is not truncated at a and b."""
    asc = dist = 0.0
    for blk in blocks:
        lo, hi = math.ceil(max(a, blk.t0)), math.floor(min(b, blk.t0 + blk.n - 1))
        if hi > lo:
            asc += blk.asc[hi - blk.t0] - blk.asc[lo - blk.t0]
            dist += blk.dist[hi - blk.t0] - blk.dist[lo - blk.t0]
    return asc / (dist / 1000) if dist > 0 else 0.0


def _stop_lengths(rows: list[dict], a: float, b: float) -> list[int]:
    """Length of every stop in rows within a..b: holes between moving rows above 5 s, plus the window edges."""
    kept, _ = moving_samples([r for r in rows if isinstance(r.get("dist"), (int, float))])
    if not kept:
        return [round(b - a)]
    out = [round(y["t"] - x["t"] - 1) for x, y in zip(kept, kept[1:]) if y["t"] - x["t"] > STOP_GAP_S]
    for gap in (kept[0]["t"] - a, b - kept[-1]["t"]):
        if gap > STOP_GAP_S:
            out.append(round(gap))
    return out


def _cv(blocks: list[_Block], a: float, b: float) -> float | None:
    """Coefficient of variation of speed over 60 s sub-windows tiled from a inside stop-free blocks."""
    speeds = []
    for blk in blocks:
        lo, hi = math.ceil(max(a, blk.t0)), math.floor(min(b, blk.t0 + blk.n - 1))
        x = lo
        while x + 60 <= hi:
            speeds.append(blk.speed(x - blk.t0, x + 60 - blk.t0))
            x += 60
    if len(speeds) < 2 or statistics.fmean(speeds) <= 0:
        return None
    return statistics.pstdev(speeds) / statistics.fmean(speeds)


def _lap_len(lap: dict) -> float:
    """Elapsed lap length (pauses included) when Garmin gives it, else the lap duration."""
    return lap.get("elapsedDuration") or lap["duration"]


def _lap_spans(laps: list[dict]) -> list[tuple[float, float]]:
    """(start, end) of each lap in elapsed s from the first lap's startTimeGMT; cumulative durations if unparsable."""
    try:
        t0 = datetime.fromisoformat(str(laps[0]["startTimeGMT"]))
        starts = [(datetime.fromisoformat(str(lp["startTimeGMT"])) - t0).total_seconds() for lp in laps]
    except (KeyError, ValueError, TypeError):
        starts, acc = [], 0.0
        for lp in laps:
            starts.append(acc)
            acc += _lap_len(lp)
    return [(st, st + _lap_len(lp)) for st, lp in zip(starts, laps)]


# --- H5 ---

def _start_time(row: dict) -> datetime | None:
    raw = row.get("startTimeGMT") or row.get("startTimeLocal")
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def _type_key(row: dict) -> str | None:
    t = row.get("activityType")
    return t.get("typeKey") if isinstance(t, dict) else t


def drift_candidate(row: dict, rows: list[dict]) -> bool:
    """H5 list-row filter: outdoor running, not starting within 24 h after the start of a race or of a run
    with aerobicTrainingEffect >= 4.0 (other rows of `rows`; a row never excludes itself)."""
    if _type_key(row) != "running":
        return False
    start = _start_time(row)
    if start is None:
        return True
    for other in rows:
        if other is row:
            continue
        hard = ((other.get("eventType") or {}).get("typeKey") == "race"
                or (other.get("aerobicTrainingEffect") or 0) >= HARD_EFFORT_TE)
        o = _start_time(other)
        if hard and o is not None and 0 < (start - o).total_seconds() < H5_PREVIOUS_RACE_H * 3600:
            return False
    return True


def _grow(blk: _Block, i: int) -> int:
    """Longest H5 segment length (s, multiple of 60) starting at grid index i; 0 if none."""
    best, subs, k = 0, [], 0
    while True:
        length = H5_STEP_S * (k + 1)
        j = i + length
        if j >= blk.n or length > H5_MAX_S:
            return best
        lo = j - H5_STEP_S
        if blk.bad_cad[j + 1] - blk.bad_cad[lo] or (blk.cad_sum[j + 1] - blk.cad_sum[lo + 1]) / H5_STEP_S < MIN_CADENCE:
            return best
        subs.append(blk.speed(lo, j))
        mean = blk.speed(i, j)
        if any(abs(v - mean) > H5_SPEED_TOL * mean for v in subs):
            return best
        if length >= H5_GRADE_WINDOW_S and not blk.window_ok(j - H5_GRADE_WINDOW_S, j):
            return best
        best, k = length, k + 1


def drift_segment(run: dict, *, fit_records: list[dict] | None, include_wrist: bool = False) -> dict:
    """H5 step 1: the longest steady, flat, stop-free segment of one run -> an evidence row or a rejection.

    run = {"id", "hr_source", "table"}. Evidence row: {id, h, drift, label, bounds [start_t, end_t], length_s,
    temperature, excluded_s, wrist}; rejection: {id, rejected, excluded_s}.
    """
    rid = run["id"]
    wrist = run.get("hr_source") != "strap"
    if wrist and not include_wrist:
        return {"id": rid, "rejected": WRIST_REASON}
    blocks, excluded = _blocks(run["table"])
    best: tuple[int, _Block, int] | None = None
    for blk in blocks:
        s = H5_START_S + H5_STEP_S * math.ceil(max(0, blk.t0 - H5_START_S) / H5_STEP_S)
        while s - blk.t0 + H5_STEP_S < blk.n:
            length = _grow(blk, s - blk.t0)
            if length and (best is None or length > best[0]):
                best = (length, blk, s)
            s += H5_STEP_S
    if best is None or best[0] < H5_MIN_S:
        reason = f"segment shorter than {H5_MIN_S} s" if best else "no steady segment"
        return {"id": rid, "rejected": reason, "excluded_s": excluded}
    length, blk, s = best
    e = s + length
    if _ascent_per_km(blocks, s, e) > MAX_ASCENT_M_PER_KM:
        return {"id": rid, "rejected": ASCENT_REASON, "excluded_s": excluded}
    temp = _mean_temp(fit_records, s, e)
    if temp != "unknown" and temp >= H5_MAX_TEMP_C:
        return {"id": rid, "rejected": f"mean temperature >= {H5_MAX_TEMP_C} C", "excluded_s": excluded}
    mid = s + length / 2
    h1, h2 = _half_stats([blk], s, mid), _half_stats([blk], mid, e)
    d = _drift_pct(h1, h2)
    if d is None:
        return {"id": rid, "rejected": "HR missing in the segment", "excluded_s": excluded}
    row = {"id": rid, "h": R(h1["hr"]), "drift": round(d, 2), "label": "below" if d <= H5_SPLIT else "above",
           "bounds": [s, e], "length_s": length, "temperature": temp, "excluded_s": excluded}
    if wrist:
        row["wrist"] = True
    return row


def drift_bounds(evidence: list[dict], h2: dict | None = None) -> dict:
    """H5 step 2 (methods doc 7, 14.2): LT1 HR bounds from the accepted evidence rows (h, label).

    h2 (optional {"a", "b"}) gives the LT1 pace. Returns {status, estimate, pace_s_per_km, confidence, errors, gap,
    bounds {lower, upper}, n, notes, reasons, runs}; one-sided or too wide evidence has no estimate.
    """
    runs = [{"id": e.get("id"), "h": e["h"], "label": e["label"], "drift": e.get("drift"),
             **({"wrist": True} if e.get("wrist") else {})} for e in evidence]
    out: dict[str, Any] = {"status": "insufficient_data", "estimate": None, "pace_s_per_km": None,
                           "confidence": None, "errors": None, "gap": None,
                           "bounds": {"lower": None, "upper": None}, "n": len(runs), "notes": [], "reasons": [],
                           "runs": runs}
    below = [e["h"] for e in evidence if e["label"] == "below"]
    above = [e["h"] for e in evidence if e["label"] == "above"]
    if not evidence:
        out["reasons"].append("no accepted runs")
        return out
    if not above:
        out["bounds"]["lower"] = max(below)
        out["notes"].append(f"LT1 HR >= {max(below)}")
        out["reasons"].append("one-sided evidence: only below runs")
        return out
    if not below:
        out["bounds"]["upper"] = min(above)
        out["notes"].append(f"LT1 HR <= {min(above)}")
        out["reasons"].append("one-sided evidence: only above runs")
        return out
    hs = below + above
    scores = {c: sum(h > c for h in below) + sum(h < c for h in above) for c in range(min(hs) - 1, max(hs) + 2)}
    errors = min(scores.values())
    best = [c for c, v in scores.items() if v == errors]
    lo, hi = min(best), max(best)
    gap = hi - lo
    out.update(errors=errors, gap=gap, bounds={"lower": lo, "upper": hi})
    if errors == 0 and gap <= H5_MEDIUM_GAP and len(below) >= 2 and len(above) >= 2:
        conf = "medium"
    elif (errors == 0 and gap <= H5_MAX_GAP) or (errors == 1 and len(runs) >= 5):
        conf = "low"
    else:
        out["reasons"].append("bounds too wide" if errors == 0 else "inconsistent drift evidence")
        return out
    est = R((lo + hi) / 2)
    if any(e.get("wrist") for e in evidence):
        conf = "low"
        out["notes"].append("wrist HR evidence included: confidence low")
    out.update(status="ok", estimate=est, confidence=conf)
    if h2 and h2.get("b"):
        v = (est - h2["a"]) / h2["b"]
        out["pace_s_per_km"] = R(1000 / v) if v > 0 else None
    return out


# --- F1 ---

def _auto_laps(laps: list[dict]) -> bool:
    """Every lap is short (about 1 km or 8 min or less): the watch's auto-laps, not the user's lap presses."""
    return all((lp.get("distance") or 0) and lp["distance"] <= AUTO_LAP_M or _lap_len(lp) <= AUTO_LAP_S for lp in laps)


def _steady_window(table: list[dict], laps: list[dict]) -> tuple[float, float]:
    """F1 steady part: the longest lap (needs 2+ laps), else 900 s .. 900 s + 3600 s capped at the table end."""
    if laps and len(laps) >= 2:
        spans = _lap_spans(laps)
        k = max(range(len(laps)), key=lambda i: (spans[i][1] - spans[i][0], -i))
        if spans[k][1] - spans[k][0] >= F1_MIN_S or not _auto_laps(laps):
            return spans[k]
    end = max((r["t"] for r in table), default=0)
    return F1_WARMUP_S, min(F1_WARMUP_S + F1_STEADY_S, end)


def drift(table: list[dict], *, laps: list[dict], treadmill: bool, fit_records: list[dict] | None = None,
          f3_hr: int | None = None) -> dict:
    """F1 (methods doc 8, 14.3): drift % between the two equal-time halves of the steady part.

    The steady part is the longest lap (needs 2+ laps), else 900 s .. 4500 s. Stops are excluded from speed and HR
    and reported (`excluded_s`); more than 60 s stopped or one stop above 30 s rejects the test.
    Treadmill: HR only (device speed ignored), so there is no pace. Confidence is medium for "at LT1" with even
    pace, low when `f3_hr` (the newest F3 LT1 HR) differs by more than 5 bpm.
    """
    start, end = _steady_window(table, laps)
    rows = [r for r in table if start <= r["t"] <= end]
    blocks, excluded = _blocks(rows)
    stops = _stop_lengths(rows, start, end)
    base: dict[str, Any] = {"bounds": [start, end], "duration_s": end - start, "treadmill": treadmill,
                            "excluded_s": excluded, "temperature": _mean_temp(fit_records, start, end)}
    reasons = []
    if end - start < F1_MIN_S:
        reasons.append(f"steady part shorter than {F1_MIN_S} s")
    if sum(stops) > F1_MAX_STOP_TOTAL_S:
        reasons.append(f"stopped time above {F1_MAX_STOP_TOTAL_S} s")
    if stops and max(stops) > F1_MAX_STOP_S:
        reasons.append(f"a stop longer than {F1_MAX_STOP_S} s")
    if not treadmill and _ascent_per_km(_blocks(table)[0], start, end) > MAX_ASCENT_M_PER_KM:   # whole-run smoothing
        reasons.append(ASCENT_REASON)
    mid = start + (end - start) / 2
    h1, h2 = _half_stats(blocks, start, mid), _half_stats(blocks, mid, end)
    d = _drift_pct(h1, h2, treadmill)
    if d is None and not reasons:
        reasons.append("HR or speed missing in the steady part")
    if reasons:
        return {"status": "rejected", "reasons": reasons, **base}
    cv = None if treadmill else _cv(blocks, start, end)
    uneven = cv is not None and cv > F1_CV_LIMIT
    if d < F1_BAND[0]:
        verdict, retest = "below LT1", F1_RETEST_STEP
    elif d <= F1_BAND[1]:
        verdict, retest = "at LT1", None
    else:
        verdict, retest = "above LT1", -F1_RETEST_STEP
    hr1 = R(h1["hr"])
    out = {"status": "ok", "drift_pct": round(d, 2), "verdict": verdict, "uneven_pace": uneven,
           "speed_cv": cv, "lt1": None, "retest_hr": None if retest is None else hr1 + retest,
           "saveable": verdict == "at LT1" and not uneven, "confidence": None, "reasons": [],
           "halves": [{"speed_mps": h1["speed"], "hr": h1["hr"]}, {"speed_mps": h2["speed"], "hr": h2["hr"]}],
           **base}
    if verdict == "at LT1":
        out["lt1"] = {"hr": hr1, "pace_s_per_km": None if treadmill else R(1000 / h1["speed"])}
        if out["saveable"]:
            out["confidence"] = "medium"
            if f3_hr is not None and abs(f3_hr - hr1) > F3_DISAGREE:
                out["confidence"] = "low"
                out["reasons"].append(f"newest F3 LT1 HR {f3_hr} differs from {hr1} by more than {F3_DISAGREE} bpm")
    if uneven:
        out["reasons"].append("uneven pace")
    return out


# --- F2 ---

def _tt_window(table: list[dict], laps: list[dict]) -> tuple[float, float] | None:
    """The time trial: consecutive laps summing to 1680..1920 s, closest to 1800 (ties: earliest); else the
    fastest 1800 s window that spans no stop above STOP_SLOW_S (shorter stops are allowed, as with laps, and
    `time_trial` reports them); the window is cut to 1800 s."""
    if laps and len(laps) >= 2:
        spans = _lap_spans(laps)
        best = None
        for k in range(len(spans)):
            for j in range(k, len(spans)):
                total = spans[j][1] - spans[k][0]
                if F2_MIN_S <= total <= F2_MAX_LAP_S and (best is None or abs(total - F2_S) < best[0]):
                    best = (abs(total - F2_S), spans[k][0], min(spans[j][1], spans[k][0] + F2_S))
        if best:
            return best[1], best[2]
    kept, _ = moving_samples([r for r in table if isinstance(r.get("dist"), (int, float))])
    return _fastest_window(kept)


def _fastest_window(kept: list[dict]) -> tuple[float, float] | None:
    """F2 without laps: the F2_S window (starting on a moving sample) with the most distance, never spanning a
    stop above STOP_SLOW_S (ties: earliest). Distance at the window end is interpolated on the samples."""
    ts, ds = [r["t"] for r in kept], [r["dist"] for r in kept]
    regions, lo = [], 0
    for k in range(1, len(kept)):
        gap = ts[k] - ts[k - 1]
        if gap > STOP_GAP_S and round(gap - 1) > STOP_SLOW_S:   # a stop's length as in _stop_lengths
            regions.append((lo, k - 1))
            lo = k
    regions.append((lo, len(kept) - 1))
    top = None
    for i0, i1 in regions:
        for i in range(i0, i1 + 1):
            end = ts[i] + F2_S
            if end > ts[i1]:
                break
            j = bisect.bisect_left(ts, end, i, i1 + 1)
            d_end = ds[j] if ts[j] == end else _lerp(ds[j - 1], ds[j], (end - ts[j - 1]) / (ts[j] - ts[j - 1]))
            d = d_end - ds[i]
            if top is None or d > top[0]:
                top = (d, ts[i])
    return None if top is None else (top[1], top[1] + F2_S)


def time_trial(table: list[dict], *, laps: list[dict]) -> dict:
    """F2 (methods doc 9, 14.4): LT2 HR = mean HR over 600..1800 s of the trial, pace = duration / distance.

    Rejected when shorter than F2_MIN_S, a stop above STOP_SLOW_S (`excluded_s` reports it) or ascent above
    MAX_ASCENT_M_PER_KM.
    Fade (second 15 min speed < 0.95 x first) gives confidence low, else medium.
    """
    win = _tt_window(table, laps)
    if win is None:
        return {"status": "rejected", "reasons": [f"no lap or window of about {F2_S} s (shorter than {F2_MIN_S} s)"],
                "excluded_s": 0}
    a, b = win
    rows = [r for r in table if a <= r["t"] <= b]
    blocks, excluded = _blocks(rows)
    stops = _stop_lengths(rows, a, b)
    base: dict[str, Any] = {"bounds": [a, b], "duration_s": b - a, "excluded_s": excluded}
    reasons = []
    if b - a < F2_MIN_S:
        reasons.append(f"trial shorter than {F2_MIN_S} s")
    if stops and max(stops) > STOP_SLOW_S:
        reasons.append(f"a stop longer than {STOP_SLOW_S} s")
    if blocks and _ascent_per_km(_blocks(table)[0], a, b) > MAX_ASCENT_M_PER_KM:   # whole-run smoothing
        reasons.append(ASCENT_REASON)
    dists = [r["dist"] for r in rows if isinstance(r.get("dist"), (int, float))]
    if not blocks or len(dists) < 2:
        reasons.append("distance missing")
    if reasons:
        return {"status": "rejected", "reasons": reasons, **base}
    dist = dists[-1] - dists[0]
    last = _half_stats(blocks, a + 600, b)           # offsets 600 s.. of the trial, from the trial start
    mid = a + (b - a) / 2
    first, second = _half_stats(blocks, a, mid), _half_stats(blocks, mid, b)
    if dist <= 0 or last["hr"] is None or not first["speed"] or not second["speed"]:
        return {"status": "rejected", "reasons": ["distance or HR missing"], **base}
    ratio = second["speed"] / first["speed"]
    fade = ratio < F2_FADE
    notes = []
    if fade:
        notes.append("faded, started too fast")
    if stops:
        notes.append(f"stops of {sum(stops)} s excluded (none above {STOP_SLOW_S} s)")
    return {"status": "ok", "lt2": {"hr": R(last["hr"]), "pace_s_per_km": R((b - a) / (dist / 1000))},
            "distance_m": dist, "speed_ratio": ratio, "fade": fade,
            "confidence": "low" if fade or stops else "medium", "reasons": notes, **base}


# --- test workouts ---

def _pace(s: float) -> str:
    s = R(s)
    return f"{s // 60}:{s % 60:02d}"


def _hr_range(hr: float, spread: int = 3) -> str:
    return f"{R(hr) - spread}-{R(hr) + spread}"


def _lt1_hr(record: dict | None, estimate_hr: int | None) -> tuple[int | None, bool]:
    """(LT1 HR, from the current record?): the record's HR, else the newest estimate's lt1_hr."""
    hr = ((record or {}).get("lt1") or {}).get("hr")
    return (hr, True) if hr is not None else (estimate_hr, False)


def test_workout(kind: str, *, lt1_record: dict | None, estimate_lt1_hr: int | None, h2: dict | None) -> dict:
    """Workout DSL for "drift" | "time_trial" | "ramp" (methods doc 14.3, 14.4, 14.8), valid for build_workout.

    lt1_record: the current LT1 record ({"lt1": {"hr", "pace_s_per_km"}}); h2: {"a", "b", "v_min", "v_max"} chosen
    as in 14.5. Fallback order: record, then estimate HR, then RPE notes.
    """
    if kind == "drift":
        return _drift_workout(lt1_record, estimate_lt1_hr)
    if kind == "time_trial":
        return _tt_workout()
    if kind == "ramp":
        return _ramp_workout(lt1_record, estimate_lt1_hr, h2)
    raise PaceError("bad_args", f"unknown test kind {kind!r}; one of drift, time_trial, ramp")


def _drift_workout(record: dict | None, estimate_hr: int | None) -> dict:
    pace = ((record or {}).get("lt1") or {}).get("pace_s_per_km")
    build = {"type": "warmup", "duration": "12m", "note": "build gradually towards the target"}
    last3: dict[str, Any] = {"type": "warmup", "duration": "3m"}
    steady: dict[str, Any] = {"type": "interval", "duration": "60m"}
    if pace is not None:
        target = {"pace": f"{_pace(pace - 10)}-{_pace(pace + 10)}"}
        last3.update(target=target, note="hold the target pace")
        steady.update(target=dict(target), note="constant pace, flat route or treadmill, no stops; let HR drift")
    elif estimate_hr is not None:
        last3.update(target={"hr": _hr_range(estimate_hr)}, note="settle on the pace for the steady part")
        steady["note"] = "hold the pace reached at the end of the warm-up; let HR drift"
    else:
        build["note"] = "build to conversational effort, RPE 3-4"
        last3["note"] = "conversational effort, RPE 3-4"
        steady["note"] = "conversational, constant pace"
    return {"name": "LT1 drift test (F1)", "sport": "running",
            "description": "Hold pace, not HR; conversational effort, RPE 3-4 of 10. Flat route or treadmill, no stops.",
            "steps": [build, last3, steady, {"type": "cooldown", "duration": "10m"}]}


def _tt_workout() -> dict:
    return {"name": "LT2 30-min time trial (F2)", "sport": "running",
            "description": "Race effort for 30 min, alone, flat. Start controlled (the first 5 min should feel "
                           "slightly too easy); RPE 9-10 at the end.",
            "steps": [
                {"type": "warmup", "duration": "15m", "note": "easy"},
                {"repeat": 3, "steps": [{"type": "interval", "duration": "20s", "note": "stride"},
                                        {"type": "recovery", "duration": "40s"}]},
                {"type": "recovery", "duration": "3m", "note": "easy"},
                {"type": "interval", "duration": "10m", "note": "maximal sustainable effort, even pace"},
                {"type": "interval", "duration": "20m", "note": "keep the effort even; RPE 9-10 at the end"},
                {"type": "cooldown", "duration": "10m"}]}


RAMP_RPE = ("2", "2-3", "3", "3-4", "4", "4-5", "5", "6")


def _ramp_workout(record: dict | None, estimate_hr: int | None, h2: dict | None) -> dict:
    hr0, from_record = _lt1_hr(record, estimate_hr)
    steps: list[dict] = [{"type": "warmup", "duration": "10m", "note": "easy"}]
    for k in range(8):
        step: dict[str, Any] = {"type": "interval", "duration": "4m"}
        if hr0 is None:
            step["note"] = f"RPE {RAMP_RPE[k]} of 10"
        else:
            hr = hr0 - 21 + 6 * k
            v = (hr - h2["a"]) / h2["b"] if from_record and h2 and h2.get("b") else None
            if v is not None and 0.9 * h2["v_min"] <= v <= 1.1 * h2["v_max"]:
                p = R(1000 / v)
                step["target"] = {"pace": f"{_pace(p - 5)}-{_pace(p + 5)}"}
            else:
                step["target"] = {"hr": _hr_range(hr)}
                step["note"] = "hold the pace steady within the step"
        steps.append(step)
    steps.append({"type": "cooldown", "duration": "10m"})
    return {"name": "LT1 alpha1 ramp (F3)", "sport": "running",
            "description": "Eight 4-min steps, each 6 bpm above the last; hold the pace steady within each step.",
            "steps": steps}


# --- alpha1 gate and H4 (methods doc 6, 14.2) ---

A1_THRESHOLD = 0.75
A1_RHO_MAX = -0.6          # usable only if Spearman rho of HR and alpha1 <= this
A1_MIN_SIDE = 3            # windows or steps needed on each side of 0.75
A1_JUDGE_N = 6             # "not monotonic" counts only with >= 6 points ...
A1_JUDGE_HR_SPAN = 10      # ... spanning >= 10 bpm (controller ruling); else outcome "insufficient_data"
A1_JUDGE_SIDE = 2          # ... and >= max(2, ceil(0.10 x points)) on each side of 0.75; else "one_sided"
A1_JUDGE_SHARE = 0.10      # (controller rulings)
A1_LINEAR = (0.5, 1.0)     # near-linear part of the alpha1 decline (Rogers et al. 2020)
A1_LT2_NOTE = 0.5          # alpha1 0.5: a note only, never LT2
H4_MIN_POINTS = 60         # 5 min of window centres per side
H4_MIN_HR_SPAN = 10
H4_MAX_R = -0.5
H4_MEDIUM_SPREAD = 6
MEDIUM_ARTIFACTS = 0.03
F3_WINDOW_S = 120
F3_MIN_STEPS = 3           # fewer step laps are refused before the gate (which needs 3 on each side)
F3_MEDIUM_SIDE = 2
AGREE_BPM = 5
_CONF_RANK = {None: -1, "low": 0, "medium": 1, "high": 2}


def _ranks(v: list[float]) -> list[float]:
    """1-based ranks, ties get the average rank."""
    order = sorted(range(len(v)), key=lambda i: v[i])
    out = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def _pearson(x: list[float], y: list[float]) -> float | None:
    """Pearson r; None with fewer than 2 points or no variance."""
    if len(x) < 2:
        return None
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / math.sqrt(sxx * syy)


def spearman(x: list[float], y: list[float]) -> float:
    """Spearman rank correlation with average ranks for ties; 0.0 with fewer than 2 points or no variance."""
    if len(x) != len(y):
        raise ValueError("spearman needs two lists of the same length")
    r = _pearson(_ranks(x), _ranks(y))
    return 0.0 if r is None else r


def alpha1_gate(points: list[dict]) -> dict:
    """Section 6 per-run gate (also the F3 gate) over points {"hr", "alpha1"}.

    Usable only if rho <= -0.6 and >= 3 points on each side of 0.75; "not monotonic" takes precedence over
    "one-sided" as the outcome. rho is None with fewer than 2 points (then only "one-sided" applies).
    A failed rho counts as "not_monotonic" (toward the H4 streak) only with >= 6 points spanning >= 10 bpm and
    max(2, ceil(0.10 x points)) points on each side of 0.75 (controller rulings); with fewer points or a narrower
    span the outcome is "insufficient_data", with points (almost) only on one side "one_sided" (reason "mostly
    one-sided"); neither touches the streak.
    """
    above = sum(p["alpha1"] > A1_THRESHOLD for p in points)
    below = sum(p["alpha1"] < A1_THRESHOLD for p in points)
    rho = spearman([p["hr"] for p in points], [p["alpha1"] for p in points]) if len(points) >= 2 else None
    reasons = []
    judged = enough = False
    if rho is not None and rho > A1_RHO_MAX:
        hrs = [p["hr"] for p in points]
        enough = len(points) >= A1_JUDGE_N and max(hrs) - min(hrs) >= A1_JUDGE_HR_SPAN
        side = max(A1_JUDGE_SIDE, math.ceil(round(A1_JUDGE_SHARE * len(points), 9)))
        judged = enough and above >= side and below >= side
        if judged:
            reasons.append(f"not monotonic, rho = {rho:.2f}")
        elif enough:
            reasons.append(f"mostly one-sided: rho = {rho:.2f} not judged ({above} points above and {below} below "
                           f"0.75, need {side} each)")
        elif not enough:
            reasons.append(f"too few points to judge monotonicity ({len(points)}, HR span "
                           f"{max(hrs) - min(hrs):.0f} bpm; need {A1_JUDGE_N} spanning {A1_JUDGE_HR_SPAN} bpm)")
    if above < A1_MIN_SIDE or below < A1_MIN_SIDE:
        reasons.append("one-sided")
    if not reasons:
        outcome = "usable"
    elif judged:
        outcome = "not_monotonic"
    else:
        outcome = "insufficient_data" if rho is not None and rho > A1_RHO_MAX and not enough else "one_sided"
    return {"usable": not reasons, "outcome": outcome, "rho": rho, "reasons": reasons, "above": above,
            "below": below, "n": len(points)}


def _side_note(points: list[dict]) -> str | None:
    """One-sided note, e.g. "alpha1 above 0.75 up to 142 bpm", when every point lies on one side."""
    if points and all(p["alpha1"] > A1_THRESHOLD for p in points):
        return f"alpha1 above 0.75 up to {R(max(p['hr'] for p in points))} bpm"
    if points and all(p["alpha1"] < A1_THRESHOLD for p in points):
        return f"alpha1 below 0.75 from {R(min(p['hr'] for p in points))} bpm"
    return None


def _h4_run(run: dict) -> tuple[dict, list[dict], float | None, float | None]:
    """One run: gate, then the alpha1-vs-HR and alpha1-vs-speed regressions on the near-linear windows.

    Windows at coupling risk are dropped here too (hrv.windows already drops them; both are counted).
    Returns (report, used windows, LT1 HR, LT1 speed).
    """
    given = run.get("windows") or []
    ws = [w for w in given if w.get("cadence") is None or not hrv.coupled(w["hr"], w["cadence"])]
    gate = alpha1_gate(ws)
    out: dict[str, Any] = {"id": run.get("id"), "outcome": gate["outcome"], "rho": gate["rho"], "windows": len(ws),
                           "dropped_coupling": (run.get("dropped_coupling") or 0) + len(given) - len(ws),
                           "reasons": list(gate["reasons"]), "notes": [], "lt1_hr": None, "lt1_speed_mps": None,
                           "fit": None, "artifact_share": None}
    note = _side_note(ws)
    if note:
        out["notes"].append(note)
    if not gate["usable"]:
        return out, [], None, None
    pts = [w for w in ws if A1_LINEAR[0] <= w["alpha1"] <= A1_LINEAR[1]]
    up = sum(w["alpha1"] > A1_THRESHOLD for w in pts)
    down = sum(w["alpha1"] < A1_THRESHOLD for w in pts)
    if up < H4_MIN_POINTS or down < H4_MIN_POINTS:
        out["reasons"].append(f"{up} windows above and {down} below 0.75 within alpha1 0.5..1.0, "
                              f"need {H4_MIN_POINTS} each")
        return out, [], None, None
    a1 = [w["alpha1"] for w in pts]
    hrs = [w["hr"] for w in pts]
    lo, hi = min(hrs), max(hrs)
    if hi - lo < H4_MIN_HR_SPAN:
        out["reasons"].append(f"HR range of the windows below {H4_MIN_HR_SPAN} bpm")
        return out, [], None, None
    c, d = _ols(a1, hrs)
    r = _pearson(a1, hrs)
    out["fit"] = {"points": len(pts), "hr_range": [lo, hi], "hr": {"c": c, "d": d, "r": r}, "speed": None}
    if d >= 0 or r is None or r > H4_MAX_R:
        out["reasons"].append("HR does not fall with alpha1 (need d < 0 and r <= -0.5)")
        return out, [], None, None
    hr = c + d * A1_THRESHOLD
    if not lo <= hr <= hi:
        out["reasons"].append("0.75 crossing outside the HR range of the windows (no extrapolation)")
        return out, [], None, None
    out["lt1_hr"] = hr
    out["artifact_share"] = statistics.median(w["artifact_share"] for w in pts)
    speed = None
    speeds = [w.get("speed") for w in pts]
    if all(isinstance(v, (int, float)) for v in speeds):
        e, f = _ols(a1, speeds)
        rv = _pearson(a1, speeds)
        out["fit"]["speed"] = {"e": e, "f": f, "r": rv}
        if f < 0 and rv is not None and rv <= H4_MAX_R:
            speed = e + f * A1_THRESHOLD
    if speed is None:
        out["notes"].append("speed does not fall with alpha1: HR only")
    out["lt1_speed_mps"] = speed
    hr_half = c + d * A1_LT2_NOTE
    if lo <= hr_half <= hi:
        out["notes"].append(f"alpha1 0.5 at {R(hr_half)} bpm (a note, never LT2)")
    return out, pts, hr, speed


def alpha1_lt1(run_windows: list[dict]) -> dict:
    """H4 (methods doc 6): LT1 HR and pace from the alpha1 windows of strap runs.

    run_windows = [{"id", "windows" (pace.hrv.windows output), "dropped_coupling"}]; the caller keeps the 12-week
    and strap rules. Per-run outcomes ("usable" | "not_monotonic" | "one_sided") feed the H4 memory.
    """
    runs, hrs, speeds, used = [], [], [], []
    for run in run_windows:
        report, pts, hr, speed = _h4_run(run)
        runs.append(report)
        if hr is not None:
            hrs.append(hr)
            used += pts
            if speed is not None:
                speeds.append(speed)
    notes = [f"run {r['id']}: {n}" for r in runs for n in r["notes"]]
    out: dict[str, Any] = {"status": "ok", "hr": None, "pace_s_per_km": None, "speed_mps": None, "spread": None,
                           "confidence": None, "artifact_share": None, "runs": runs, "notes": notes, "reasons": []}
    if not hrs:
        if runs and all(r["outcome"] != "usable" for r in runs):
            out.update(status="alpha1_unusable", reasons=["no run passed the alpha1 gate"])
        else:
            out.update(status="insufficient_data",
                       reasons=["no run with a valid 0.75 crossing" if runs else "no runs with RR data"])
        return out
    spread = max(hrs) - min(hrs)
    share = statistics.median(w["artifact_share"] for w in used)
    medium = len(hrs) >= 2 and spread <= H4_MEDIUM_SPREAD and share <= MEDIUM_ARTIFACTS
    out.update(hr=R(statistics.median(hrs)), spread=spread, artifact_share=share,
               confidence="medium" if medium else "low")
    if speeds:
        v = statistics.median(speeds)
        out.update(speed_mps=v, pace_s_per_km=R(1000 / v))
    return out


# --- F3 ramp (methods doc 9a, 14.8) ---

def _ramp_step(k: int, span: tuple[float, float], aligned: list[dict], times: list[float], corr: tuple,
               treadmill: bool) -> dict:
    """One step: the last 120 s with the section 6 extraction, then coupling, artifact and beat rules."""
    start, end = span
    t0, t1 = end - F3_WINDOW_S, end
    step: dict[str, Any] = {"step": k, "bounds": [t0, t1], "status": "skipped", "reason": None, "hr": None,
                            "speed_mps": None, "cadence": None, "alpha1": None, "artifact_share": None, "beats": None}
    ctimes, cvals, flags = corr
    lo, hi = bisect.bisect_left(times, t0), bisect.bisect_left(times, t1)
    cads = [b.get("cadence") for b in aligned[lo:hi]]
    if end - start < F3_WINDOW_S:
        step["reason"] = f"step shorter than {F3_WINDOW_S} s"
    elif hi == lo:
        step["reason"] = "no beats in the window"
    elif any(hrv.bad_beat(b) for b in aligned[lo:hi]):
        step["reason"] = "gap or stop in the window"
    elif any(c is None for c in cads):
        step["reason"] = "cadence missing"
    if step["reason"]:
        return step
    x = cvals[bisect.bisect_left(ctimes, t0):bisect.bisect_left(ctimes, t1)]
    hr, cadence = 60 / statistics.fmean(x), statistics.fmean(cads)
    share = sum(flags[lo:hi]) / (hi - lo)
    step.update(hr=hr, cadence=cadence, artifact_share=share, beats=len(x))
    if not treadmill:
        da, db = _beat_dist(aligned, times, t0), _beat_dist(aligned, times, t1)
        step["speed_mps"] = None if da is None or db is None else (db - da) / F3_WINDOW_S
    if hrv.coupled(hr, cadence):
        step.update(status="dropped", reason="coupling risk: HR within 5% of the step rate or of half of it")
    elif share > hrv.MAX_ARTIFACT_SHARE:
        step.update(status="dropped", reason="artifact share above 5%")
    elif len(x) < hrv.MIN_BEATS:
        step["reason"] = f"fewer than {hrv.MIN_BEATS} corrected beats"
    else:
        step["alpha1"] = hrv.dfa_alpha1(x)
        if step["alpha1"] is None:
            step["reason"] = "alpha1 undefined"
        else:
            step["status"] = "kept"
    return step


def _beat_dist(aligned: list[dict], times: list[float], t: float) -> float | None:
    idx = bisect.bisect_right(times, t) - 1
    v = aligned[idx].get("dist") if idx >= 0 else None
    return v if isinstance(v, (int, float)) else None


def ramp(aligned: list[dict], *, laps: list[dict], treadmill: bool) -> dict:
    """F3 (methods doc 9a, 14.8): one alpha1 window per step (its last 120 s), the gate, then the 0.75 crossing.

    aligned = pace.fit.aligned_rr output; steps = the laps between the warm-up lap and the cool-down lap.
    Laps that look automatic (F1's `_auto_laps` heuristic) are not steps, and fewer than F3_MIN_STEPS steps
    refuse the test. `outcome` is the gate outcome for the H4 memory (None when refused before the gate).
    Treadmill: HR only.
    """
    out: dict[str, Any] = {"status": "rejected", "outcome": None, "lt1": None, "confidence": None, "bracketed": False,
                           "saveable": False, "steps": [], "dropped_coupling": 0, "gate": None, "fit": None,
                           "treadmill": treadmill, "reasons": [], "notes": []}
    laps = laps or []
    if laps and _auto_laps(laps):
        out["reasons"].append(f"the laps look automatic (every lap {AUTO_LAP_M} m or {AUTO_LAP_S // 60} min or "
                              "shorter): press lap at the warm-up end and at each step change")
        return out
    if len(laps) - 2 < F3_MIN_STEPS:
        out["reasons"].append(f"fewer than {F3_MIN_STEPS} steps: steps need laps (a warm-up lap, at least "
                              f"{F3_MIN_STEPS} step laps and a cool-down lap)")
        return out
    if not aligned:
        out["reasons"].append("no RR data")
        return out
    times = [b["t_s"] for b in aligned]
    corr = hrv.corrected_beats(aligned)
    steps = [_ramp_step(k, span, aligned, times, corr, treadmill)
             for k, span in enumerate(_lap_spans(laps)[1:-1], start=1)]
    kept = [s for s in steps if s["status"] == "kept"]
    gate = alpha1_gate(kept)
    out.update(steps=steps, gate=gate, outcome=gate["outcome"],
               dropped_coupling=sum(s["status"] == "dropped" and s["reason"].startswith("coupling") for s in steps))
    if not gate["usable"]:
        out.update(status="alpha1_unusable", reasons=list(gate["reasons"]))
        return out
    pts = [s for s in kept if A1_LINEAR[0] <= s["alpha1"] <= A1_LINEAR[1]]
    up = sum(s["alpha1"] > A1_THRESHOLD for s in pts)
    down = sum(s["alpha1"] < A1_THRESHOLD for s in pts)
    if not up or not down:
        out["reasons"].append("the ramp does not cross alpha1 0.75 within 0.5..1.0")
        return out
    a1 = [s["alpha1"] for s in pts]
    c, d = _ols(a1, [s["hr"] for s in pts])
    out["fit"] = {"points": len(pts), "hr": {"c": c, "d": d}, "speed": None}
    if d >= 0:
        out["reasons"].append("HR does not fall with alpha1 (d >= 0)")
        return out
    pace = None
    speeds = [s["speed_mps"] for s in pts]
    if not treadmill and all(isinstance(v, (int, float)) for v in speeds):
        e, f = _ols(a1, speeds)
        out["fit"]["speed"] = {"e": e, "f": f}
        v = e + f * A1_THRESHOLD
        if f < 0 and v > 0:
            pace = R(1000 / v)
    if not treadmill and pace is None:
        out["notes"].append("speed does not fall with alpha1: no LT1 pace")
    share = statistics.median(s["artifact_share"] for s in kept)
    medium = up >= F3_MEDIUM_SIDE and down >= F3_MEDIUM_SIDE and share <= MEDIUM_ARTIFACTS
    out.update(status="ok", lt1={"hr": R(c + d * A1_THRESHOLD), "pace_s_per_km": pace},
               confidence="medium" if medium else "low", bracketed=True, saveable=True)
    return out


# --- LT1 combination (14.2) ---

def _lt1_of(res: dict | None) -> float | None:
    """LT1 HR of an H3/H4 ("hr") or H5 ("estimate") result with status ok, else None."""
    if not res or res.get("status") != "ok":
        return None
    return res.get("hr") if res.get("hr") is not None else res.get("estimate")


def combine_lt1(h3: dict | None, h4: dict | None, h5: dict | None, h2: dict | None) -> dict:
    """Methods doc 14.2: H4 and H5 combined, H3 as fallback (never averaged in).

    h3 = lt1_from_cs, h4 = alpha1_lt1, h5 = drift_bounds, h2 = hr_speed (or {"a", "b"}) results.
    Final confidence is medium only if the picked result's own confidence is medium, a method outside the picked
    ones agrees within 5 bpm (for "H4+H5" the mutual agreement of H4 and H5 counts, controller ruling), and no
    method with own confidence medium disagrees; never high.
    """
    vals = {"H3": _lt1_of(h3), "H4": _lt1_of(h4), "H5": _lt1_of(h5)}
    confs = {"H3": "low", "H4": (h4 or {}).get("confidence"), "H5": (h5 or {}).get("confidence")}
    out: dict[str, Any] = {"status": "insufficient_data", "hr": None, "pace_s_per_km": None, "method": None,
                           "confidence": None, "own_confidence": None, "flags": [], "suggest": None,
                           "h3": {"hr": (h3 or {}).get("hr"), "pace_s_per_km": (h3 or {}).get("pace_s_per_km")},
                           "notes": [], "reasons": []}
    v4, v5 = vals["H4"], vals["H5"]
    if v4 is not None and v5 is not None:
        if abs(v4 - v5) <= AGREE_BPM:
            picked, hr = ["H4", "H5"], R((v4 + v5) / 2)
            own = max(confs["H4"], confs["H5"], key=lambda c: _CONF_RANK.get(c, -1))
        else:
            name = "H4" if _CONF_RANK.get(confs["H4"], -1) > _CONF_RANK.get(confs["H5"], -1) else "H5"
            picked, hr, own = [name], R(vals[name]), confs[name]
            out["flags"].append(f"H4 and H5 disagree by {R(abs(v4 - v5))} bpm")
            out["suggest"] = {"test": "F1", "hr": R((v4 + v5) / 2)}
    elif v4 is not None or v5 is not None:
        name = "H4" if v4 is not None else "H5"
        picked, hr, own = [name], R(vals[name]), confs[name]
    elif h3 and h3.get("status") == "ok":
        picked, own = ["H3"], "low"
        hr = None if vals["H3"] is None else R(vals["H3"])
    else:
        out["reasons"].append("no LT1 method gave an estimate")
        return out
    if picked == ["H3"]:
        pace = h3.get("pace_s_per_km")
    elif h2 and h2.get("status", "ok") == "ok" and h2.get("b"):
        v = (hr - h2["a"]) / h2["b"]
        pace = R(1000 / v) if v > 0 else None
    elif "H4" in picked:
        pace = h4.get("pace_s_per_km")
    else:
        pace = None
    others = [v for m, v in vals.items() if m not in picked and v is not None]
    vetoed = any(confs[m] == "medium" and abs(v - hr) > AGREE_BPM
                 for m, v in vals.items() if v is not None and hr is not None)
    agreed = hr is not None and (len(picked) == 2 or any(abs(v - hr) <= AGREE_BPM for v in others))
    out.update(status="ok", hr=hr, pace_s_per_km=pace, method="+".join(picked), own_confidence=own,
               confidence="medium" if own == "medium" and agreed and not vetoed else "low")
    out["notes"] += [f"H4: {n}" for n in (h4 or {}).get("notes") or []]
    bounds = (h5 or {}).get("bounds") or {}
    if (h5 or {}).get("status") != "ok" and hr is not None and (bounds.get("lower") is None) != (bounds.get("upper") is None):
        lower, upper = bounds.get("lower"), bounds.get("upper")
        text, ok = (f"LT1 HR >= {lower}", hr >= lower) if lower is not None else (f"LT1 HR <= {upper}", hr <= upper)
        out["notes"].append(f"H5: {text} ({'consistent' if ok else 'inconsistent'} with {hr})")
    return out


# --- run selection (14.7) ---

SEL_SLOTS = 6
SEL_PERCENTILES = (10, 30, 50, 70, 90, 100)
SEL_MIN_S = 1200
STEADY_S = (1800, 5400)
FIT_MAX_DOWNLOADS = 6
FIT_MIN_SPAN_MIN = 5.0
FIT_BAND_BPM = 15
FIT_WINDOW_S = 120
FIT_STEP_S = 5


def _row_id(row: dict) -> int:
    return row.get("activityId")


def tie_key(row: dict) -> tuple[float, int]:
    """Tie-break key: newest first, then lowest activity id."""
    start = _start_time(row)
    ts = (start.replace(tzinfo=None) - datetime(1970, 1, 1)).total_seconds() if start else -math.inf
    return -ts, _row_id(row)


def _num(v: Any) -> float | None:
    return v if isinstance(v, (int, float)) else None


def _prefilter(row: dict, span: tuple[int, int | None], max_gain: float) -> str | None:
    """List-row filter of 14.7; the reason a row is not a candidate, or None."""
    kind = _type_key(row)
    if kind != "running":
        return f"not outdoor running ({kind})"
    dur = _num(row.get("duration"))
    if dur is None or dur < span[0] or (span[1] is not None and dur > span[1]):
        return (f"duration outside {span[0] // 60}-{span[1] // 60} min" if span[1] is not None
                else f"shorter than {span[0] // 60} min")
    if _num(row.get("averageHR")) is None:
        return "no average HR"
    dist = _num(row.get("distance"))
    if not dist:
        return "no distance"
    if (_num(row.get("elevationGain")) or 0) / (dist / 1000) > max_gain:
        return f"elevation gain above {max_gain:g} m per km"
    return None


def _percentile_picks(pool: list[dict], key: str) -> tuple[list[int], list[dict]]:
    """Up to 6 picks: for each p, the not yet picked row nearest the nearest-rank percentile of `key`."""
    vals = sorted(r[key] for r in pool)
    n = len(vals)
    picks: list[int] = []
    targets = []
    for p in SEL_PERCENTILES:
        if len(picks) == n:
            break
        target = vals[(p * n + 99) // 100 - 1]
        rest = [r for r in pool if _row_id(r) not in picks]
        best = min(rest, key=lambda r: (round(abs(r[key] - target), 9), *tie_key(r)))
        picks.append(_row_id(best))
        targets.append({"p": p, "target": target, "id": _row_id(best)})
    return picks, targets


def select_history(rows_8w: list[dict], *, exclude: set[int] = frozenset()) -> dict:
    """14.7 phase 1 on list rows only: up to 6 effort runs (races, then maxHR, then averageSpeed) and up to 6
    steady runs (30-90 min, nearest-rank averageSpeed percentiles) among the rest. `exclude`: ids already picked."""
    cands, skipped = [], []
    for row in rows_8w:
        if _row_id(row) in exclude:
            continue
        reason = _prefilter(row, (SEL_MIN_S, None), SEL_MAX_GAIN)
        if reason:
            skipped.append({"id": _row_id(row), "reason": reason})
        else:
            cands.append(row)

    def effort_key(r: dict) -> tuple:
        race = (r.get("eventType") or {}).get("typeKey") == "race"
        return not race, -(_num(r.get("maxHR")) or 0), -(_num(r.get("averageSpeed")) or 0), *tie_key(r)

    effort = [_row_id(r) for r in sorted(cands, key=effort_key)[:SEL_SLOTS]]
    pool = [r for r in cands if _row_id(r) not in effort and _num(r.get("averageSpeed")) is not None
            and STEADY_S[0] <= r["duration"] <= STEADY_S[1]]
    steady, targets = _percentile_picks(pool, "averageSpeed")
    return {"effort": effort, "steady": steady, "steady_targets": targets, "skipped": skipped}


def select_drift(rows_12w: list[dict], *, ref_hr: int | None, exclude: set[int] = frozenset()) -> dict:
    """14.7 phase 2: up to 6 H5 drift picks among 60-150 min flat runs (previous-day rule of section 7).

    With a ref: alternate nearest averageHR below ref, then at or above, continuing on the other side when one
    runs out; without: nearest-rank percentiles of averageHR. `exclude`: ids to leave out (e.g. wrist runs).
    """
    cands, skipped = [], []
    for row in rows_12w:
        if _row_id(row) in exclude:
            continue
        reason = _prefilter(row, H5_RUN_S, DRIFT_MAX_GAIN)
        if reason is None and not drift_candidate(row, rows_12w):
            reason = (f"within {H5_PREVIOUS_RACE_H} h after a race or a run with aerobicTrainingEffect "
                      f">= {HARD_EFFORT_TE}")
        if reason:
            skipped.append({"id": _row_id(row), "reason": reason})
        else:
            cands.append(row)
    if ref_hr is None:
        picks, targets = _percentile_picks(cands, "averageHR")
        return {"picks": picks, "mode": "percentile", "ref_hr": None, "targets": targets, "skipped": skipped}
    below = sorted((r for r in cands if r["averageHR"] < ref_hr),
                   key=lambda r: (round(ref_hr - r["averageHR"], 9), *tie_key(r)))
    above = sorted((r for r in cands if r["averageHR"] >= ref_hr),
                   key=lambda r: (round(r["averageHR"] - ref_hr, 9), *tie_key(r)))
    picks = []
    while len(picks) < SEL_SLOTS and (below or above):
        first, second = (below, above) if len(picks) % 2 == 0 else (above, below)
        picks.append(_row_id((first or second).pop(0)))
    return {"picks": picks, "mode": "alternate", "ref_hr": ref_hr, "targets": [], "skipped": skipped}


def _window_hrs(table: list[dict]) -> list[float]:
    """Mean HR of each qualifying 2-min window of a sample table (section 6 rules without the RR ones):
    inside 600..4500 s, stop-free, flat, cadence >= 140, steady 30 s sub-windows, steady 120 s before, no 30 s
    surge in the 180 s before, no coupling risk. Windows every 5 s, so each counts as 5 s of window centres."""
    out = []
    blocks, _ = _blocks(table)
    for blk in blocks:
        t0 = FIT_STEP_S * math.ceil(max(hrv.WINDOW_FROM_S, blk.t0 + hrv.PRIOR_SURGE_S) / FIT_STEP_S)
        while t0 + FIT_WINDOW_S <= hrv.WINDOW_TO_S and t0 + FIT_WINDOW_S - blk.t0 < blk.n:
            i = t0 - blk.t0
            j = i + FIT_WINDOW_S
            t0 += FIT_STEP_S
            if not blk.window_ok(i, j):
                continue
            v = blk.speed(i, j)
            band = hrv.SPEED_BAND * v
            sub = hrv.SUB_WINDOW_S
            if any(abs(blk.speed(k, k + sub) - v) > band for k in range(i, j, sub)):
                continue
            if abs(blk.speed(i - hrv.PRIOR_MEAN_S, i) - v) > band:
                continue
            if any(blk.speed(k, k + sub) > v + band for k in range(i - hrv.PRIOR_SURGE_S, i - sub + 1, FIT_STEP_S)):
                continue
            n_hr = FIT_WINDOW_S - (blk.bad_hr[j + 1] - blk.bad_hr[i + 1])
            if n_hr < HR_COVERAGE * FIT_WINDOW_S:
                continue
            hr = (blk.hr_sum[j + 1] - blk.hr_sum[i + 1]) / n_hr
            if hrv.coupled(hr, (blk.cad_sum[j + 1] - blk.cad_sum[i + 1]) / FIT_WINDOW_S):
                continue
            out.append(hr)
    return out


def fit_span_score(table: list[dict], ref_hr: int | None) -> float:
    """14.7 span score in minutes: min(minutes of qualifying windows with HR in [ref - 15, ref), minutes in
    [ref, ref + 15)); without a ref, the minutes with HR inside the run's middle 80% (nearest-rank P10..P90 of the
    HR samples in 600..4500 s)."""
    hrs = _window_hrs(table)
    per = FIT_STEP_S / 60
    if ref_hr is not None:
        lo = sum(ref_hr - FIT_BAND_BPM <= h < ref_hr for h in hrs)
        hi = sum(ref_hr <= h < ref_hr + FIT_BAND_BPM for h in hrs)
        return min(lo, hi) * per
    vals = sorted(r["hr"] for r in table
                  if hrv.WINDOW_FROM_S < r["t"] <= hrv.WINDOW_TO_S and _clean_hr(r.get("hr")) is not None)
    if not vals:
        return 0.0
    n = len(vals)
    p10, p90 = vals[(10 * n + 99) // 100 - 1], vals[(90 * n + 99) // 100 - 1]
    return sum(p10 <= h <= p90 for h in hrs) * per


def _run_day(run: dict) -> date | None:
    d = run.get("date")
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    try:
        return date.fromisoformat(str(d)[:10])
    except ValueError:
        return None


def select_fit(detailed: list[dict], *, ref_hr: int | None, cached: set[int], no_rr: set[int], rr_since: date,
               unreliable: bool, include_wrist: bool = False) -> list[int]:
    """14.7 FIT downloads for H4: up to 6 ids by span score (>= 5 min; ties newest first, then lowest id).

    detailed = [{"id", "date", "hr_source", "table"}] (runs already fetched in detail). Skips cached and no-RR
    runs, wrist runs (unless include_wrist) and runs before rr_since; [] while H4 is marked unreliable.
    """
    if unreliable:
        return []
    scored = []
    for run in detailed:
        rid = run["id"]
        day = _run_day(run)
        if rid in cached or rid in no_rr or day is None or day < rr_since:
            continue
        if run.get("hr_source") != "strap" and not include_wrist:
            continue
        score = fit_span_score(run["table"], ref_hr)
        if score >= FIT_MIN_SPAN_MIN:
            scored.append((round(score, 9), day, rid))
    scored.sort(key=lambda s: (-s[0], -s[1].toordinal(), s[2]))
    return [rid for *_, rid in scored[:FIT_MAX_DOWNLOADS]]
