"""History threshold methods H1 (critical speed), H2 (HR-speed regression), H3 (LT1 from CS), max HR.

Pure functions over per-second sample tables (no Garmin calls). Methods:
docs/superpowers/specs/2026-11-17-zones-methods.md sections 1, 3, 4, 5 and 14.1/14.2.
A sample row is {"t" (elapsed s), "hr", "speed", "dist", "alt", "cadence" (steps/min)}.
"""

import math
import statistics
from datetime import datetime
from typing import Any

from pace.envelope import PaceError
from pace.zones import R

STOP_SPEED = 1.0           # m/s; a stretch below this for more than STOP_SECONDS is a stop (section 1)
STOP_SECONDS = 10
STOP_GAP = 5               # a timestamp gap above this is a stop (section 1)
HR_MIN, HR_MAX = 30, 230   # HR samples outside are dropped
MAX_HR_SKIP_S = 300        # dry-strap spikes: first 5 minutes ignored for max HR
MAX_HR_WINDOW = 5
WARMUP_S = 600
MIN_CADENCE = 140          # steps/min; below is walking
FLAT_GRADE = 0.02
MAX_ASCENT_M_PER_KM = 10.0

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

H5_START_S = 900           # H5 segments start at 900 s, 60 s steps (methods doc 7)
H5_STEP_S = 60
H5_MAX_S = 4500
H5_MIN_S = 2700
H5_SPEED_TOL = 0.05
H5_GRADE_WINDOW_S = 300
H5_MAX_TEMP_C = 24
H5_PREVIOUS_RACE_H = 24
H5_HARD_TE = 4.0
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
    """Methods doc 1 stop rule: drop stretches above 10 s below 1.0 m/s; a timestamp gap above 5 s is a stop.

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
            while j + 1 < n and _slow(rows[j + 1]) and rows[j + 1]["t"] - rows[j]["t"] <= STOP_GAP:
                j += 1
            span = rows[j]["t"] - rows[i]["t"] + 1
            if span > STOP_SECONDS:
                excluded += round(span)
            else:
                kept.extend(rows[i:j + 1])
            i = j + 1
        else:
            kept.append(rows[i])
            i += 1
    for a, b in zip(rows, rows[1:]):
        if b["t"] - a["t"] > STOP_GAP:
            excluded += round(b["t"] - a["t"] - 1)
    return kept, excluded


def _slow(row: dict) -> bool:
    s = row.get("speed")
    return s is not None and s < STOP_SPEED


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
        self.dist, self.hr, self.alt, self.cad = dist, hr, alt, cad
        self.bad_alt = _prefix_bad(alt)
        self.bad_cad = _prefix_bad(cad)
        self.cad_sum = _prefix_sum(cad)
        self.hr_sum = _prefix_sum(hr)
        self.bad_hr = _prefix_bad(hr)
        asc = [0.0]
        for a, b in zip(alt, alt[1:]):
            asc.append(asc[-1] + (max(b - a, 0.0) if a is not None and b is not None else 0.0))
        self.asc = asc

    def start_index(self, from_t: float) -> int:
        return max(0, math.ceil(from_t - self.t0))

    def window_ok(self, i: int, j: int) -> bool:
        """Flat, not walking, complete elevation and cadence on grid indices i..j."""
        d = self.dist[j] - self.dist[i]
        if d <= 0 or self.bad_alt[j + 1] - self.bad_alt[i] or self.bad_cad[j + 1] - self.bad_cad[i]:
            return False
        if abs(self.alt[j] - self.alt[i]) / d > FLAT_GRADE or (self.asc[j] - self.asc[i]) > MAX_ASCENT_M_PER_KM * d / 1000:
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
        if groups and r["t"] - groups[-1][-1]["t"] <= STOP_GAP:
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
            entry["reason"] = "no valid window (flat, stop-free, cadence >= 140, after 600 s)"
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
        reasons.append("180 s speed below 1.08 x the 720 s speed (short effort not near maximal)")
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
        out["reasons"].append("fastest steady segment below 0.90 x CS: LT2 HR not extrapolated (suggest the 30-min test)")
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
    out = [round(y["t"] - x["t"] - 1) for x, y in zip(kept, kept[1:]) if y["t"] - x["t"] > STOP_GAP]
    for gap in (kept[0]["t"] - a, b - kept[-1]["t"]):
        if gap > STOP_GAP:
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


def _lap_spans(laps: list[dict]) -> list[tuple[float, float]]:
    """(start, end) of each lap in elapsed s from the first lap's startTimeGMT; cumulative durations if unparsable."""
    try:
        t0 = datetime.fromisoformat(str(laps[0]["startTimeGMT"]))
        starts = [(datetime.fromisoformat(str(lp["startTimeGMT"])) - t0).total_seconds() for lp in laps]
    except (KeyError, ValueError, TypeError):
        starts, acc = [], 0.0
        for lp in laps:
            starts.append(acc)
            acc += lp["duration"]
    return [(st, st + lp["duration"]) for st, lp in zip(starts, laps)]


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
                or (other.get("aerobicTrainingEffect") or 0) >= H5_HARD_TE)
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
        return {"id": rid, "rejected": "ascent above 10 m per km", "excluded_s": excluded}
    temp = _mean_temp(fit_records, s, e)
    if temp != "unknown" and temp >= H5_MAX_TEMP_C:
        return {"id": rid, "rejected": "mean temperature >= 24 C", "excluded_s": excluded}
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
    runs = [{"id": e.get("id"), "h": e["h"], "label": e["label"], "drift": e.get("drift")} for e in evidence]
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
    out.update(status="ok", estimate=est, confidence=conf)
    if h2 and h2.get("b"):
        out["pace_s_per_km"] = R(1000 / ((est - h2["a"]) / h2["b"]))
    return out


# --- F1 ---

def _steady_window(table: list[dict], laps: list[dict]) -> tuple[float, float]:
    """F1 steady part: the longest lap (needs 2+ laps), else 900 s .. 900 s + 3600 s capped at the table end."""
    if laps and len(laps) >= 2:
        spans = _lap_spans(laps)
        k = max(range(len(laps)), key=lambda i: (spans[i][1] - spans[i][0], -i))
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
    if not treadmill and _ascent_per_km(blocks, start, end) > MAX_ASCENT_M_PER_KM:
        reasons.append("ascent above 10 m per km")
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
        elif uneven:
            out["reasons"].append("uneven pace")
    return out


# --- F2 ---

def _tt_window(table: list[dict], laps: list[dict]) -> tuple[float, float] | None:
    """The time trial: consecutive laps summing to 1680..1920 s, closest to 1800 (ties: earliest); else the
    fastest stop-free 1800 s window; the window is cut to 1800 s."""
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
    top = None
    blocks, _ = _blocks(table)
    for blk in blocks:
        for i in range(blk.n - F2_S):
            d = blk.dist[i + F2_S] - blk.dist[i]
            if top is None or d > top[0]:
                top = (d, blk.t0 + i)
    return None if top is None else (top[1], top[1] + F2_S)


def time_trial(table: list[dict], *, laps: list[dict]) -> dict:
    """F2 (methods doc 9, 14.4): LT2 HR = mean HR over 600..1800 s of the trial, pace = duration / distance.

    Rejected when shorter than 1680 s, a stop above 10 s (`excluded_s` reports it) or ascent above 10 m per km.
    Fade (second 15 min speed < 0.95 x first) gives confidence low, else medium.
    """
    win = _tt_window(table, laps)
    if win is None:
        return {"status": "rejected", "reasons": [f"no lap or window of about {F2_S} s (shorter than {F2_MIN_S} s)"],
                "excluded_s": 0}
    a, b = win
    rows = [r for r in table if a <= r["t"] <= b]
    blocks, excluded = _blocks(rows)
    base: dict[str, Any] = {"bounds": [a, b], "duration_s": b - a, "excluded_s": excluded}
    reasons = []
    if b - a < F2_MIN_S:
        reasons.append(f"trial shorter than {F2_MIN_S} s")
    if excluded or len(blocks) != 1:
        reasons.append("a stop longer than 10 s")
    if blocks and _ascent_per_km(blocks, a, b) > MAX_ASCENT_M_PER_KM:
        reasons.append("ascent above 10 m per km")
    if reasons:
        return {"status": "rejected", "reasons": reasons, **base}
    blk = blocks[0]
    dist = blk.dist[-1] - blk.dist[0]
    n = blk.n - 1
    hr_n = (n - 600) - (blk.bad_hr[n] - blk.bad_hr[600])
    if dist <= 0 or n <= 600 or hr_n < HR_COVERAGE * (n - 600):
        return {"status": "rejected", "reasons": ["distance or HR missing"], **base}
    hr = (blk.hr_sum[n] - blk.hr_sum[600]) / hr_n
    mid = blk.t0 + n / 2
    first, second = _half_stats([blk], blk.t0, mid), _half_stats([blk], mid, blk.t0 + n)
    ratio = second["speed"] / first["speed"]
    fade = ratio < F2_FADE
    return {"status": "ok", "lt2": {"hr": R(hr), "pace_s_per_km": R(n / (dist / 1000))}, "distance_m": dist,
            "speed_ratio": ratio, "fade": fade, "confidence": "low" if fade else "medium",
            "reasons": ["faded, started too fast"] if fade else [], **base}


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
