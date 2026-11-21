"""Central data rules (methods doc section 1) and the shared ascent helper."""

import random

import pytest

from pace import config, fit, hrv, rules, thresholds, zones


def _per_km(alts: list[float], dist_m: float) -> float:
    return rules.ascent_m(alts) / (dist_m / 1000)


# --- ascent with hysteresis (methods doc section 1, "Grade") ---

def test_ascent_ignores_uniform_altitude_noise() -> None:
    rng = random.Random(1)
    alts = [100 + rng.uniform(-0.2, 0.2) for _ in range(3600)]   # 1 Hz, 3 m/s: 10.8 km of flat road
    assert rules.ascent_m(alts) == 0.0


def test_ascent_ignores_gaussian_altitude_noise() -> None:
    rng = random.Random(2)
    alts = [100 + rng.gauss(0, 0.3) for _ in range(3600)]
    assert _per_km(alts, 3.0 * 3600) < 0.1 * rules.MAX_ASCENT_M_PER_KM


def test_ascent_keeps_a_real_climb() -> None:
    rng = random.Random(3)
    slope = 1.5 * rules.MAX_ASCENT_M_PER_KM / 1000            # 15 m per km
    alts = [slope * 3.0 * t + rng.gauss(0, 0.3) for t in range(3600)]
    assert _per_km(alts, 3.0 * 3600) == pytest.approx(1.5 * rules.MAX_ASCENT_M_PER_KM, rel=0.1)


def test_ascent_counts_a_loop_above_the_threshold_only() -> None:
    def loop(h: float) -> list[float]:
        up = [h * k / 10 for k in range(11)]
        return up + up[::-1]

    above = rules.ASCENT_HYSTERESIS_M + 0.5
    assert rules.ascent_m(loop(above)) == pytest.approx(above)
    assert rules.ascent_m(loop(rules.ASCENT_HYSTERESIS_M - 0.5)) == 0.0


def test_ascent_profile_is_cumulative_and_skips_missing() -> None:
    prof = rules.ascent_profile([0.0, None, 1.0, 3.0, 2.5, 5.0])
    assert len(prof) == 6 and prof[:3] == [0.0, 0.0, 0.0]
    assert prof[3] == pytest.approx(3.0) and prof[4] == pytest.approx(3.0) and prof[5] == pytest.approx(5.0)
    assert rules.ascent_m([]) == 0.0 and rules.ascent_m([None]) == 0.0


# --- one place for the numbers ---

def test_modules_use_the_central_rules() -> None:
    assert thresholds.MAX_ASCENT_M_PER_KM is rules.MAX_ASCENT_M_PER_KM
    assert hrv.MAX_ASCENT_M_PER_KM is rules.MAX_ASCENT_M_PER_KM
    assert thresholds.MIN_CADENCE is hrv.MIN_CADENCE is rules.MIN_CADENCE
    assert fit.STOP_GAP_S is thresholds.STOP_GAP_S is rules.STOP_GAP_S
    assert fit.GAP_RR_S is hrv.GAP_RR_S is rules.GAP_RR_S
    assert zones.HARD_EFFORT_TE is thresholds.HARD_EFFORT_TE is rules.HARD_EFFORT_TE
    assert thresholds.DRIFT_MAX_GAIN is rules.DRIFT_MAX_GAIN
    assert thresholds.smooth_altitude is hrv.smooth_altitude is rules.smooth_altitude


def test_h5_run_window_and_segment_move_together() -> None:
    # methods doc 7 and 14.7: a list row shorter than warm-up + minimum segment can never give H5 evidence
    assert rules.H5_RUN_S[0] >= rules.H5_START_S + rules.H5_MIN_S
    assert rules.H5_RUN_S[1] > rules.H5_RUN_S[0]
    assert rules.H5_RUN_S == (3600, 9000)          # 60-150 min, user decision 2026-11-18
    assert rules.DRIFT_MAX_GAIN == 15.0            # list-row pre-filter, user decision 2026-11-18


def test_smooth_altitude_centered_mean() -> None:
    # methods doc section 1: centered ALT_SMOOTH_S moving mean over a time span
    assert rules.ALT_SMOOTH_S == 61
    n = 200
    t = [float(i) for i in range(n)]
    assert rules.smooth_altitude(t, [12.3] * n) == pytest.approx([12.3] * n)
    assert rules.smooth_altitude([], []) == []
    spike = [0.0] * n
    spike[100] = 10.0
    out = rules.smooth_altitude(t, spike)
    assert out[100] == pytest.approx(10 / 61) and out[70] == pytest.approx(10 / 61) and out[130] == pytest.approx(10 / 61)
    assert out[69] == 0.0 and out[131] == 0.0
    # ends: the window is truncated, not padded
    ramp = rules.smooth_altitude(t, t)
    assert ramp[0] == pytest.approx(15.0) and ramp[n - 1] == pytest.approx(184.0) and ramp[100] == pytest.approx(100.0)
    # None stays None and is skipped in the neighbours' means
    holes: list[float | None] = [0.0] * n
    holes[100], holes[101] = None, 61.0
    out = rules.smooth_altitude(t, holes)
    assert out[100] is None and out[99] == pytest.approx(61 / 60) and out[131] == pytest.approx(61 / 61)
    assert rules.smooth_altitude([0.0, 1.0], [None, None]) == [None, None]
    # width_s: a 1 s window on a 1 Hz series keeps every sample
    assert rules.smooth_altitude(t, spike, width_s=1) == spike
    # non-uniform times (beats about 0.4 s apart): the time span decides, not a count of 61 samples
    bt = [round(0.4 * k, 9) for k in range(1000)]
    bspike = [0.0] * 1000
    bspike[500] = 10.0
    inside = sum(abs(x - bt[500]) <= 30.5 for x in bt)
    assert inside == 153
    assert rules.smooth_altitude(bt, bspike)[500] == pytest.approx(10 / inside)
    with pytest.raises(ValueError):
        rules.smooth_altitude([0.0], [])


def test_reason_strings_follow_the_constants(monkeypatch) -> None:
    assert f"{rules.MAX_ASCENT_M_PER_KM:g} m per km" in thresholds.ASCENT_REASON
    assert f"{rules.MIN_CADENCE}" in thresholds.NO_WINDOW_REASON and f"{rules.WARMUP_S} s" in thresholds.NO_WINDOW_REASON


# --- guard: no test writes to the real ~/.pace ---

def test_fit_dir_guard_refuses_the_real_pace_home(tmp_path, monkeypatch) -> None:
    from tests import conftest
    fake_real = tmp_path / "real"
    monkeypatch.setattr(conftest, "REAL_PACE_HOME", fake_real)    # stand-in, so a broken guard writes nothing real
    monkeypatch.setattr(config, "PACE_HOME", fake_real)
    with pytest.raises(AssertionError, match="real PACE_HOME"):
        fit.no_rr(1)
    assert not fake_real.exists()
