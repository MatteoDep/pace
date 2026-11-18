"""RR artifact correction, smoothness-priors detrending, DFA alpha1 and windows (methods doc 6, 14.2)."""

import cmath
import math
import random
import statistics

import pytest

from pace import hrv, rules

SERIES = 50  # seeded series per statistical case (methods doc 14.2 test strategy)
N = 300


# --- artifact correction (14.2 worked example) ---

def test_correct_artifacts_worked_example() -> None:
    base = [0.400] * 30
    rr = (base + [0.800] + base + [0.150, 0.250] + base + [0.267, 0.400] + base + [3.891] + base
          + [0.430] + base)
    out, flags = hrv.correct_artifacts(rr)
    expected = (base + [0.400, 0.400] + base + [0.400] + base + [0.400, 0.400] + base + [3.891] + base
                + [0.430] + base)
    assert out == pytest.approx(expected, abs=1e-12)
    assert len(flags) == len(rr)
    flagged = [i for i, f in enumerate(flags) if f]
    assert flagged == [30, 61, 62, 93]  # 0.800, 0.150 + 0.250 (2 flags), 0.267; not 3.891, not 0.430
    assert rr[30] == 0.800 and rr[61] == 0.150 and rr[93] == 0.267


def test_correct_artifacts_triple_missed_beat_and_clean_series() -> None:
    rr = [0.4] * 25 + [1.2] + [0.4] * 25
    out, flags = hrv.correct_artifacts(rr)
    assert out == pytest.approx([0.4] * 25 + [0.4, 0.4, 0.4] + [0.4] * 25)
    assert sum(flags) == 1
    assert hrv.correct_artifacts([]) == ([], [])
    clean = [0.4 + 0.01 * math.sin(i) for i in range(50)]
    assert hrv.correct_artifacts(clean) == (clean, [False] * 50)


# --- smoothness-priors detrending ---

def _dense_detrend(x: list[float], lam: float) -> list[float]:
    """Reference: build I + lam^2 D2'D2 densely and solve with Gaussian elimination (partial pivoting)."""
    n = len(x)
    a = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for r in range(n - 2):
        v = {r: 1.0, r + 1: -2.0, r + 2: 1.0}
        for i, vi in v.items():
            for j, vj in v.items():
                a[i][j] += lam * lam * vi * vj
    m = [row[:] + [x[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            for k in range(c, n + 1):
                m[r][k] -= f * m[c][k]
    s = [0.0] * n
    for i in range(n - 1, -1, -1):
        s[i] = (m[i][n] - sum(m[i][k] * s[k] for k in range(i + 1, n))) / m[i][i]
    return [xi - si for xi, si in zip(x, s, strict=True)]


@pytest.mark.parametrize(("n", "lam"), [(3, 500.0), (4, 2.0), (12, 500.0), (25, 10.0)])
def test_smoothness_detrend_matches_dense_solve(n: int, lam: float) -> None:
    rng = random.Random(n)
    x = [0.4 + 0.02 * rng.gauss(0, 1) + 0.001 * i for i in range(n)]
    assert hrv.smoothness_detrend(x, lam) == pytest.approx(_dense_detrend(x, lam), abs=1e-9)


def test_smoothness_detrend_short_and_linear() -> None:
    assert hrv.smoothness_detrend([], 500) == []
    assert hrv.smoothness_detrend([0.4, 0.5], 500) == [0.0, 0.0]
    line = [0.3 + 0.01 * i for i in range(40)]
    assert hrv.smoothness_detrend(line) == pytest.approx([0.0] * 40, abs=1e-9)


# --- DFA alpha1: statistical properties (14.2 test strategy) ---

def _white(rng: random.Random, n: int = N) -> list[float]:
    return [rng.gauss(0, 1) for _ in range(n)]


def _power_noise(rng: random.Random, beta: float, n: int = N) -> list[float]:
    """Spectral synthesis: random phases, amplitude k^(-beta/2) at frequency index k."""
    terms = [(k ** (-beta / 2), rng.uniform(0, 2 * math.pi)) for k in range(1, n // 2 + 1)]
    out = []
    for t in range(n):
        z = sum(a * cmath.exp(1j * (2 * math.pi * k * t / n + ph)) for k, (a, ph) in enumerate(terms, 1))
        out.append(z.real)
    return out


def _brownian(rng: random.Random, n: int = N) -> list[float]:
    acc, out = 0.0, []
    for v in _white(rng, n):
        acc += v
        out.append(acc)
    return out


def _mean_alpha1(gen) -> float:
    vals = [hrv.dfa_alpha1(gen(random.Random(seed))) for seed in range(SERIES)]
    assert all(v is not None for v in vals)
    return statistics.fmean(vals)


def test_alpha1_white_noise() -> None:
    assert _mean_alpha1(_white) == pytest.approx(0.58, abs=0.03)


def test_alpha1_pink_noise() -> None:
    assert _mean_alpha1(lambda r: _power_noise(r, 1.0)) == pytest.approx(1.03, abs=0.05)


def test_alpha1_brownian_noise() -> None:
    # lambda 500 detrending removes the slowest Brownian components (methods doc 14.2, controller ruling)
    assert _mean_alpha1(_brownian) == pytest.approx(1.43, abs=0.05)


def test_alpha1_brownian_noise_without_detrending() -> None:
    vals = [hrv.dfa_alpha1(_brownian(random.Random(seed)), lam=None) for seed in range(SERIES)]
    assert statistics.fmean(vals) == pytest.approx(1.50, abs=0.05)


def test_alpha1_affine_invariance_and_constant() -> None:
    x = [0.4 + 0.02 * v for v in _power_noise(random.Random(7), 1.0)]
    a1 = hrv.dfa_alpha1(x)
    assert a1 is not None
    assert hrv.dfa_alpha1([2.5 * v + 0.3 for v in x]) == pytest.approx(a1, abs=1e-9)
    assert hrv.dfa_alpha1([0.4] * N) is None
    assert hrv.dfa_alpha1([0.4] * 10) is None  # too short for one box of 16


def test_alpha1_detrending_removes_a_linear_trend() -> None:
    raw_free, raw_trend = [], []
    for seed in range(SERIES):
        noise = [0.400 + 0.01 * v for v in _white(random.Random(seed))]
        trend = [v + 0.0005 * i for i, v in enumerate(noise)]
        free = hrv.dfa_alpha1(noise, lam=None)
        assert abs(hrv.dfa_alpha1(trend) - free) <= 0.01
        raw_free.append(free)
        raw_trend.append(hrv.dfa_alpha1(trend, lam=None))
    assert statistics.fmean(raw_trend) - statistics.fmean(raw_free) > 0.03


def test_alpha1_artifact_robustness() -> None:
    clean, corrected = [], []
    for seed in range(SERIES):
        rng = random.Random(1000 + seed)
        noise = _power_noise(rng, 0.6)
        sd = statistics.pstdev(noise)
        rr = [0.400 + 0.02 * v / sd for v in noise]
        merged, i = [], 0
        picks = set(rng.sample(range(0, N - 1, 2), round(0.03 * N)))  # 3% of beats, non-overlapping pairs
        while i < N:
            if i in picks:
                merged.append(rr[i] + rr[i + 1])
                i += 2
            else:
                merged.append(rr[i])
                i += 1
        fixed, flags = hrv.correct_artifacts(merged)
        assert sum(flags) >= len(picks)
        clean.append(hrv.dfa_alpha1(rr))
        corrected.append(hrv.dfa_alpha1(fixed))
    assert abs(statistics.fmean(corrected) - statistics.fmean(clean)) <= 0.02


# --- windows (methods doc 6 qualifying rules, 14.2) ---

def _beats(rr_fn, *, until: float = 722.0, speed: float = 3.0, cadence: float = 172.0, seed: int = 3,
           noise: float = 0.004) -> list[dict]:
    """Synthetic aligned beats: t_s on an exact grid of the mean RR (so counts are exact), RR with noise."""
    rng = random.Random(seed)
    out, t = [], 0.0
    k = 0
    while True:
        base = rr_fn(t)
        k += 1
        t = round(t + base, 9)
        if t > until + 1e-9:
            break
        sec = math.floor(t)
        out.append({"t_s": t, "rr_s": base + noise * rng.gauss(0, 1), "hr": round(60 / base), "speed": speed,
                    "dist": speed * sec, "alt": 10.0, "cadence": cadence, "gap": False, "stop": False})
    return out


def _only(res: dict) -> list[dict]:
    return [w for w in res["windows"] if w["t_mid"] == 660.0]


def test_windows_single_window_fields() -> None:
    res = hrv.windows(_beats(lambda t: 0.4))
    assert res["dropped_coupling"] == 0
    assert len(res["windows"]) == 1
    w = res["windows"][0]
    assert w["t_mid"] == 660.0 and w["beats"] == 300 and w["artifact_share"] == 0.0
    assert w["hr"] == pytest.approx(150, abs=1) and w["speed"] == pytest.approx(3.0)
    assert w["cadence"] == 172.0 and isinstance(w["alpha1"], float)


def _with_artifacts(count: int) -> list[dict]:
    beats = _beats(lambda t: 0.4)
    inside = [i for i, b in enumerate(beats) if 600 <= b["t_s"] < 720]
    assert len(inside) == 300
    for i in inside[5::len(inside) // count][:count]:
        beats[i]["rr_s"] = 0.300  # "other" artifact: replaced by the local median
    return beats


def test_windows_artifact_share_limit() -> None:
    used = _only(hrv.windows(_with_artifacts(12)))
    assert len(used) == 1 and used[0]["artifact_share"] == pytest.approx(0.04) and used[0]["beats"] == 300
    assert _only(hrv.windows(_with_artifacts(18))) == []


def test_windows_too_few_beats() -> None:
    assert hrv.windows(_beats(lambda t: 120 / 190))["windows"] == []
    assert len(hrv.windows(_beats(lambda t: 120 / 205))["windows"]) == 1


@pytest.mark.parametrize(("hr", "cadence", "kept"), [(176, 185, False), (160, 172, True), (88, 172, False)])
def test_windows_coupling(hr: int, cadence: float, kept: bool) -> None:
    res = hrv.windows(_beats(lambda t: 60 / hr, cadence=cadence, noise=0.002))
    assert len(res["windows"]) == (1 if kept else 0)
    # counted even at HR 88, whose 176 beats would also fail the 200-beat minimum checked after coupling
    assert res["dropped_coupling"] == (0 if kept else 1)


def test_windows_coupling_counted_only_after_earlier_checks() -> None:
    hill = _beats(lambda t: 60 / 176, cadence=185, noise=0.002)
    for b in hill:
        b["alt"] = 10.0 + 0.03 * b["dist"]
    walk = _beats(lambda t: 60 / 132, cadence=132, noise=0.002)  # HR equals the step rate, but walking
    stopped = _beats(lambda t: 60 / 176, cadence=185, noise=0.002)
    next(b for b in stopped if b["t_s"] >= 650)["stop"] = True
    for beats in (hill, walk, stopped):
        assert hrv.windows(beats) == {"windows": [], "dropped_coupling": 0}
    many = _beats(lambda t: 60 / 176, until=760, cadence=185, noise=0.002)
    assert hrv.windows(many)["dropped_coupling"] == 8  # windows at 600, 605, ..., 635


def test_windows_skip_gap_stop_walking_and_warmup() -> None:
    beats = _beats(lambda t: 0.4)
    beats[1600]["gap"] = True
    assert hrv.windows(beats)["windows"] == []
    beats = _beats(lambda t: 0.4)
    beats[1600]["stop"] = True
    assert hrv.windows(beats)["windows"] == []
    beats = _beats(lambda t: 0.4)
    beats[1600]["rr_s"] = 2.5  # raw RR > 2 s is a gap
    assert hrv.windows(beats)["windows"] == []
    assert hrv.windows(_beats(lambda t: 0.4, cadence=rules.MIN_CADENCE - 10))["windows"] == []  # section 1 walking
    assert hrv.windows(_beats(lambda t: 0.4, until=700))["windows"] == []  # no window fits in 600..4500 s


def test_windows_speed_rules() -> None:
    def surge(start: float, end: float, factor: float) -> list[dict]:
        beats = _beats(lambda t: 0.4)
        extra = 0.0
        prev = 0
        for b in beats:
            sec = math.floor(b["t_s"])
            if sec != prev:
                if start <= prev < end:
                    extra += 3.0 * (factor - 1)
                prev = sec
            b["dist"] = 3.0 * sec + extra
        return beats

    assert hrv.windows(surge(500, 530, 1.3))["windows"] == []    # 30 s faster than 1.10 x in the 180 s before
    assert len(hrv.windows(surge(380, 410, 1.3))["windows"]) == 1  # earlier than the 180 s before the window
    assert hrv.windows(surge(640, 670, 1.3))["windows"] == []    # a 30 s sub-window off by more than 10%
    assert hrv.windows(surge(480, 600, 0.85))["windows"] == []   # previous 120 s mean speed off by more than 10%


def test_windows_hill_is_not_flat() -> None:
    # methods doc section 1 (grade): net grade above MAX_GRADE
    beats = _beats(lambda t: 0.4)
    for b in beats:
        b["alt"] = 10.0 + 1.5 * rules.MAX_GRADE * b["dist"]
    assert hrv.windows(beats)["windows"] == []


@pytest.mark.parametrize("noise", ["uniform", "gauss"])
def test_windows_flat_run_with_altitude_noise_is_flat(noise: str) -> None:
    # methods doc section 1 (grade): GPS altitude noise on a flat road is not ascent (hysteresis)
    rng = random.Random(11)
    beats = _beats(lambda t: 0.4)
    for b in beats:
        b["alt"] = 10.0 + (rng.uniform(-0.2, 0.2) if noise == "uniform" else rng.gauss(0, 0.3))
    assert len(hrv.windows(beats)["windows"]) == 1


def test_beats_flat_uses_smoothing(monkeypatch) -> None:
    # methods doc section 1: altitude is smoothed once per run by beat time (beats ~0.4 s apart), then checked
    rng = random.Random(13)
    beats = _beats(lambda t: 0.4)
    per_second: dict[int, float] = {}
    for b in beats:                                   # GPS altitude: one jittered value per second (sd 1.5 m)
        b["alt"] = per_second.setdefault(math.floor(b["t_s"]), 10.0 + rng.gauss(0, 1.5))
    inside = [b for b in beats if 600 <= b["t_s"] < 720]
    assert not hrv._flat([b["alt"] for b in inside], 3.0 * 120)        # raw: the jitter reads as climbing
    calls = []
    real = hrv.smooth_altitude

    def spy(times, alts, **kw):
        calls.append(times)
        return real(times, alts, **kw)

    monkeypatch.setattr(hrv, "smooth_altitude", spy)
    assert len(_only(hrv.windows(beats))) == 1
    assert calls == [[b["t_s"] for b in beats]]                       # once, on beat times


def test_windows_gentle_climb_with_noise_is_not_flat() -> None:
    # methods doc section 1: grade under MAX_GRADE but ascent 1.5 x MAX_ASCENT_M_PER_KM is still a climb
    rng = random.Random(12)
    beats = _beats(lambda t: 0.4)
    slope = 1.5 * rules.MAX_ASCENT_M_PER_KM / 1000
    assert slope < rules.MAX_GRADE
    for b in beats:
        b["alt"] = 10.0 + slope * b["dist"] + rng.gauss(0, 0.3)
    assert hrv.windows(beats)["windows"] == []
