import copy
import json
import math
import random
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from pace import hrv, rules, zones
from pace import thresholds as th
from pace.workout import build_workout

FIX = Path(__file__).parent / "fixtures"


def table(duration, speed_fn, hr_fn=lambda t: 150, cadence=170, alt=0.0):
    """1 Hz table: row t carries the speed of second (t-1, t]; dist is cumulative."""
    rows, dist = [], 0.0
    for t in range(duration + 1):
        v = speed_fn(t) if t else 0.0
        dist += v
        rows.append({"t": t, "hr": hr_fn(t), "speed": v, "dist": dist, "alt": alt, "cadence": cadence})
    return rows


def easy(t):
    return 2.0


def effort(start, end, v):
    return lambda t: v if start < t <= end else 2.0


def run(rid, tbl, src="strap"):
    return {"id": rid, "hr_source": src, "table": tbl}


# --- hr_source / samples ---

def test_hr_source_fixtures():
    strap = json.loads((FIX / "sensors_strap.json").read_text())
    wrist = json.loads((FIX / "sensors_wrist.json").read_text())
    assert th.hr_source(strap) == "strap"
    assert th.hr_source(wrist) == "wrist"
    assert th.hr_source({}) == "wrist"
    assert th.hr_source({"metadataDTO": {"sensors": [{"bleDeviceType": "CADENCE"}]}}) == "wrist"
    assert th.hr_source({"metadataDTO": {"sensors": [{"antplusDeviceType": "HEART_RATE"}]}}) == "strap"


def test_samples_from_details_raw_timestamps():
    descs = ["directTimestamp", "directHeartRate", "directSpeed", "sumDistance", "directElevation", "directRunCadence"]
    payload = {
        "metricDescriptors": [{"key": k, "metricsIndex": i} for i, k in enumerate(descs)],
        "activityDetailMetrics": [
            {"metrics": [1_700_000_001_000, 141, 3.0, 3.0, 10.0, 88.0]},
            {"metrics": [1_700_000_000_000, 140, 2.0, 0.0, 10.0, 87.0]},
            {"metrics": [1_700_000_002_000, None, 3.0, 6.0, None, None]},
        ],
    }
    rows = th.samples(payload)
    assert [r["t"] for r in rows] == [0, 1, 2]
    assert rows[0] == {"t": 0, "hr": 140, "speed": 2.0, "dist": 0.0, "alt": 10.0, "cadence": 174.0}
    assert rows[1]["cadence"] == 176.0
    assert rows[2]["hr"] is None and rows[2]["cadence"] is None
    assert th.samples({}) == []


# --- moving_samples ---

def _slow(n_slow):
    rows = []
    for t in range(40):
        slow = 10 <= t < 10 + n_slow
        rows.append({"t": t, "hr": 140, "speed": 0.8 if slow else 3.0, "dist": float(t), "alt": 0.0, "cadence": 170})
    return rows


def test_moving_samples_stop_of_12_s_is_removed():
    # methods doc section 1: a slow stretch longer than STOP_SLOW_S is a stop
    n = rules.STOP_SLOW_S + 2
    kept, excluded = th.moving_samples(_slow(n))
    assert excluded == n and len(kept) == 40 - n
    assert all(not (10 <= r["t"] < 10 + n) for r in kept)


def test_moving_samples_9_s_is_not_a_stop():
    kept, excluded = th.moving_samples(_slow(rules.STOP_SLOW_S - 1))    # methods doc section 1
    assert excluded == 0 and len(kept) == 40


def _gapped(skip):
    return [{"t": t, "hr": 140, "speed": 3.0, "dist": 3.0 * t, "alt": 0.0, "cadence": 170}
            for t in range(30) if t not in skip]


def test_moving_samples_timestamp_gap_over_5_s_is_a_stop():
    kept, excluded = th.moving_samples(_gapped(range(10, 16)))   # t 9 -> 16: a 7 s gap
    assert len(kept) == 24 and excluded == 6                      # the hole is reported, not filled


def test_moving_samples_gap_of_5_s_is_not_a_stop():
    assert th.moving_samples(_gapped(range(10, 14)))[1] == 0      # t 9 -> 14


# --- max_hr ---

def hr_rows(values, start=0):
    return [{"t": start + i, "hr": v} for i, v in enumerate(values)]


def test_max_hr_ignores_first_300_s_and_single_spike():
    rows = hr_rows([200] * 20 + [150] * 280)             # 200 bpm only in the first 300 s
    rows += hr_rows([160, 160, 185, 160, 160, 170, 171, 172, 173, 174], start=300)
    # the 185 single spike is removed by the median; best 5-s median is 172 (171..174 window: 172 at 6..10)
    assert th.max_hr(rows) == 172


def test_max_hr_drops_out_of_range_and_none():
    rows = hr_rows([150] * 300) + hr_rows([250, 250, 250, 250, 250, 20, 20, 20, 20, 20, 155, 155, 155, 155, 155], start=300)
    assert th.max_hr(rows) == 155
    assert th.max_hr(hr_rows([None] * 400)) is None
    assert th.max_hr([]) is None
    assert th.max_hr(hr_rows([150] * 200)) is None       # nothing after 300 s


# --- H1 ---

def h1_runs():
    return [
        run(1, table(1200, effort(600, 780, 5.0))),
        run(2, table(1200, effort(600, 1020, 1860 / 420))),
        run(3, table(1400, effort(600, 1320, 4.25))),
    ]


def test_h1_worked_example():
    r = th.critical_speed(h1_runs(), prs=None)
    assert r["status"] == "ok"
    assert r["cs_mps"] == pytest.approx(4.0, abs=1e-6)
    assert r["d_prime_m"] == pytest.approx(180, abs=1e-4)
    assert r["pace_s_per_km"] == 250
    assert [w["distance_m"] for w in r["windows"]] == pytest.approx([900, 1860, 3060])
    assert r["confidence"] == "medium"
    assert [x["id"] for x in r["runs"]] == [1, 2, 3] and all(x["used"] for x in r["runs"])


def test_h1_single_run_is_low_confidence():
    def v(t):
        if 600 < t <= 780:
            return 5.0
        if 1000 < t <= 1420:
            return 1860 / 420
        if 1700 < t <= 2420:
            return 4.25
        return 2.0
    r = th.critical_speed([run(1, table(2500, v))], prs=None)
    assert r["status"] == "ok" and r["confidence"] == "low"
    assert any("2 different runs" in x for x in r["reasons"])
    assert r["cs_mps"] == pytest.approx(4.0, abs=1e-6)


def test_h1_gate_rejects_flat_fit():
    r = th.critical_speed([run(1, table(1500, effort(600, 1400, 4.0)))], prs=None)
    assert r["status"] == "insufficient_data" and r["cs_mps"] is None
    assert any("D'" in x for x in r["reasons"])


def test_h1_warmup_hills_walking_and_stops_excluded():
    # a fast burst inside the first 600 s, a hilly one and a walking-cadence one never count
    hot = table(900, effort(100, 280, 6.0))
    hill = table(1400, effort(600, 780, 5.0))
    for r in hill:
        r["alt"] = r["dist"] * 1.5 * rules.MAX_GRADE  # methods doc section 1: grade above the flat limit
    walk = table(1400, effort(600, 780, 5.0), cadence=rules.MIN_CADENCE - 20)   # section 1: walking
    out = th.critical_speed([run(1, hot), run(2, hill), run(3, walk)], prs=None)
    assert out["status"] == "insufficient_data"
    used = {x["id"]: x["used"] for x in out["runs"]}
    assert used == {1: True, 2: False, 3: False}
    assert all(x.get("reason") for x in out["runs"] if not x["used"])
    assert [w["distance_m"] for w in out["windows"]] == pytest.approx([360])   # the 6 m/s burst before 600 s is ignored


def test_h1_window_cannot_span_a_stop():
    def v(t):
        return 0.0 if 700 < t <= 740 else 5.0
    rows = table(1500, v)
    for r in rows:
        if 700 < r["t"] <= 740:
            r["speed"] = 0.0
    out = th.critical_speed([run(1, rows)], prs=None)
    # no 720 s window exists without the stop (600..700 and 740..1500 give 100 s and 760 s: one 720 window fits after the stop)
    w = {x["T"]: x for x in out.get("windows", [])}
    assert w[720]["start_t"] >= 740


def test_h1_wrist_runs_are_used_and_prs_add_points():
    runs = h1_runs()
    for r in runs:
        r["hr_source"] = "wrist"
    assert th.critical_speed(runs, prs=None)["status"] == "ok"
    out = th.critical_speed(h1_runs(), prs=[{"duration_s": 1200, "distance_m": 4800}, {"duration_s": 60, "distance_m": 400}])
    assert out["points"] == 4                          # the 60 s record is outside 120..1200


# --- H2 ---

def seg_run(rid, speeds, src="strap"):
    def v(t):
        i = (t - 1 - 600) // 360
        return speeds[i] if t > 600 and 0 <= i < len(speeds) else 2.5

    def hr(t):
        return 60 + 25 * v(t) if t > 600 else 120
    return run(rid, table(600 + 360 * len(speeds) + 60, v, hr), src)


def h2_runs():
    return [
        seg_run(1, [2.8, 3.0, 3.2, 3.4]),
        seg_run(2, [3.6, 3.8, 2.8, 3.0]),
        seg_run(3, [3.2, 3.4, 3.6, 3.8]),
    ]


def test_h2_worked_example():
    r = th.hr_speed(h2_runs())
    assert r["status"] == "ok" and r["n"] == 12
    assert r["a"] == pytest.approx(60, abs=1e-6) and r["b"] == pytest.approx(25, abs=1e-6)
    assert r["see"] == pytest.approx(0, abs=1e-6)
    assert r["v_min"] == pytest.approx(2.8) and r["v_max"] == pytest.approx(3.8)
    assert all(x["used"] for x in r["runs"])


def test_lt2_from_cs_worked_example():
    cs = th.critical_speed(h1_runs(), prs=None)
    h2 = th.hr_speed(h2_runs())
    lt2 = th.lt2_from_cs(cs, h2)
    assert lt2["hr"] == 160 and lt2["pace_s_per_km"] == 250 and lt2["confidence"] == "medium"
    short = dict(h2, v_max=3.5)
    assert th.lt2_from_cs(cs, short)["hr"] is None      # 3.5 < 0.9 x 4.0
    assert th.lt2_from_cs(cs, dict(h2, see=6.0))["confidence"] == "low"


def test_h2_wrist_runs_skipped_with_reason():
    runs = h2_runs()
    runs[2]["hr_source"] = "wrist"
    r = th.hr_speed(runs)
    assert r["status"] == "insufficient_data" and r["n"] == 8
    assert r["runs"][2] == {"id": 3, "used": False, "reason": "wrist HR (use --include-wrist)"}
    assert any("2 different runs" in x for x in r["reasons"])
    inc = th.hr_speed(runs, include_wrist=True)
    assert inc["status"] == "ok" and inc["n"] == 12 and inc["runs"][2]["used"]


def test_h2_unsteady_segment_rejected_and_insufficient_reasons():
    def v(t):
        return 3.0 + (1.0 if (t // 120) % 2 else 0.0)   # 120 s blocks: 60 s sub-windows differ by > 5%
    r = th.hr_speed([run(1, table(2000, v))])
    assert r["status"] == "insufficient_data" and r["n"] == 0
    assert r["runs"][0]["used"] is False and r["reasons"]


def test_h2_needs_a_speed_range():
    runs = [seg_run(i, [3.0] * 4) for i in (1, 2, 3)]
    r = th.hr_speed(runs)
    assert r["status"] == "insufficient_data" and any("0.5 m/s" in x for x in r["reasons"])


# --- H3 ---

def test_h3_worked_example():
    cs = th.critical_speed(h1_runs(), prs=None)
    r = th.lt1_from_cs(cs, th.hr_speed(h2_runs()))
    assert r["status"] == "ok" and r["hr"] == 142 and r["pace_s_per_km"] == 304
    assert r["confidence"] == "low"


def test_h3_without_h2_or_cs():
    cs = th.critical_speed(h1_runs(), prs=None)
    r = th.lt1_from_cs(cs, None)
    assert r["status"] == "ok" and r["hr"] is None and r["pace_s_per_km"] == 304
    bad = th.lt1_from_cs({"status": "insufficient_data", "reasons": ["x"]}, None)
    assert bad["status"] == "insufficient_data" and bad["reasons"]


# --- H5 (task 3b): candidates, segment, bounds ---

def lrow(aid, start, type_key="running", event="uncategorized", te=3.0):
    return {"activityId": aid, "startTimeGMT": start.strftime("%Y-%m-%d %H:%M:%S"),
            "activityType": {"typeKey": type_key}, "eventType": {"typeKey": event},
            "aerobicTrainingEffect": te}


T0 = datetime(2026, 11, 1, 6, 0, 0)


def test_h5_candidate_24h_rule_race_and_training_effect():
    race = lrow(1, T0, event="race")
    hard = lrow(2, T0 + timedelta(days=5), te=4.2)
    soft = lrow(3, T0 + timedelta(days=9), te=3.9)
    after20 = lrow(10, T0 + timedelta(hours=20))
    after30 = lrow(11, T0 + timedelta(hours=30))
    hard20 = lrow(12, T0 + timedelta(days=5, hours=20))
    hard30 = lrow(13, T0 + timedelta(days=5, hours=30))
    soft20 = lrow(14, T0 + timedelta(days=9, hours=20))
    rows = [race, hard, soft, after20, after30, hard20, hard30, soft20]
    assert th.drift_candidate(after20, rows) is False
    assert th.drift_candidate(after30, rows) is True
    assert th.drift_candidate(hard20, rows) is False
    assert th.drift_candidate(hard30, rows) is True
    assert th.drift_candidate(soft20, rows) is True      # TE 3.9 < 4.0
    assert th.drift_candidate(race, rows) is True        # never excluded by itself
    assert th.drift_candidate(lrow(20, T0 - timedelta(hours=5)), rows) is True   # the race is later, not before


def test_h5_candidate_outdoor_running_only():
    assert th.drift_candidate(lrow(1, T0, type_key="treadmill_running"), []) is False
    assert th.drift_candidate(lrow(2, T0, type_key="trail_running"), []) is False
    assert th.drift_candidate(lrow(3, T0, type_key="cycling"), []) is False
    assert th.drift_candidate(lrow(4, T0), []) is True


def steady_table(extra, hr=140, speed=3.0, **kw):
    return table(900 + extra, lambda t: 2.0 if t <= 900 else speed, hr_fn=lambda t: hr, **kw)


def test_h5_segment_evidence_row():
    ev = th.drift_segment(run(7, steady_table(3000, hr=140)), fit_records=None)
    assert ev["id"] == 7 and ev["bounds"] == [900, 3900] and ev["length_s"] == 3000
    assert ev["h"] == 140 and ev["drift"] == pytest.approx(0.0, abs=1e-9) and ev["label"] == "below"
    assert ev["temperature"] == "unknown"


def test_h5_segment_drift_and_label():
    tbl = table(3900, lambda t: 2.0 if t <= 900 else 3.0, hr_fn=lambda t: 140 if t <= 2400 else 150)
    ev = th.drift_segment(run(1, tbl), fit_records=None)
    assert ev["h"] == 140 and ev["drift"] == pytest.approx((150 / 140 - 1) * 100, abs=0.01)
    assert ev["label"] == "above"


def test_h5_segment_40_min_rejected():
    # methods doc section 7: a segment shorter than H5_MIN_S is rejected
    out = th.drift_segment(run(2, steady_table(rules.H5_MIN_S - 300)), fit_records=None)
    assert out["id"] == 2 and out["rejected"] == f"segment shorter than {rules.H5_MIN_S} s"


def test_h5_segment_hot_fit_temperature_matched_on_elapsed_t():
    tbl = steady_table(3000)
    hot = [{"t": t, "temp": 27} for t in range(900, 3901)] + [{"t": t, "temp": 10} for t in range(0, 900)]
    assert th.drift_segment(run(3, tbl), fit_records=hot)["rejected"] == "mean temperature >= 24 C"
    # the same hot records but outside the segment (warm-up only) do not count
    cool = [{"t": t, "temp": 27} for t in range(0, 800)] + [{"t": t, "temp": 15} for t in range(900, 3901)]
    ev = th.drift_segment(run(3, tbl), fit_records=cool)
    assert ev["temperature"] == 15
    # records present but none with temperature: unknown
    assert th.drift_segment(run(3, tbl), fit_records=[{"t": 1000, "hr": 140}])["temperature"] == "unknown"


def test_h5_segment_stop_excluded_and_reported():
    spd = lambda t: 2.0 if t <= 900 else (0.0 if 1000 <= t < 1020 else 3.0)  # noqa: E731
    out = th.drift_segment(run(4, table(5100, spd)), fit_records=None)
    assert out["excluded_s"] == 20
    assert out["bounds"][0] >= 1020 and out["drift"] == pytest.approx(0.0, abs=1e-9)
    # a stop in the middle that leaves no 2700 s side: rejected, not a wrong drift
    spd2 = lambda t: 2.0 if t <= 900 else (0.0 if 2000 <= t < 2020 else 3.0)  # noqa: E731
    rej = th.drift_segment(run(5, table(4500, spd2)), fit_records=None)
    assert "rejected" in rej and rej["excluded_s"] == 20


def noisy(tbl, sigma=0.3, seed=7, slope=0.0):
    """GPS-like altitude: `slope` m per m of distance plus Gaussian noise of `sigma` m on every sample."""
    rng = random.Random(seed)
    for r in tbl:
        r["alt"] = 100.0 + slope * r["dist"] + rng.gauss(0, sigma)
    return tbl


def test_h5_segment_hilly_rejected():
    # methods doc section 1/7: grade under MAX_GRADE per window but 1.5 x MAX_ASCENT_M_PER_KM of ascent
    tbl = noisy(table(3900, lambda t: 2.0 if t <= 900 else 3.0), slope=1.5 * rules.MAX_ASCENT_M_PER_KM / 1000)
    out = th.drift_segment(run(6, tbl), fit_records=None)
    assert "rejected" in out


def test_noisy_flat_altitude_is_flat_everywhere():
    # methods doc section 1 (grade): altitude noise (sigma 0.3 m, +/-0.2 m) on a flat road is not ascent
    ev = th.drift_segment(run(7, noisy(steady_table(3000))), fit_records=None)
    assert "rejected" not in ev and ev["length_s"] == 3000
    rng = random.Random(3)
    tbl = steady_table(3000)
    for r in tbl:
        r["alt"] = 50.0 + rng.uniform(-0.2, 0.2)
    assert "rejected" not in th.drift_segment(run(8, tbl), fit_records=None)
    cs = th.critical_speed([run(r["id"], noisy(r["table"], seed=r["id"])) for r in h1_runs()], prs=None)
    assert cs["status"] == "ok" and cs["cs_mps"] == pytest.approx(4.0, abs=1e-6)
    f1 = th.drift(noisy(f1_table(3.20, 140, 3.18, 146)), laps=F1_LAPS, treadmill=False)
    assert f1["status"] == "ok" and f1["verdict"] == "at LT1"


def test_noisy_climb_rejects_f1_and_f2():
    # methods doc sections 8 and 9: total ascent above MAX_ASCENT_M_PER_KM rejects the test
    slope = 1.5 * rules.MAX_ASCENT_M_PER_KM / 1000
    f1 = th.drift(noisy(f1_table(3.20, 140, 3.18, 146), slope=slope), laps=F1_LAPS, treadmill=False)
    assert f1["status"] == "rejected" and th.ASCENT_REASON in f1["reasons"]
    f2 = th.time_trial(noisy(tt_table(), slope=slope), laps=TT_LAPS)
    assert f2["status"] == "rejected" and th.ASCENT_REASON in f2["reasons"]


def gps_flat(tbl, seed=5, hill=None):
    """GPS-like altitude on flat ground: a slow sine (3 m amplitude, 2 km wavelength) plus iid 1 s jitter (sd 1.5 m);
    `hill` = (start_m, rise_m, length_m) adds a real climb."""
    rng = random.Random(seed)
    for r in tbl:
        d = r["dist"]
        r["alt"] = 100.0 + 3.0 * math.sin(2 * math.pi * d / 2000) + rng.gauss(0, 1.5)
        if hill:
            start, rise, length = hill
            r["alt"] += rise * min(max((d - start) / length, 0.0), 1.0)
    return tbl


def _windows_300(blk, step=60):
    return [(i, blk.window_ok(i, i + 300)) for i in range(0, blk.n - 300, step)]


def test_noisy_flat_block_passes(monkeypatch):
    # methods doc section 1: GPS jitter of metres per second on a flat run is smoothed away before grade and ascent
    tbl = gps_flat(table(3900, lambda t: 3.0))
    with monkeypatch.context() as m:
        m.setattr(th, "smooth_altitude", lambda times, alts, **kw: list(alts))
        (raw,), _ = th._blocks(tbl)
        assert sum(not ok for _, ok in _windows_300(raw)) > 0.5 * len(_windows_300(raw))   # the noise bites
        assert "rejected" in th.drift_segment(run(31, tbl), fit_records=None)
    (blk,), _ = th._blocks(tbl)
    oks = _windows_300(blk, step=1)
    assert sum(ok for _, ok in oks) >= 0.95 * len(oks)
    ev = th.drift_segment(run(31, tbl), fit_records=None)
    assert "rejected" not in ev and ev["length_s"] >= rules.H5_MIN_S
    # F1 and F2 terrain checks read the same smoothed series
    f1 = th.drift(gps_flat(f1_table(3.20, 140, 3.18, 146)), laps=F1_LAPS, treadmill=False)
    assert f1["status"] == "ok"
    f2 = th.time_trial(gps_flat(tt_table()), laps=TT_LAPS)
    assert th.ASCENT_REASON not in f2.get("reasons", [])


def test_real_hill_fails():
    # methods doc section 1: smoothing removes jitter, not a real climb (30 m over 1 km at 3 m/s: t 2000..2333)
    tbl = gps_flat(table(3900, lambda t: 3.0), hill=(6000.0, 30.0, 1000.0))
    (blk,), _ = th._blocks(tbl)
    on_hill = [ok for i, ok in _windows_300(blk, step=1) if 2000 <= blk.t0 + i and blk.t0 + i + 300 <= 2333]
    assert on_hill and not any(on_hill)
    out = th.drift_segment(run(32, tbl), fit_records=None)
    assert "rejected" in out


def test_h5_segment_wrist():
    r = run(8, steady_table(3000), src="wrist")
    assert th.drift_segment(r, fit_records=None) == {"id": 8, "rejected": th.WRIST_REASON}
    ev = th.drift_segment(r, fit_records=None, include_wrist=True)
    assert ev["id"] == 8 and ev["wrist"] is True and "rejected" not in ev


def ev(h, label, drift=0.0):
    return {"id": h, "h": h, "label": label, "drift": drift}


BASE = [ev(132, "below"), ev(136, "below"), ev(138, "below"), ev(146, "above"), ev(149, "above"), ev(152, "above")]


def test_h5_bounds_worked_example():
    out = th.drift_bounds(BASE)
    assert out["status"] == "ok" and out["estimate"] == 142 and out["confidence"] == "medium"
    assert out["errors"] == 0 and out["gap"] == 8
    assert out["bounds"] == {"lower": 138, "upper": 146}
    assert th.drift_bounds(BASE, h2={"a": 60, "b": 25})["pace_s_per_km"] == 305
    assert th.drift_bounds(BASE)["pace_s_per_km"] is None


def test_h5_bounds_with_one_error_is_low():
    out = th.drift_bounds(BASE + [ev(144, "below"), ev(141, "above")])
    assert out["errors"] == 1 and out["bounds"] == {"lower": 138, "upper": 146}
    assert out["estimate"] == 142 and out["confidence"] == "low"


def test_h5_bounds_one_sided():
    below = th.drift_bounds(BASE[:3])
    assert below["estimate"] is None and below["status"] == "insufficient_data"
    assert "LT1 HR >= 138" in below["notes"]
    above = th.drift_bounds(BASE[3:])
    assert above["estimate"] is None and "LT1 HR <= 146" in above["notes"]
    assert th.drift_bounds([])["status"] == "insufficient_data"


def test_h5_bounds_gap_limits():
    low = th.drift_bounds([ev(130, "below"), ev(131, "below"), ev(146, "above"), ev(147, "above")])
    assert low["gap"] == 15 and low["confidence"] == "low" and low["estimate"] == 139   # R(138.5)
    wide = th.drift_bounds([ev(120, "below"), ev(125, "below"), ev(150, "above"), ev(155, "above")])
    assert wide["estimate"] is None and "bounds too wide" in wide["reasons"]
    one_each = th.drift_bounds([ev(138, "below"), ev(146, "above")])
    assert one_each["confidence"] == "low"       # E = 0, gap 8 but fewer than 2 runs per label
    bad = th.drift_bounds([ev(130, "below"), ev(150, "below"), ev(140, "above")])
    assert bad["estimate"] is None and "inconsistent drift evidence" in bad["reasons"]
    assert [r["id"] for r in bad["runs"]] == [130, 150, 140]


# --- F1 (task 3b) ---

def lap(start_s, dur):
    return {"startTimeGMT": (datetime(2026, 11, 17, 10) + timedelta(seconds=start_s)).strftime("%Y-%m-%dT%H:%M:%S.0"),
            "duration": float(dur), "distance": 0.0}


F1_LAPS = [lap(0, 900), lap(900, 3600), lap(4500, 600)]


def f1_table(v1, hr1, v2, hr2, steady=3600, spd=None):
    half = 900 + steady // 2

    def speed(t):
        if t <= 900:
            return 2.0
        return v1 if t <= half else v2

    return table(900 + steady + 600, spd or speed, hr_fn=lambda t: 120 if t <= 900 else (hr1 if t <= half else hr2))


def f1_laps(steady=3600):
    return [lap(0, 900), lap(900, steady), lap(900 + steady, 600)]


def test_f1_at_lt1_saveable():
    out = th.drift(f1_table(3.20, 140, 3.18, 146), laps=F1_LAPS, treadmill=False)
    assert out["status"] == "ok" and out["verdict"] == "at LT1" and out["saveable"] is True
    assert out["drift_pct"] == pytest.approx(4.94, abs=0.01)
    assert out["lt1"] == {"hr": 140, "pace_s_per_km": 313}
    assert out["confidence"] == "medium" and out["temperature"] == "unknown"
    assert out["uneven_pace"] is False and out["excluded_s"] == 0


def test_f1_below_and_above_retest():
    below = th.drift(f1_table(3.10, 138, 3.09, 141), laps=F1_LAPS, treadmill=False)
    assert below["drift_pct"] == pytest.approx(2.50, abs=0.01) and below["verdict"] == "below LT1"
    assert below["saveable"] is False and below["retest_hr"] == 143 and below["confidence"] is None
    above = th.drift(f1_table(3.30, 146, 3.26, 156), laps=F1_LAPS, treadmill=False)
    assert above["drift_pct"] == pytest.approx(8.16, abs=0.01) and above["verdict"] == "above LT1"
    assert above["saveable"] is False and above["retest_hr"] == 141


def test_f1_35_min_rejected():
    out = th.drift(f1_table(3.2, 140, 3.18, 146, steady=2100), laps=f1_laps(2100), treadmill=False)
    assert out["status"] == "rejected" and any(f"shorter than {th.F1_MIN_S} s" in r for r in out["reasons"])


def test_f1_uneven_pace_not_saveable():
    def spd(t):
        if t <= 900:
            return 2.0
        return 3.0 if ((t - 901) // 60) % 2 == 0 else 3.4
    out = th.drift(f1_table(3.2, 140, 3.2, 146, spd=spd), laps=F1_LAPS, treadmill=False)
    assert out["uneven_pace"] is True and out["speed_cv"] > 0.03
    assert out["verdict"] == "at LT1" and out["saveable"] is False and out["confidence"] is None


def test_f1_stop_excluded_reported_and_limits():
    def spd(t):
        if t <= 900:
            return 2.0
        return 0.0 if 2000 <= t < 2020 else 3.0
    out = th.drift(f1_table(3.0, 140, 3.0, 146, spd=spd), laps=F1_LAPS, treadmill=False)
    assert out["status"] == "ok" and out["excluded_s"] == 20
    assert out["drift_pct"] == pytest.approx((146 / 140 - 1) * 100, abs=0.01)   # not distorted by the stop
    long_stop = th.drift(f1_table(3.0, 140, 3.0, 146, spd=lambda t: 2.0 if t <= 900 else (0.0 if 2000 <= t < 2040 else 3.0)),
                         laps=F1_LAPS, treadmill=False)
    assert long_stop["status"] == "rejected" and long_stop["excluded_s"] == 40
    assert any("30 s" in r for r in long_stop["reasons"])
    many = th.drift(f1_table(3.0, 140, 3.0, 146, spd=lambda t: 2.0 if t <= 900 else (
        0.0 if t % 500 < 25 and t > 1000 else 3.0)), laps=F1_LAPS, treadmill=False)
    assert many["status"] == "rejected" and any("60 s" in r or "30 s" in r for r in many["reasons"])


def test_f1_treadmill_uses_hr_only():
    out = th.drift(f1_table(3.0, 140, 3.0, 145, spd=lambda t: 2.0 if t <= 900 else 3.0 + (t % 7) * 0.2),
                   laps=F1_LAPS, treadmill=True)
    assert out["drift_pct"] == pytest.approx((145 / 140 - 1) * 100, abs=0.01) and out["verdict"] == "at LT1"
    assert out["lt1"] == {"hr": 140, "pace_s_per_km": None} and out["uneven_pace"] is False


def test_f1_without_laps_uses_900_to_4500():
    out = th.drift(f1_table(3.20, 140, 3.18, 146), laps=[], treadmill=False)
    assert out["bounds"] == [900, 4500] and out["verdict"] == "at LT1"


def test_f1_temperature_from_fit_and_f3_downgrade():
    fit = [{"t": t, "temp": 18} for t in range(0, 5100)]
    out = th.drift(f1_table(3.20, 140, 3.18, 146), laps=F1_LAPS, treadmill=False, fit_records=fit, f3_hr=145)
    assert out["temperature"] == 18 and out["confidence"] == "medium"      # 5 bpm apart: not more than 5
    low = th.drift(f1_table(3.20, 140, 3.18, 146), laps=F1_LAPS, treadmill=False, f3_hr=146)
    assert low["confidence"] == "low" and low["saveable"] is True


# --- F2 (task 3b) ---

def tt_table(v1=4.0, v2=4.0, spd=None):
    def speed(t):
        if t <= 900 or t > 2700:
            return 2.0
        return v1 if t <= 1800 else v2

    return table(3300, spd or speed, hr_fn=lambda t: 140 if t <= 900 else (158 if t < 1500 else 168))


TT_LAPS = [lap(0, 900), lap(900, 600), lap(1500, 1200), lap(2700, 600)]


def test_f2_worked_example():
    out = th.time_trial(tt_table(), laps=TT_LAPS)
    assert out["status"] == "ok" and out["lt2"] == {"hr": 168, "pace_s_per_km": 250}
    assert out["speed_ratio"] == pytest.approx(1.0) and out["fade"] is False and out["confidence"] == "medium"
    assert out["bounds"] == [900, 2700] and out["distance_m"] == pytest.approx(7200)


def test_f2_fade_is_low():
    out = th.time_trial(tt_table(4.4, 3.6), laps=TT_LAPS)
    assert out["fade"] is True and out["speed_ratio"] < 0.95 and out["confidence"] == "low"


def test_f2_without_laps_uses_fastest_window():
    out = th.time_trial(tt_table(), laps=[])
    assert out["status"] == "ok" and out["lt2"]["pace_s_per_km"] == 250 and out["bounds"] == [900, 2700]


def test_f2_stop_rejected_and_reported_and_short_rejected():
    out = th.time_trial(tt_table(spd=lambda t: 2.0 if t <= 900 or t > 2700 else (0.0 if 1700 <= t < 1720 else 4.0)),
                        laps=TT_LAPS)
    # methods doc section 9: any stop longer than the section 1 stop duration rejects the trial
    assert out["status"] == "rejected" and out["excluded_s"] == 20
    assert any(f"{rules.STOP_SLOW_S} s" in r for r in out["reasons"])
    short = th.time_trial(table(1500, lambda t: 4.0), laps=[lap(0, 1500)])
    assert short["status"] == "rejected"


# --- test workouts (task 3b) ---

def steps_flat(dsl):
    out = []
    for s in dsl["steps"]:
        out += s["steps"] if "repeat" in s else [s]
    return out


def targets(steps):
    return [s.get("target") for s in steps]


def test_workout_drift_with_record_pace_target():
    dsl = th.test_workout("drift", lt1_record={"lt1": {"hr": 142, "pace_s_per_km": 304}},
                          estimate_lt1_hr=None, h2=None)
    build_workout(dsl)
    steady = [s for s in dsl["steps"] if s["type"] == "interval"]
    assert len(steady) == 1 and steady[0]["duration"] == "60m"
    assert steady[0]["target"] == {"pace": "4:54-5:14"}
    assert dsl["steps"][-1]["type"] == "cooldown" and dsl["steps"][-1]["duration"] == "10m"
    assert [s["duration"] for s in dsl["steps"] if s["type"] == "warmup"] == ["12m", "3m"]


def test_workout_drift_with_estimate_hr_only():
    dsl = th.test_workout("drift", lt1_record=None, estimate_lt1_hr=142, h2=None)
    build_workout(dsl)
    warm = [s for s in dsl["steps"] if s["type"] == "warmup"]
    assert warm[0].get("target") is None and warm[1]["target"] == {"hr": "139-145"}
    steady = [s for s in dsl["steps"] if s["type"] == "interval"][0]
    assert "target" not in steady and "hold the pace reached at the end of the warm-up" in steady["note"]
    # a record without a pace also falls back to the estimate
    dsl2 = th.test_workout("drift", lt1_record={"lt1": {"hr": 140, "pace_s_per_km": None}},
                           estimate_lt1_hr=142, h2=None)
    assert [s for s in dsl2["steps"] if s["type"] == "warmup"][1]["target"] == {"hr": "139-145"}


def test_workout_drift_without_anything():
    dsl = th.test_workout("drift", lt1_record=None, estimate_lt1_hr=None, h2=None)
    build_workout(dsl)
    assert all(t is None for t in targets(steps_flat(dsl)))
    notes = " ".join(s.get("note", "") for s in dsl["steps"])
    assert "build to conversational effort, RPE 3-4" in notes and "conversational, constant pace" in notes


def test_workout_time_trial():
    dsl = th.test_workout("time_trial", lt1_record=None, estimate_lt1_hr=None, h2=None)
    build_workout(dsl)
    flat = steps_flat(dsl)
    assert all(t is None for t in targets(flat))
    assert [s["duration"] for s in dsl["steps"] if "duration" in s and s["type"] == "interval"] == ["10m", "20m"]
    assert dsl["steps"][0]["duration"] == "15m" and dsl["steps"][1]["repeat"] == 3
    assert dsl["steps"][1]["steps"][0]["duration"] == "20s"


def test_workout_ramp_from_record_with_h2():
    rec = {"lt1": {"hr": 142, "pace_s_per_km": 304}}
    h2 = {"a": 60, "b": 25, "v_min": 2.8, "v_max": 3.8}
    dsl = th.test_workout("ramp", lt1_record=rec, estimate_lt1_hr=None, h2=h2)
    build_workout(dsl)
    ramp = [s for s in dsl["steps"] if s["type"] == "interval"]
    assert len(ramp) == 8 and all(s["duration"] == "4m" for s in ramp)
    assert dsl["steps"][0]["type"] == "warmup" and dsl["steps"][-1]["type"] == "cooldown"
    # 121 bpm -> 2.44 m/s, below 0.9 x 2.8: HR target; 127 bpm -> 2.68 m/s: pace R(373.1) = 373 +/- 5
    assert ramp[0]["target"] == {"hr": "118-124"} and "hold the pace steady" in ramp[0]["note"]
    assert ramp[1]["target"] == {"pace": "6:08-6:18"}
    assert ramp[7]["target"] == {"pace": "3:58-4:08"}      # 163 bpm -> 4.12 m/s -> 243 s/km, 4.12 <= 1.1 x 3.8
    hrs = [int(s["target"]["hr"].split("-")[0]) + 3 for s in ramp if "hr" in s["target"]]
    assert hrs == [121]


def test_workout_ramp_hr_steps_from_estimate_and_no_h2():
    dsl = th.test_workout("ramp", lt1_record=None, estimate_lt1_hr=142, h2={"a": 60, "b": 25, "v_min": 2.0, "v_max": 4.0})
    build_workout(dsl)
    ramp = [s for s in dsl["steps"] if s["type"] == "interval"]
    centers = [(lambda lo, hi: (int(lo) + int(hi)) // 2)(*s["target"]["hr"].split("-")) for s in ramp]
    assert centers == [121, 127, 133, 139, 145, 151, 157, 163]
    rec = {"lt1": {"hr": 142, "pace_s_per_km": 304}}
    assert all("hr" in s["target"] for s in
               [s for s in th.test_workout("ramp", lt1_record=rec, estimate_lt1_hr=None, h2=None)["steps"]
                if s["type"] == "interval"])


def test_workout_ramp_rpe_notes_without_data():
    dsl = th.test_workout("ramp", lt1_record=None, estimate_lt1_hr=None, h2=None)
    build_workout(dsl)
    ramp = [s for s in dsl["steps"] if s["type"] == "interval"]
    assert all("target" not in s for s in ramp)
    assert [s["note"] for s in ramp] == ["RPE 2 of 10", "RPE 2-3 of 10", "RPE 3 of 10", "RPE 3-4 of 10",
                                         "RPE 4 of 10", "RPE 4-5 of 10", "RPE 5 of 10", "RPE 6 of 10"]


def test_workout_unknown_kind():
    from pace.envelope import PaceError
    with pytest.raises(PaceError):
        th.test_workout("nope", lt1_record=None, estimate_lt1_hr=None, h2=None)


# --- fix round 1 ---

def test_f2_short_gap_is_low_not_rejected():
    tbl = [r for r in tt_table() if not 1700 <= r["t"] < 1708]      # 8 rows missing: an 8 s stop
    out = th.time_trial(tbl, laps=TT_LAPS)
    assert out["status"] == "ok" and out["confidence"] == "low" and out["excluded_s"] == 8
    assert any("stops" in r for r in out["reasons"]) and out["lt2"]["hr"] == 168


def test_f2_without_laps_allows_a_short_stop_and_reports_it():
    # methods doc sections 1 and 9: a stop up to STOP_SLOW_S is allowed in the trial (reported, confidence low)
    tbl = [r for r in tt_table() if not 1700 <= r["t"] < 1708]      # an 8 s timestamp gap
    out = th.time_trial(tbl, laps=[])
    assert out["status"] == "ok" and out["bounds"] == [900, 2700] and out["excluded_s"] == 8
    assert out["confidence"] == "low" and any("stops" in r for r in out["reasons"])
    # a longer stop is never spanned: no 1800 s window remains on either side
    long_stop = tt_table(spd=lambda t: 2.0 if t <= 900 or t > 2700 else (0.0 if 1700 <= t < 1720 else 4.0))
    out = th.time_trial(long_stop, laps=[])
    assert out["status"] == "rejected" or not out["bounds"][0] < 1710 < out["bounds"][1]


def test_f2_hr_window_is_relative_to_trial_start():
    # warm-up HR 100 then 158/168 inside the trial; the trial starts in the middle of a block (t0 = 0)
    out = th.time_trial(tt_table(), laps=TT_LAPS)
    assert out["lt2"]["hr"] == 168 and out["bounds"][0] == 900


def test_lap_elapsed_duration_covers_a_pause():
    laps = [lap(0, 900), dict(lap(900, 1700), elapsedDuration=1800.0), lap(2700, 600)]
    assert th._lap_spans(laps)[1] == (900, 2700)
    laps2 = [dict(lap(0, 900)), dict(lap(900, 600)), dict(lap(1500, 1100), elapsedDuration=1200.0), lap(2700, 600)]
    assert th.time_trial(tt_table(), laps=laps2)["bounds"] == [900, 2700]


def test_f1_auto_laps_fall_back_to_900_4500():
    auto = [dict(lap(300 * k, 300), distance=900.0) for k in range(17)]
    out = th.drift(f1_table(3.20, 140, 3.18, 146), laps=auto, treadmill=False)
    assert out["bounds"] == [900, 4500] and out["verdict"] == "at LT1"
    # manual laps with a short steady part still reject
    short = th.drift(f1_table(3.2, 140, 3.18, 146, steady=2100), laps=f1_laps(2100), treadmill=False)
    assert short["status"] == "rejected"


def test_f1_uneven_pace_reason_on_any_verdict():
    def spd(t):
        return 2.0 if t <= 900 else (3.0 if ((t - 901) // 60) % 2 == 0 else 3.4)
    out = th.drift(f1_table(3.2, 140, 3.2, 141, spd=spd), laps=F1_LAPS, treadmill=False)
    assert out["verdict"] == "below LT1" and out["uneven_pace"] is True and "uneven pace" in out["reasons"]


def test_h5_bounds_wrist_caps_confidence_and_pace_guard():
    rows = [dict(e, wrist=True) if e["h"] == 132 else e for e in BASE]
    out = th.drift_bounds(rows)
    assert out["estimate"] == 142 and out["confidence"] == "low"
    # v = (142 - 200) / 25 < 0: no pace instead of a negative or failing one
    assert th.drift_bounds(BASE, h2={"a": 200, "b": 25})["pace_s_per_km"] is None
    assert th.drift_bounds(BASE, h2={"a": 142, "b": 25})["pace_s_per_km"] is None


# --- Task 4: spearman, alpha1 gate, H4 (methods doc 6, 14.2) ---

def a1w(alpha1, hr, speed, cadence=172, share=0.01, n=20):
    return [{"alpha1": alpha1, "hr": hr, "speed": speed, "cadence": cadence, "artifact_share": share,
             "beats": 300} for _ in range(n)]


H4_A = [(1.00, 130, 2.80), (0.90, 136, 3.04), (0.80, 142, 3.28), (0.70, 148, 3.52), (0.60, 154, 3.76),
        (0.50, 160, 4.00)]
OBSERVED = [(126, 0.54), (133, 0.65), (136, 0.45), (139, 0.35), (142, 0.37), (148, 0.35), (151, 0.46),
            (156, 0.61), (159, 0.63), (164, 1.03), (170, 1.13), (176, 0.99), (180, 0.35)]
OBSERVED_CADENCE = [166, 168, 169, 170, 171, 172, 174, 176, 178, 180, 182, 185, 188]


def h4_run(rid, pts, share=0.01, cadence=172, dropped=0, n=20):
    return {"id": rid, "windows": [w for a, hr, v in pts for w in a1w(a, hr, v, cadence, share, n)],
            "dropped_coupling": dropped}


def test_spearman_average_ranks_for_ties():
    assert th.spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1)
    assert th.spearman([1, 2, 2, 3], [1, 2, 3, 4]) == pytest.approx(4.5 / (4.5 * 5) ** 0.5)
    assert th.spearman([1, 1, 1], [1, 2, 3]) == 0.0      # no variance: no monotonic relation
    hr = [h for h, _ in OBSERVED]
    a1 = [a for _, a in OBSERVED]
    keep = list(range(11))
    assert th.spearman([hr[i] for i in keep], [a1[i] for i in keep]) == pytest.approx(0.492, abs=0.001)


def test_alpha1_gate_outcomes():
    falling = [{"hr": 130 + 6 * k, "alpha1": 1.0 - 0.1 * k} for k in range(6)]
    g = th.alpha1_gate(falling)
    assert g["usable"] and g["outcome"] == "usable" and g["rho"] == pytest.approx(-1) and g["reasons"] == []
    one = th.alpha1_gate(falling[:3])
    assert one["outcome"] == "one_sided" and one["reasons"] == ["one-sided"] and not one["usable"]
    rising = [{"hr": p["hr"], "alpha1": 1.5 - p["alpha1"]} for p in falling]
    mixed = [{"hr": 130 + 6 * k, "alpha1": a} for k, a in enumerate((0.5, 0.6, 0.7, 0.8, 0.9, 0.6))]
    both = th.alpha1_gate(mixed)                             # 6 points over 30 bpm, 2 above 0.75, rising
    assert both["outcome"] == "not_monotonic" and both["reasons"][1] == "one-sided"
    assert both["reasons"][0].startswith("not monotonic, rho = ")
    below_only = th.alpha1_gate(rising[:3] + falling[3:])    # rho 0 but all below 0.75: never counts
    assert below_only["outcome"] == "one_sided" and below_only["reasons"][1] == "one-sided"
    assert below_only["reasons"][0].startswith("mostly one-sided: rho = 0.00 not judged (0 points above")
    one_above = th.alpha1_gate([dict(p, alpha1=0.8) if p["alpha1"] == 0.9 else p for p in mixed])
    assert one_above["outcome"] == "not_monotonic"           # 0.8 twice: still 2 above
    assert th.alpha1_gate([dict(p, alpha1=0.6) if p["alpha1"] == 0.9 else p for p in mixed])["outcome"] == "one_sided"
    assert th.alpha1_gate([])["outcome"] == "one_sided" and th.alpha1_gate([])["rho"] is None
    few = th.alpha1_gate(rising[:2] + falling[3:])           # 5 points: too few to judge monotonicity
    assert few["outcome"] == "insufficient_data" and few["reasons"][0].startswith("too few points")
    narrow = th.alpha1_gate([{"hr": 140 + k, "alpha1": 0.5 + 0.08 * k} for k in range(7)])   # 6 bpm span
    assert narrow["outcome"] == "insufficient_data"


def test_h4_noisy_steady_easy_run_is_one_sided():
    rng = random.Random(11)
    ws = [{"alpha1": 0.80 + 0.4 * rng.random(), "hr": 130 + 12 * k / 399 + rng.gauss(0, 1), "speed": 2.8,
           "cadence": 172, "artifact_share": 0.01, "beats": 300} for k in range(400)]
    r = th.alpha1_lt1([{"id": 8, "windows": ws, "dropped_coupling": 0}])["runs"][0]
    assert r["windows"] == 400 and r["outcome"] == "one_sided" and r["reasons"][-1] == "one-sided"
    assert r["notes"][0].startswith("alpha1 above 0.75 up to ")
    data = copy.deepcopy(zones.EMPTY)
    zones.record_alpha1_outcome(data, "not_monotonic", day=date(2026, 11, 16), activity=7)
    zones.record_alpha1_outcome(data, r["outcome"], day=date(2026, 11, 17), activity=8)
    assert data["h4"]["streak"] == 1 and data["h4"]["unreliable"] is False


@pytest.mark.parametrize(("n_below", "outcome"), [(2, "one_sided"), (30, "one_sided"), (40, "not_monotonic")])
def test_alpha1_gate_side_minimum_scales_with_points(n_below, outcome):
    rng = random.Random(5)
    below = set(rng.sample(range(400), n_below))
    pts = [{"hr": 130 + 12 * k / 399, "alpha1": 0.6 if k in below else 0.8 + 0.4 * rng.random()} for k in range(400)]
    g = th.alpha1_gate(pts)
    assert g["rho"] > -0.6 and g["below"] == n_below and g["outcome"] == outcome and not g["usable"]
    if outcome == "one_sided":
        assert g["reasons"][0].endswith("need 40 each)")


def test_h4_three_window_rising_run_does_not_touch_the_streak():
    out = th.alpha1_lt1([h4_run(7, [(0.6, 130, 3.0), (0.8, 136, 3.1), (1.0, 142, 3.2)], n=1)])
    r = out["runs"][0]
    assert r["windows"] == 3 and r["rho"] == pytest.approx(1)
    assert r["outcome"] == "insufficient_data" and out["status"] == "alpha1_unusable" and out["hr"] is None
    data = copy.deepcopy(zones.EMPTY)
    zones.record_alpha1_outcome(data, "not_monotonic", day=date(2026, 11, 16), activity=6)
    zones.record_alpha1_outcome(data, r["outcome"], day=date(2026, 11, 17), activity=7)
    assert data["h4"]["streak"] == 1 and data["h4"]["unreliable"] is False


def test_h4_regression_worked_example():
    a = h4_run(1, H4_A, share=0.01)
    b = h4_run(2, [(x, hr + 2, v + 0.08) for x, hr, v in H4_A], share=0.02)
    out = th.alpha1_lt1([a, b])
    ra, rb = out["runs"]
    assert ra["outcome"] == "usable" and ra["rho"] == pytest.approx(-1)
    assert ra["fit"]["hr"]["c"] == pytest.approx(190) and ra["fit"]["hr"]["d"] == pytest.approx(-60)
    assert ra["fit"]["speed"]["e"] == pytest.approx(5.20) and ra["fit"]["speed"]["f"] == pytest.approx(-2.40)
    assert ra["fit"]["hr"]["r"] == pytest.approx(-1)
    assert ra["lt1_hr"] == pytest.approx(145) and ra["lt1_speed_mps"] == pytest.approx(3.40)
    assert rb["lt1_hr"] == pytest.approx(147) and rb["lt1_speed_mps"] == pytest.approx(3.48)
    assert out["status"] == "ok" and out["hr"] == 146 and out["pace_s_per_km"] == 291
    assert out["spread"] == pytest.approx(2) and out["artifact_share"] == pytest.approx(0.015)
    assert out["confidence"] == "medium"


def test_h4_confidence_low_with_one_run_wide_spread_or_artifacts():
    assert th.alpha1_lt1([h4_run(1, H4_A)])["confidence"] == "low"
    far = h4_run(2, [(x, hr + 8, v) for x, hr, v in H4_A], cadence=185)   # 168 bpm vs 172 would be coupled
    wide = th.alpha1_lt1([h4_run(1, H4_A), far])
    assert wide["spread"] == pytest.approx(8) and wide["confidence"] == "low"
    dirty = th.alpha1_lt1([h4_run(1, H4_A, share=0.04), h4_run(2, H4_A, share=0.04)])
    assert dirty["confidence"] == "low"


def test_h4_crossing_requirements():
    few = [(x, hr, v) for x, hr, v in H4_A]
    short = {"id": 1, "windows": [w for a, hr, v in few for w in a1w(a, hr, v, n=10)], "dropped_coupling": 0}
    out = th.alpha1_lt1([short])                     # gate passes, but only 30 points per side
    assert out["runs"][0]["outcome"] == "usable" and out["runs"][0]["lt1_hr"] is None
    assert out["status"] == "insufficient_data" and out["hr"] is None
    flat_v = h4_run(2, [(x, hr, 3.0) for x, hr, _ in H4_A])
    r = th.alpha1_lt1([flat_v])
    assert r["runs"][0]["lt1_hr"] == pytest.approx(145) and r["runs"][0]["lt1_speed_mps"] is None
    assert r["hr"] == 145 and r["pace_s_per_km"] is None


def test_h4_one_sided_run_note_and_memory():
    out = th.alpha1_lt1([h4_run(3, H4_A[:3])])
    r = out["runs"][0]
    assert r["outcome"] == "one_sided" and r["reasons"] == ["one-sided"]
    assert r["notes"] == ["alpha1 above 0.75 up to 142 bpm"]
    assert out["status"] == "alpha1_unusable" and out["hr"] is None
    data = copy.deepcopy(zones.EMPTY)
    zones.record_alpha1_outcome(data, r["outcome"], day=date(2026, 11, 17), activity=3)
    assert data["h4"]["streak"] == 0


def test_h4_gate_observed_pattern_not_monotonic_marks_unreliable():
    def observed_run(rid):
        ws = [w for (hr, a), c in zip(OBSERVED, OBSERVED_CADENCE) for w in a1w(a, hr, 3.0, cadence=c)]
        return {"id": rid, "windows": ws, "dropped_coupling": 0}

    out = th.alpha1_lt1([observed_run(5)])
    r = out["runs"][0]
    assert r["dropped_coupling"] == 40 and r["windows"] == 220
    assert r["rho"] == pytest.approx(0.49, abs=0.005)
    assert r["outcome"] == "not_monotonic" and r["reasons"][0] == "not monotonic, rho = 0.49"
    assert out["status"] == "alpha1_unusable" and out["hr"] is None
    data = copy.deepcopy(zones.EMPTY)
    for rid in (5, 6):
        res = th.alpha1_lt1([observed_run(rid)])["runs"][0]
        zones.record_alpha1_outcome(data, res["outcome"], day=date(2026, 11, 17), activity=rid)
    assert data["h4"]["unreliable"] is True
    assert th.select_fit([], ref_hr=142, cached=set(), no_rr=set(), rr_since=date(2026, 9, 1),
                         unreliable=data["h4"]["unreliable"]) == []


# --- F3 ramp (methods doc 9a, 14.8) ---

def ramp_beats(steps, *, step_s=240, warm=600, cool=300, art=None):
    """Aligned beats and laps: warm-up, one lap per (hr, speed, cadence) step, cool-down. RR = 60 / HR exactly;
    art = {step index: n} turns n beats of the step's last 120 s into "other" artifacts (0.7 x RR)."""
    segs = [(warm, 120, 2.5, 170)] + [(step_s, hr, v, c) for hr, v, c in steps] + [(cool, 120, 2.5, 170)]
    ends, acc = [], 0
    for dur, *_ in segs:
        acc += dur
        ends.append(acc)
    dist_sec, d = [0.0], 0.0
    for s in range(1, acc + 2):
        k = next((i for i, e in enumerate(ends) if s - 1 < e), len(segs) - 1)
        d += segs[k][2]
        dist_sec.append(d)
    beats, t = [], 0.0
    for k, (dur, hr, v, c) in enumerate(segs):
        while t < ends[k]:   # the last beat of a segment may end just after it, never one of the next before
            t += 60 / hr
            beats.append({"t_s": t, "rr_s": 60 / hr, "hr": hr, "speed": v, "dist": dist_sec[math.floor(t)],
                          "alt": 10.0, "cadence": c, "gap": False, "stop": False})
    for k, n in (art or {}).items():
        end = ends[k + 1]
        idx = [i for i, b in enumerate(beats) if end - 120 <= b["t_s"] < end]
        for i in idx[3::len(idx) // n][:n]:
            beats[i]["rr_s"] *= 0.7
    laps = [lap(0, warm)] + [lap(warm + step_s * k, step_s) for k in range(len(steps))]
    laps.append(lap(warm + step_s * len(steps), cool))
    return beats, laps


RAMP_STEPS = [(x, hr, v) for x, hr, v in H4_A]


@pytest.fixture
def inject_alpha1(monkeypatch):
    """Replace DFA by a lookup on the window HR (alpha1 injected per step)."""
    table_ = {}
    monkeypatch.setattr(hrv, "dfa_alpha1", lambda x, **kw: table_[round(60 / statistics.fmean(x))])
    return table_


def test_f3_worked_example(inject_alpha1):
    inject_alpha1.update({hr: a for a, hr, _ in RAMP_STEPS})
    beats, laps = ramp_beats([(hr, v, 172) for _, hr, v in RAMP_STEPS], art={k: 3 for k in range(6)})
    out = th.ramp(beats, laps=laps, treadmill=False)
    assert out["dropped_coupling"] == 0 and out["gate"]["rho"] == pytest.approx(-1)
    assert out["outcome"] == "usable" and out["status"] == "ok"
    assert all(s["status"] == "kept" for s in out["steps"]) and len(out["steps"]) == 6
    assert all(0.009 <= s["artifact_share"] <= 0.012 for s in out["steps"])
    assert out["fit"]["hr"]["c"] == pytest.approx(190) and out["fit"]["hr"]["d"] == pytest.approx(-60)
    assert out["fit"]["speed"]["e"] == pytest.approx(5.20) and out["fit"]["speed"]["f"] == pytest.approx(-2.40)
    assert out["lt1"] == {"hr": 145, "pace_s_per_km": 294}
    assert out["confidence"] == "medium" and out["bracketed"] and out["saveable"]
    tm = th.ramp(beats, laps=laps, treadmill=True)
    assert tm["lt1"] == {"hr": 145, "pace_s_per_km": None} and tm["fit"]["speed"] is None


def test_f3_artifact_step_dropped_one_sided(inject_alpha1):
    inject_alpha1.update({hr: a for a, hr, _ in RAMP_STEPS})
    beats, laps = ramp_beats([(hr, v, 172) for _, hr, v in RAMP_STEPS], art={3: 18})
    out = th.ramp(beats, laps=laps, treadmill=False)
    step = out["steps"][3]
    assert step["status"] == "dropped" and step["artifact_share"] > 0.05 and "artifact" in step["reason"]
    assert out["status"] == "alpha1_unusable" and out["outcome"] == "one_sided"
    assert out["reasons"] == ["one-sided"] and out["lt1"] is None and not out["saveable"]


def test_f3_first_three_steps_one_sided(inject_alpha1):
    inject_alpha1.update({hr: a for a, hr, _ in RAMP_STEPS})
    beats, laps = ramp_beats([(hr, v, 172) for _, hr, v in RAMP_STEPS[:3]])
    out = th.ramp(beats, laps=laps, treadmill=False)
    assert out["status"] == "alpha1_unusable" and out["reasons"] == ["one-sided"]


def test_f3_observed_ramp_is_unusable(inject_alpha1):
    inject_alpha1.update(dict(OBSERVED))
    beats, laps = ramp_beats([(hr, 3.0, c) for (hr, _), c in zip(OBSERVED, OBSERVED_CADENCE)], step_s=180)
    out = th.ramp(beats, laps=laps, treadmill=False)
    assert out["dropped_coupling"] == 2
    assert [s["step"] for s in out["steps"] if s["status"] == "dropped"] == [12, 13]
    assert out["gate"]["rho"] == pytest.approx(0.49, abs=0.005)
    assert out["status"] == "alpha1_unusable" and out["outcome"] == "not_monotonic"
    assert out["reasons"] == ["not monotonic, rho = 0.49", "one-sided"]
    assert out["lt1"] is None and not out["saveable"]
    data = copy.deepcopy(zones.EMPTY)
    zones.record_alpha1_outcome(data, out["outcome"], day=date(2026, 11, 17), activity=9)
    assert data["h4"]["streak"] == 1


def test_f3_step_excluded_on_step_share(inject_alpha1):
    # methods doc 9a: a step whose RR oscillation at the step frequency dominates is dropped like a coupled one
    inject_alpha1.update({hr: a for a, hr, _ in RAMP_STEPS})
    beats, laps = ramp_beats([(hr, v, 172) for _, hr, v in RAMP_STEPS])
    rng = random.Random(21)
    for b in beats:
        b["rr_s"] += 0.002 * rng.gauss(0, 1)
    f = hrv.step_frequency_hz(172, 148)                    # step 4 (index 3): HR 148, alpha1 0.70
    end = 600 + 4 * 240                                    # warm-up 600 s, steps of 240 s
    for b in beats:
        if end - 240 <= b["t_s"] < end:
            b["rr_s"] += 0.020 * math.sin(2 * math.pi * f * b["t_s"])
    out = th.ramp(beats, laps=laps, treadmill=False)
    step = out["steps"][3]
    assert step["status"] == "dropped" and step["reason"].startswith("step interference")
    assert step["step_share"] > hrv.STEP_SHARE_MAX
    others = [s for k, s in enumerate(out["steps"]) if k != 3]
    assert all(s["status"] == "kept" and s["step_share"] is not None and s["step_share"] <= hrv.STEP_SHARE_MAX
               for s in others)
    assert out["dropped_step"] == 1 and out["dropped_coupling"] == 0
    assert out["status"] == "alpha1_unusable" and out["outcome"] == "one_sided"


def test_h4_run_refilters_step_share():
    run = h4_run(3, H4_A)
    for w in run["windows"][:20]:      # the 20 windows at alpha1 1.00
        w["step_share"] = hrv.STEP_SHARE_MAX + 0.01
    for w in run["windows"][20:40]:
        w["step_share"] = hrv.STEP_SHARE_MAX   # at the limit: kept
    run["dropped_step"] = 4
    r = th.alpha1_lt1([run])["runs"][0]
    assert r["windows"] == 100 and r["dropped_step"] == 24 and r["dropped_coupling"] == 0


def test_combine_ignores_flagged_h4():
    flagged = dict(h4(146), flags=[th.A1_UNRELIABLE])
    out = th.combine_lt1(H3_142, flagged, h5(142), H2_OK)
    assert out["method"] == "H5" and out["hr"] == 142
    assert any("not combined" in n and "146" in n for n in out["notes"])
    alone = th.combine_lt1(H3_142, flagged, None, H2_OK)
    assert alone["method"] == "H3"


def test_f3_refuses_automatic_laps_and_too_few_steps(inject_alpha1):
    # methods doc 9a: steps come from lap presses; automatic laps (as F1's heuristic) are not steps
    inject_alpha1.update({hr: a for a, hr, _ in RAMP_STEPS})
    beats, laps = ramp_beats([(hr, v, 172) for _, hr, v in RAMP_STEPS])
    auto = [dict(lap(300 * k, 300), distance=1000.0) for k in range(10)]
    out = th.ramp(beats, laps=auto, treadmill=False)
    assert out["status"] == "rejected" and out["outcome"] is None and "automatic" in out["reasons"][0]
    two = th.ramp(beats, laps=laps[:3] + laps[-1:], treadmill=False)   # warm-up, 2 steps, cool-down
    assert two["status"] == "rejected" and "3 steps" in two["reasons"][0] and two["steps"] == []


def test_f3_needs_laps_and_skips_gaps(inject_alpha1):
    inject_alpha1.update({hr: a for a, hr, _ in RAMP_STEPS})
    beats, laps = ramp_beats([(hr, v, 172) for _, hr, v in RAMP_STEPS])
    assert th.ramp(beats, laps=[], treadmill=False)["status"] == "rejected"
    assert th.ramp(beats, laps=laps[:2], treadmill=False)["status"] == "rejected"
    end = 600 + 240
    next(b for b in beats if b["t_s"] > end - 60)["gap"] = True
    out = th.ramp(beats, laps=laps, treadmill=False)
    assert out["steps"][0]["status"] == "skipped" and out["outcome"] == "one_sided"


# --- LT1 combination (14.2) ---

H2_OK = {"status": "ok", "a": 60, "b": 25}
H3_142 = {"status": "ok", "hr": 142, "pace_s_per_km": 304, "speed_mps": 3.292, "confidence": "low"}


def h5(hr, conf="medium"):
    return {"status": "ok", "estimate": hr, "pace_s_per_km": None, "confidence": conf, "notes": [],
            "bounds": {"lower": hr - 4, "upper": hr + 4}}


def h4(hr, conf="medium", pace=291):
    return {"status": "ok", "hr": hr, "pace_s_per_km": pace, "confidence": conf, "notes": []}


def test_combine_agreement():
    out = th.combine_lt1(H3_142, h4(146), h5(142), H2_OK)
    assert out["status"] == "ok" and out["method"] == "H4+H5"
    assert out["hr"] == 144 and out["pace_s_per_km"] == 298 and out["confidence"] == "medium"
    assert out["flags"] == [] and out["suggest"] is None and out["h3"]["hr"] == 142
    # H4 and H5 agreeing confirm each other: medium with H3 missing or disagreeing (low, so no veto)
    for h3 in (None, {"status": "ok", "hr": None, "pace_s_per_km": 304, "confidence": "low"},
               dict(H3_142, hr=130)):
        alone = th.combine_lt1(h3, h4(146), h5(142), H2_OK)
        assert (alone["method"], alone["hr"], alone["pace_s_per_km"], alone["confidence"]) == ("H4+H5", 144, 298,
                                                                                               "medium")
    low4 = th.combine_lt1(None, h4(146, "low"), h5(142, "low"), H2_OK)
    assert low4["confidence"] == "low"


def test_combine_disagreement_picks_h5():
    out = th.combine_lt1(H3_142, h4(152), h5(142), H2_OK)
    assert out["method"] == "H5" and out["hr"] == 142 and out["pace_s_per_km"] == 305
    assert out["confidence"] == "low" and out["flags"] == ["H4 and H5 disagree by 10 bpm"]
    assert out["suggest"] == {"test": "F1", "hr": 147}
    high = th.combine_lt1(H3_142, h4(152, "medium"), h5(142, "low"), H2_OK)
    assert high["method"] == "H4" and high["hr"] == 152


def test_combine_falls_back_to_h3():
    one_sided = {"status": "insufficient_data", "estimate": None, "confidence": None, "notes": ["LT1 HR >= 138"],
                 "bounds": {"lower": 138, "upper": None}}
    unusable = {"status": "alpha1_unusable", "hr": None, "notes": []}
    out = th.combine_lt1(H3_142, unusable, one_sided, H2_OK)
    assert out["method"] == "H3" and out["hr"] == 142 and out["pace_s_per_km"] == 304
    assert out["confidence"] == "low"
    assert "H5: LT1 HR >= 138 (consistent with 142)" in out["notes"]
    assert th.combine_lt1(None, None, None, None)["status"] == "insufficient_data"


def test_combine_single_method_and_pace_sources():
    only4 = th.combine_lt1(H3_142, h4(144), None, None)       # no H2: H4's own pace
    assert only4["method"] == "H4" and only4["pace_s_per_km"] == 291 and only4["confidence"] == "medium"
    only5 = th.combine_lt1({"status": "ok", "hr": None, "pace_s_per_km": 304, "confidence": "low"},
                           None, h5(142), None)                # H5 alone without H2: no pace; nothing agrees
    assert only5["method"] == "H5" and only5["pace_s_per_km"] is None and only5["confidence"] == "low"


# --- run selection (14.7) ---

def srow(aid, day, *, speed=3.0, max_hr=160, avg_hr=140, dur=3000, race=False, type_key="running", gain=0.0):
    return {"activityId": aid, "activityType": {"typeKey": type_key},
            "startTimeGMT": f"2026-11-{day:02d} 08:00:00", "duration": dur, "averageHR": avg_hr, "maxHR": max_hr,
            "averageSpeed": speed, "elevationGain": gain, "distance": speed * dur,
            "eventType": {"typeKey": "race" if race else "uncategorized"}}


def test_select_history_effort_ranking_and_skips():
    rows = [srow(1, 3, max_hr=150, race=True), srow(2, 4, max_hr=185), srow(3, 5, max_hr=180, speed=3.0),
            srow(4, 5, max_hr=180, speed=3.5), srow(5, 6, max_hr=175), srow(6, 9, max_hr=175),
            srow(7, 10, max_hr=170), srow(8, 10, max_hr=170),
            srow(20, 1, type_key="treadmill_running"), srow(21, 1, type_key="trail_running"),
            srow(22, 1, dur=1100), srow(23, 1, avg_hr=None), srow(24, 1, gain=200.0)]
    out = th.select_history(rows)
    assert out["effort"] == [1, 2, 4, 3, 6, 5]
    assert out["steady"] == [7, 8]           # fewer candidates than slots: each picked once
    assert {s["id"] for s in out["skipped"]} == {20, 21, 22, 23, 24}
    assert all(s["reason"] for s in out["skipped"])
    assert th.select_history(rows, exclude={1, 2})["effort"] == [4, 3, 6, 5, 7, 8]


def test_selection_gain_and_duration_limits_follow_the_rules():
    # methods doc 14.7: list-row elevation gain per km (history and drift picks) and the drift duration window
    km = 3.0 * 3000 / 1000
    rows = [srow(1, 3, gain=km * rules.SEL_MAX_GAIN), srow(2, 3, gain=km * rules.SEL_MAX_GAIN + 1)]
    assert {s["id"] for s in th.select_history(rows)["skipped"]} == {2}
    lo, hi = rules.H5_RUN_S
    drift = [srow(11, 3, dur=lo, gain=3.0 * lo / 1000 * rules.DRIFT_MAX_GAIN),
             srow(12, 3, dur=lo, gain=3.0 * lo / 1000 * rules.DRIFT_MAX_GAIN + 1),
             srow(13, 3, dur=lo - 1), srow(14, 3, dur=hi), srow(15, 3, dur=hi + 1)]
    assert th.select_drift(drift, ref_hr=None)["picks"] == [11, 14]
    skipped = {s["id"]: s["reason"] for s in th.select_drift(drift, ref_hr=None)["skipped"]}
    assert set(skipped) == {12, 13, 15} and f"{rules.DRIFT_MAX_GAIN:g} m per km" in skipped[12]


def test_select_history_steady_nearest_rank_percentiles():
    effort = [srow(100 + k, 1, max_hr=190, dur=1500) for k in range(6)]   # 25 min: effort only
    steady = [srow(11 + k, 2, speed=2.5 + 0.1 * k, max_hr=150) for k in range(10)]
    out = th.select_history(effort + steady)
    assert out["effort"] == [100, 101, 102, 103, 104, 105]
    assert out["steady"] == [11, 13, 15, 17, 19, 20]
    assert [t["p"] for t in out["steady_targets"]] == [10, 30, 50, 70, 90, 100]
    ties = [srow(31, 5, max_hr=150), srow(32, 9, max_hr=150), srow(33, 9, max_hr=150), srow(34, 9, dur=6000)]
    assert th.select_history(effort + ties)["steady"] == [32, 33, 31]     # 34 is longer than 90 min


def test_select_drift_alternates_and_continues_on_the_other_side():
    hrs = {1: 141, 2: 138, 3: 142, 4: 145, 5: 150, 6: 155, 7: 160}
    rows = [srow(i, 2 + i, avg_hr=h, dur=4000) for i, h in hrs.items()]
    rows += [srow(8, 12, avg_hr=141, dur=3000), srow(9, 12, avg_hr=141, dur=4000, type_key="treadmill_running"),
             srow(10, 12, avg_hr=141, dur=4000, gain=200.0)]
    out = th.select_drift(rows, ref_hr=142)
    assert out["picks"] == [1, 3, 2, 4, 5, 6]
    assert {s["id"] for s in out["skipped"]} == {8, 9, 10}
    after_race = rows + [srow(11, 14, race=True, dur=1500), dict(srow(12, 14, avg_hr=139, dur=4000),
                                                                 startTimeGMT="2026-11-14 20:00:00")]
    assert 12 not in th.select_drift(after_race, ref_hr=142)["picks"]
    no_ref = th.select_drift(rows, ref_hr=None)
    assert no_ref["picks"] == [2, 3, 4, 5, 7, 6]   # targets 138, 142, 145, 150, 160, then 160 again -> 155
    assert th.select_drift(rows, ref_hr=142, exclude={1})["picks"] == [2, 3, 4, 5, 6, 7]


def prog_table(split=2400, hr1=135, hr2=150):
    return table(4600, lambda t: 3.0, hr_fn=lambda t: hr1 if t <= split else hr2)


def test_fit_span_score_minutes_each_side():
    assert th.fit_span_score(prog_table(), 142) == pytest.approx(348 * 5 / 60)
    assert th.fit_span_score(prog_table(), None) == pytest.approx(757 * 5 / 60)
    assert th.fit_span_score(prog_table(hr1=150), 142) == 0


def test_select_fit_order_cache_no_rr_and_cap():
    def det(rid, day, tbl=None, src="strap"):
        return {"id": rid, "date": date(2026, 10, day), "hr_source": src, "table": tbl or prog_table()}

    runs = [det(1, 5), det(2, 9), det(3, 9, prog_table(hr1=150)), det(4, 10), det(5, 11), det(6, 12, src="wrist")]
    runs.append({**det(7, 1), "date": date(2026, 8, 30)})
    kw = {"ref_hr": 142, "rr_since": date(2026, 9, 1), "unreliable": False}
    assert th.select_fit(runs, cached={4}, no_rr={5}, **kw) == [2, 1]
    assert th.select_fit(runs, cached=set(), no_rr=set(), unreliable=True, ref_hr=142,
                         rr_since=date(2026, 9, 1)) == []
    many = [det(10 + k, 1 + k) for k in range(7)]
    assert th.select_fit(many, cached=set(), no_rr=set(), **kw) == [16, 15, 14, 13, 12, 11]


def test_tie_key_newest_first_then_lowest_id():
    rows = [{"activityId": 3, "startTimeGMT": "2026-11-01 07:00:00"},
            {"activityId": 2, "startTimeGMT": "2026-11-02 07:00:00"},
            {"activityId": 1, "startTimeGMT": "2026-11-01 07:00:00"}]
    assert [r["activityId"] for r in sorted(rows, key=th.tie_key)] == [2, 1, 3]


# --- R: race efforts (methods doc 9b) ---

H2R = {"a": 60.0, "b": 25.0, "v_min": 2.8, "v_max": 3.8}


def race_tbl(secs, dist, *, warm=0, cool=0, hr=170):
    v = dist / secs
    return table(warm + secs + cool, lambda t: v if warm < t <= warm + secs else 2.5, hr_fn=lambda t: hr)


def rlap(start, dur, dist):
    return {**lap(start, dur), "distance": float(dist)}


def analyse(tbl, laps=(), lap_range=None, **kw):
    return th.race(tbl, th.race_effort(tbl, laps=list(laps), lap_range=lap_range), **kw)


def raises_bad(fn, *a, **kw):
    with pytest.raises(th.PaceError) as exc:
        fn(*a, **kw)
    assert exc.value.code == "bad_args"
    return exc.value


def test_race_single_5k_is_a_cs_range():
    out = analyse(race_tbl(1165, 5042), activity=9001, day="2026-11-10", h2=H2R, h2_source="current records")
    assert out["class"] == "cs" and out["status"] == "ok" and out["method"] == "race_cs" and out["fit"] == "single"
    assert out["duration_s"] == 1165 and out["distance_m"] == pytest.approx(5042)
    lo, hi = out["cs_range"]
    assert 4.070 <= lo < 4.071 and 4.199 <= hi < 4.200 and out["d_prime_range"] == [150, 300]
    assert out["cs_mps"] == pytest.approx((5042 - 225) / 1165) and out["d_prime_m"] is None
    assert out["lt2"]["pace_s_per_km"] == 242 and out["lt2_pace_range"] == [246, 238]
    assert out["confidence"] == "low" and out["saveable"] is True
    assert out["points"] == [{"source": "effort", "activity": 9001, "date": "2026-11-10", "duration_s": 1165,
                              "distance_m": pytest.approx(5042)}]
    # CS 4.13 above H2's v_max 3.8 but within 1.1 x: extrapolated and flagged
    assert out["lt2"]["hr"] == 163 and out["flags"] == ["extrapolated"] and out["h2_source"] == "current records"


def test_race_hr_cap_and_missing_h2():
    capped = analyse(race_tbl(1165, 5042), h2={**H2R, "v_max": 3.6})   # 1.1 x 3.6 = 3.96 < CS 4.13
    assert capped["status"] == "ok" and capped["lt2"]["hr"] is None and capped["flags"] == []
    assert any("1.1 x the H2 top speed" in r for r in capped["reasons"])
    inside = analyse(race_tbl(1165, 5042), h2={**H2R, "v_max": 4.2})
    assert inside["lt2"]["hr"] == 163 and inside["flags"] == []
    none = analyse(race_tbl(1165, 5042))
    assert none["lt2"]["hr"] is None and any("no H2" in r for r in none["reasons"])


def test_race_short_single_effort_is_a_point_only():
    out = analyse(race_tbl(300, 1500), activity=9001, h2=H2R)
    assert out["status"] == "point_only" and out["lt2"] is None and out["saveable"] is False
    assert out["cs_mps"] is None and "600 s" in out["reasons"][0]


def test_race_two_effort_fit():
    other = {"source": "effort", "activity": 9002, "date": "2026-10-01", "duration_s": 300, "distance_m": 1495.5}
    out = analyse(race_tbl(1165, 5042), activity=9001, points=[other], h2=H2R)
    assert out["status"] == "ok" and out["fit"] == "points" and len(out["points"]) == 2
    assert out["cs_mps"] == pytest.approx(4.1) and out["d_prime_m"] == pytest.approx(265.5)
    assert out["confidence"] == "medium" and out["cs_range"] is None and out["lt2"]["pace_s_per_km"] == 244
    same = analyse(race_tbl(1165, 5042), activity=9001, points=[{**other, "activity": 9001}], h2=H2R)
    assert same["confidence"] == "low"
    # a 300 s effort with a point of another duration gets an estimate instead of a point only
    assert analyse(race_tbl(300, 1495.5), activity=9002, points=[{**other, "activity": 9001, "duration_s": 1165,
                                                                  "distance_m": 5042}])["status"] == "ok"


def test_race_fit_needs_a_duration_span_and_passes_the_gates():
    near = {"source": "pr", "activity": 9003, "date": "2026-10-01", "duration_s": 1000, "distance_m": 4350}
    out = analyse(race_tbl(1165, 5042), activity=9001, points=[near])
    assert out["fit"] == "single"                                       # 1165 / 1000 < 1.5
    short = {**near, "duration_s": 100, "distance_m": 500}               # below 120 s: not a point
    assert analyse(race_tbl(1165, 5042), points=[short])["fit"] == "single"
    bad_fit = {**near, "duration_s": 300, "distance_m": 1100}            # D' negative
    out = analyse(race_tbl(1165, 5042), activity=9001, points=[bad_fit])
    assert out["status"] == "rejected" and out["lt2"] is None and out["saveable"] is False
    assert "D'" in out["reasons"][0] and out["rejected_fit"]["d_prime_m"] < 0


def test_race_laps_select_the_effort_and_a_warmup_is_refused():
    tbl = race_tbl(1165, 5042, warm=900, cool=300)
    laps = [rlap(0, 900, 2250), rlap(900, 1165, 5042), rlap(2065, 300, 750)]
    err = raises_bad(analyse, tbl, laps)
    assert "lap 1 and lap 3" in err.message and "--laps" in err.hint and "2: 4.33" in err.hint
    out = analyse(tbl, laps, lap_range=(2, 2), h2=H2R)
    assert out["laps"] == [2, 2] and out["bounds"] == [900, 2065] and out["duration_s"] == 1165
    assert out["distance_m"] == pytest.approx(5042) and out["lt2"]["pace_s_per_km"] == 242
    raises_bad(analyse, tbl, laps, lap_range=(3, 4))
    raises_bad(analyse, tbl, [], lap_range=(1, 1))
    # two laps (warm-up, effort): the median falls back to all laps
    err = raises_bad(analyse, race_tbl(1165, 5042, warm=900), laps[:2])
    assert err.message.startswith("lap 1 slower than 0.85 x")
    # auto-laps of an evenly paced effort with a short last lap are not refused
    v = 5042 / 1165
    auto = [rlap(k * 1000 / v, 1000 / v, 1000) for k in range(5)] + [rlap(5000 / v, 42 / v, 42)]
    assert analyse(race_tbl(1165, 5042), auto)["status"] == "ok"
    # laps without distance (or evenly paced) are not refused
    assert analyse(race_tbl(1165, 5042), [lap(0, 600), lap(600, 565)])["status"] == "ok"


def test_race_moving_time_excludes_stops():
    tbl = race_tbl(1165, 5042)
    for r in tbl:
        if 500 <= r["t"] < 530:
            r["speed"] = 0.0
    out = analyse(tbl)
    assert out["excluded_s"] == 30 and out["duration_s"] == 1135


def test_race_shorter_than_120_s_is_refused():
    err = raises_bad(analyse, race_tbl(100, 500))
    assert "120 s" in err.message


def test_race_time_trial_class_uses_the_f2_rules():
    tbl = table(1300, lambda t: 4.0, hr_fn=lambda t: 160 if t < 600 else 170)
    out = analyse(tbl)                                  # no 1680 s minimum
    assert out["class"] == "time_trial" and out["method"] == "race_tt" and out["status"] == "ok"
    assert out["lt2"] == {"hr": 170, "pace_s_per_km": 250} and out["confidence"] == "medium" and out["saveable"]
    fade = analyse(table(2400, lambda t: 4.4 if t <= 1200 else 3.6))
    assert fade["fade"] is True and fade["confidence"] == "low"
    stopped = table(2400, lambda t: 0.0 if 1000 < t <= 1030 else 4.0)
    out = analyse(stopped)
    assert out["status"] == "rejected" and "a stop longer than 10 s" in out["reasons"] and not out["saveable"]
    hilly = table(2400, lambda t: 4.0)
    for r in hilly:
        r["alt"] = r["dist"] * 0.03
    assert th.ASCENT_REASON in analyse(hilly)["reasons"]
    assert analyse(table(1201, lambda t: 4.0))["class"] == "time_trial"
    assert analyse(table(1200, lambda t: 4.0))["class"] == "cs"


def test_race_longer_than_an_hour_is_a_check_only():
    out = analyse(table(3700, lambda t: 3.5, hr_fn=lambda t: 150))
    assert out["status"] == "check_only" and out["class"] == "check" and out["lt2"] is None
    assert out["pace_s_per_km"] == 286 and out["hr"] == 150 and not out["saveable"]
    assert out["reasons"] == ["longer than 60 min: below LT2, a check only"]
    assert analyse(table(3600, lambda t: 3.5))["class"] == "time_trial"
