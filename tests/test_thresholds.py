import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

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
    kept, excluded = th.moving_samples(_slow(12))
    assert excluded == 12 and len(kept) == 28
    assert all(not (10 <= r["t"] < 22) for r in kept)


def test_moving_samples_9_s_is_not_a_stop():
    kept, excluded = th.moving_samples(_slow(9))
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
        r["alt"] = r["t"] * 0.5                       # 0.5 m/s climb on 2-5 m/s: grade > 2%
    walk = table(1400, effort(600, 780, 5.0), cadence=120)
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
    out = th.drift_segment(run(2, steady_table(2400)), fit_records=None)
    assert out["id"] == 2 and out["rejected"] == "segment shorter than 2700 s"


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


def test_h5_segment_hilly_rejected():
    tbl = table(3900, lambda t: 2.0 if t <= 900 else 3.0)
    for r in tbl:   # grade 1.5% (flat enough per window) but 15 m of ascent per km (> 10)
        r["alt"] = r["dist"] * 0.015
    out = th.drift_segment(run(6, tbl), fit_records=None)
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
    assert out["status"] == "rejected" and any("shorter than 2400 s" in r for r in out["reasons"])


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
    assert out["status"] == "rejected" and out["excluded_s"] == 20 and any("10 s" in r for r in out["reasons"])
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
