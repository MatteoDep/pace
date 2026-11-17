import json
from pathlib import Path

import pytest

from pace import thresholds as th

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
