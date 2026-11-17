"""RR artifact correction, smoothness-priors detrending, DFA alpha1 and alpha1 windows.

Pure module, pure Python (no numpy). Methods: docs/superpowers/specs/2026-11-17-zones-methods.md section 6
(artifact rule, detrending, DFA, qualifying windows) and section 1 (stops, flat, walking).
"""

import bisect
import math
from typing import Any

MEDIAN_HALF = 10           # 21-beat local median
BAND = 0.20                # valid if within 20% of the local median
GAP_RR_S = 2.0             # a raw RR above this is a signal dropout: a gap, never corrected
LAMBDA = 500.0             # smoothness priors (Tarvainen et al. 2002)
MAX_ARTIFACT_SHARE = 0.05
MIN_BEATS = 200
WINDOW_FROM_S = 600        # warm-up excluded
WINDOW_TO_S = 4500         # late-session fatigue excluded
MIN_CADENCE = 140          # steps/min; below is walking (section 1)
COUPLING_BAND = 0.05
SPEED_BAND = 0.10
SUB_WINDOW_S = 30
PRIOR_MEAN_S = 120
PRIOR_SURGE_S = 180
FLAT_GRADE = 0.02
MAX_ASCENT_M_PER_KM = 10.0


def _medians(rr: list[float]) -> list[float | None]:
    """21-beat local median (truncated at the ends), ignoring gap values (> 2 s)."""
    out: list[float | None] = []
    for k in range(len(rr)):
        vals = sorted(v for v in rr[max(0, k - MEDIAN_HALF):k + MEDIAN_HALF + 1] if v <= GAP_RR_S)
        if not vals:
            out.append(None)
            continue
        mid = len(vals) // 2
        out.append(vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2)
    return out


def _correct(rr: list[float]) -> tuple[list[tuple[float, int, int, int]], list[bool]]:
    """Corrected beats as (value, last raw index consumed, part i, of j parts), and a flag per raw beat."""
    med = _medians(rr)
    out: list[tuple[float, int, int, int]] = []
    flags = [False] * len(rr)
    k = 0
    while k < len(rr):
        v, m = rr[k], med[k]
        if v > GAP_RR_S or m is None or abs(v - m) <= BAND * m:
            out.append((v, k, 1, 1))
            k += 1
            continue
        flags[k] = True
        j = round(v / m)
        if j >= 2 and abs(v - j * m) <= BAND * m:
            out.extend((v / j, k, i, j) for i in range(1, j + 1))   # missed beat(s): split
            k += 1
        elif v < m and k + 1 < len(rr) and rr[k + 1] <= GAP_RR_S and abs(v + rr[k + 1] - m) <= BAND * m:
            flags[k + 1] = True
            out.append((v + rr[k + 1], k + 1, 1, 1))                # extra beat: merge with the next
            k += 2
        else:
            out.append((m, k, 1, 1))                                # other: replace by the median
            k += 1
    return out, flags


def correct_artifacts(rr: list[float]) -> tuple[list[float], list[bool]]:
    """Methods doc 6: returns (corrected RR, flag per raw beat). Values > 2 s are kept as gaps, unflagged."""
    out, flags = _correct(rr)
    return [v for v, *_ in out], flags


def smoothness_detrend(x: list[float], lam: float = LAMBDA) -> list[float]:
    """z = x - (I + lam^2 D2'D2)^-1 x, solved with a banded (pentadiagonal) LDL' factorization, O(N)."""
    n = len(x)
    if n < 3:
        return [0.0] * n
    l2 = lam * lam
    a0 = [1.0] * n                 # A[i][i]
    a1 = [0.0] * (n - 1)           # A[i][i+1]
    a2 = [0.0] * (n - 2)           # A[i][i+2]
    for r in range(n - 2):         # D2 row r is (1, -2, 1) at r, r+1, r+2
        a0[r] += l2
        a0[r + 1] += 4 * l2
        a0[r + 2] += l2
        a1[r] -= 2 * l2
        a1[r + 1] -= 2 * l2
        a2[r] += l2
    d = [0.0] * n
    e1 = [0.0] * n                 # L[i][i-1]
    e2 = [0.0] * n                 # L[i][i-2]
    for i in range(n):
        if i >= 2:
            e2[i] = a2[i - 2] / d[i - 2]
        if i >= 1:
            e1[i] = (a1[i - 1] - (e2[i] * d[i - 2] * e1[i - 1] if i >= 2 else 0.0)) / d[i - 1]
        d[i] = a0[i] - (e1[i] ** 2 * d[i - 1] if i >= 1 else 0.0) - (e2[i] ** 2 * d[i - 2] if i >= 2 else 0.0)
    y = [0.0] * n
    for i in range(n):
        y[i] = x[i] - (e1[i] * y[i - 1] if i >= 1 else 0.0) - (e2[i] * y[i - 2] if i >= 2 else 0.0)
    s = [0.0] * n
    for i in range(n - 1, -1, -1):
        s[i] = y[i] / d[i] - (e1[i + 1] * s[i + 1] if i + 1 < n else 0.0) - (e2[i + 2] * s[i + 2] if i + 2 < n else 0.0)
    return [xi - si for xi, si in zip(x, s, strict=True)]


def _ols_slope(xs: list[float], ys: list[float]) -> float:
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys, strict=True)) / sum((a - mx) ** 2 for a in xs)


def dfa_alpha1(rr: list[float], *, n_min: int = 4, n_max: int = 16, lam: float | None = LAMBDA) -> float | None:
    """Short-term DFA scaling exponent (Peng et al. 1995) after smoothness-priors detrending.

    `lam=None` skips the detrending (tests only). None for a constant series, a series shorter than one box
    of `n_max`, or any F(n) = 0.
    """
    if len(rr) < n_max or max(rr) == min(rr):
        return None
    z = smoothness_detrend(rr, lam) if lam is not None else list(rr)
    mean = sum(z) / len(z)
    y, acc = [], 0.0
    for v in z:
        acc += v - mean
        y.append(acc)
    log_n, log_f = [], []
    for n in range(n_min, n_max + 1):
        boxes = len(y) // n
        xbar = (n - 1) / 2
        sxx = n * (n * n - 1) / 12
        ss = 0.0
        for b in range(boxes):
            seg = y[b * n:(b + 1) * n]
            ybar = sum(seg) / n
            sxy = sum((i - xbar) * (v - ybar) for i, v in enumerate(seg))
            syy = sum((v - ybar) ** 2 for v in seg)
            ss += max(0.0, syy - sxy * sxy / sxx)
        f = math.sqrt(ss / (boxes * n))
        if f == 0:
            return None
        log_n.append(math.log10(n))
        log_f.append(math.log10(f))
    return _ols_slope(log_n, log_f)


def _coupled(hr: float, cadence: float) -> bool:
    """Cardio-locomotor coupling risk: HR within 5% of the step rate or of half of it."""
    return abs(hr - cadence) / cadence <= COUPLING_BAND or abs(hr - cadence / 2) / (cadence / 2) <= COUPLING_BAND


def windows(aligned: list[dict[str, Any]], *, size_s: int = 120, step_s: int = 5) -> dict[str, Any]:
    """Qualifying alpha1 windows of a training run (methods doc 6).

    `aligned` is `pace.fit.aligned_rr` output. Windows of `size_s` beat seconds every `step_s`, lying inside
    600..4500 s. Checks in order: gap or stop, walking cadence (< 140), not flat, unsteady speed in or before
    the window; then coupling risk (counted in `dropped_coupling` only for windows that passed the checks
    before it); then more than 5% artifacts or fewer than 200 corrected beats. "Outdoor" is a run-level check
    left to the caller.
    """
    result: dict[str, Any] = {"windows": [], "dropped_coupling": 0}
    if not aligned:
        return result
    times = [b["t_s"] for b in aligned]
    rr = [b["rr_s"] for b in aligned]
    entries, flags = _correct(rr)
    ctimes, cvals = [], []
    for v, k, i, j in entries:
        prev = times[k - 1] if k > 0 else times[k] - rr[k]
        ctimes.append(prev + (times[k] - prev) * i / j)
        cvals.append(v)
    flag_cum, bad_cum = [0], [0]
    for b, f in zip(aligned, flags, strict=True):
        flag_cum.append(flag_cum[-1] + f)
        bad_cum.append(bad_cum[-1] + (bool(b.get("gap")) or bool(b.get("stop")) or b["rr_s"] > GAP_RR_S))

    def dist_at(t: float) -> float | None:
        idx = bisect.bisect_right(times, t) - 1
        return aligned[idx]["dist"] if idx >= 0 else None

    def speed(a: float, b: float) -> float | None:
        da, db = dist_at(a), dist_at(b)
        return None if da is None or db is None else (db - da) / (b - a)

    t0 = math.ceil(WINDOW_FROM_S / step_s) * step_s
    while t0 + size_s <= WINDOW_TO_S and times[-1] >= t0 + size_s:
        t1 = t0 + size_s
        w = _window(aligned, t0, t1, times, flag_cum, bad_cum, ctimes, cvals, speed, result)
        if w is not None:
            result["windows"].append(w)
        t0 += step_s
    return result


def _window(aligned, t0, t1, times, flag_cum, bad_cum, ctimes, cvals, speed, result) -> dict[str, Any] | None:
    lo, hi = bisect.bisect_left(times, t0), bisect.bisect_left(times, t1)
    raw = hi - lo
    if raw == 0 or bad_cum[hi] - bad_cum[lo] > 0:
        return None
    cads = [b["cadence"] for b in aligned[lo:hi]]
    if any(c is None for c in cads):
        return None
    cadence = sum(cads) / raw
    if cadence < MIN_CADENCE:
        return None
    v = speed(t0, t1)
    if v is None or v <= 0 or not _flat(aligned[lo:hi], v * (t1 - t0)):
        return None
    for s in range(int((t1 - t0) // SUB_WINDOW_S)):
        sv = speed(t0 + s * SUB_WINDOW_S, t0 + (s + 1) * SUB_WINDOW_S)
        if sv is None or abs(sv - v) > SPEED_BAND * v:
            return None
    prior = speed(t0 - PRIOR_MEAN_S, t0)
    if prior is None or abs(prior - v) > SPEED_BAND * v:
        return None
    s = t0 - PRIOR_SURGE_S
    while s + SUB_WINDOW_S <= t0:
        sv = speed(s, s + SUB_WINDOW_S)
        if sv is None or sv > (1 + SPEED_BAND) * v:
            return None
        s += 5
    x = cvals[bisect.bisect_left(ctimes, t0):bisect.bisect_left(ctimes, t1)]
    if not x:
        return None
    hr = 60 / (sum(x) / len(x))
    if _coupled(hr, cadence):  # counted only for windows that passed every check above
        result["dropped_coupling"] += 1
        return None
    share = (flag_cum[hi] - flag_cum[lo]) / raw
    if share > MAX_ARTIFACT_SHARE or len(x) < MIN_BEATS:
        return None
    alpha1 = dfa_alpha1(x)
    if alpha1 is None:
        return None
    return {"alpha1": alpha1, "hr": hr, "speed": v, "cadence": cadence, "artifact_share": share, "beats": len(x),
            "t_mid": (t0 + t1) / 2}


def _flat(beats: list[dict[str, Any]], dist_m: float) -> bool:
    """Section 1: abs(elevation change) / distance <= 0.02 and total ascent <= 10 m per km."""
    alts = [b["alt"] for b in beats]
    if any(a is None for a in alts):
        return False
    ascent = sum(max(0.0, b - a) for a, b in zip(alts, alts[1:], strict=False))
    return abs(alts[-1] - alts[0]) / dist_m <= FLAT_GRADE and ascent <= MAX_ASCENT_M_PER_KM * dist_m / 1000
