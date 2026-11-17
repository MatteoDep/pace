"""History threshold methods H1 (critical speed), H2 (HR-speed regression), H3 (LT1 from CS), max HR.

Pure functions over per-second sample tables (no Garmin calls). Methods:
docs/superpowers/specs/2026-11-17-zones-methods.md sections 1, 3, 4, 5 and 14.1/14.2.
A sample row is {"t" (elapsed s), "hr", "speed", "dist", "alt", "cadence" (steps/min)}.
"""

import math
import statistics
from typing import Any

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
