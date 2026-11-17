import struct
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from pace.charts import plan as plan_chart
from pace.charts import recovery, sleep, volume
from pace.load import aggregate
from pace.matching import match

TODAY = date(2026, 11, 18)  # a Wednesday


def _png_size(path: Path) -> tuple[int, int]:
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", raw[16:24])


def _act(aid: int, day: str, km: float, elev: float = 0.0, typ: str = "running") -> dict:
    return {"activityId": aid, "startTimeLocal": f"{day} 07:00:00", "activityType": typ,
            "distance": km * 1000, "elevationGain": elev, "movingDuration": km * 360}


def _session(sid: int, day: str, km: float | None, *, status: str = "planned", intensity: str = "easy",
             sport: str = "running", elev: float | None = None) -> dict:
    return {"id": sid, "date": day, "title": f"s{sid}", "sport": sport, "intensity": intensity, "status": status,
            "target": {"duration_s": km * 360 if km else None, "distance_m": km * 1000 if km else None,
                       "elevation_m": elev}}


def _volume_data() -> dict:
    acts = [_act(1, "2026-11-03", 10, 50), _act(2, "2026-11-08", 15, 200), _act(3, "2026-11-12", 8, 30),
            _act(4, "2026-11-17", 12, 100), _act(5, "2026-11-04", 30, 500, typ="cycling")]
    sessions = [_session(10, "2026-11-20", 10, elev=80), _session(11, "2026-11-22", 18, elev=300),
                _session(12, "2026-11-25", 12, elev=100)]
    data = aggregate(acts, sessions, start=date(2026, 11, 2), end=date(2026, 11, 29), today=TODAY, sport="running")
    return {**data, "today": TODAY.isoformat()}


def test_volume_png_size_and_summary(tmp_path) -> None:
    data = _volume_data()
    out = tmp_path / "v.png"
    summary = volume.render(data, out, metric="km")
    assert _png_size(out) == (1200, 675)
    assert summary["metric"] == "km" and summary["sport"] == "running" and summary["empty"] is False
    expected = [{"week": w["week"], "actual": w["actual"]["km"] if w["actual"] else None,
                 "projected": w["projected"]["km"]} for w in data["weeks"]]
    assert summary["weeks"] == expected
    assert summary["weeks"][2] == {"week": "2026-W47", "actual": 12.0, "projected": 40.0}
    assert summary["weeks"][3]["actual"] is None
    today_row = next(d for d in data["days"] if d["date"] == TODAY.isoformat())
    assert summary["rolling7_today"] == today_row["rolling7"]["km"]


def test_volume_metric_switch(tmp_path) -> None:
    data = _volume_data()
    summary = volume.render(data, tmp_path / "e.png", metric="elev")
    assert summary["metric"] == "elev"
    assert [w["projected"] for w in summary["weeks"]] == [w["projected"]["elev_m"] for w in data["weeks"]]
    assert summary["weeks"][0]["actual"] == 250.0


def test_volume_rolling7_today_outside_range(tmp_path) -> None:
    data = {**_volume_data(), "today": "2027-01-10"}
    assert volume.render(data, tmp_path / "v.png", metric="hours")["rolling7_today"] is None


def test_plan_status_counts(tmp_path) -> None:
    sessions = [_session(1, "2026-11-10", 10), _session(2, "2026-11-12", 10), _session(3, "2026-11-14", 10),
                _session(4, "2026-11-15", None, intensity="rest"), _session(5, "2026-11-16", 10, status="skipped"),
                _session(6, "2026-11-13", None, sport="strength"), _session(7, "2026-11-22", 12)]
    acts = [_act(100, "2026-11-10", 10), _act(101, "2026-11-12", 5), _act(102, "2026-11-11", 6)]
    matched = match(sessions, acts, today=TODAY)
    data = {**matched, "range": {"from": "2026-11-04", "to": "2026-12-02"}, "today": TODAY.isoformat()}
    out = tmp_path / "p.png"
    summary = plan_chart.render(data, out)
    assert _png_size(out) == (1200, 675)
    expected: dict[str, int] = {}
    for d in matched["days"]:
        for s in d["sessions"]:
            expected[s["status"]] = expected.get(s["status"], 0) + 1
    assert summary["counts"] == expected
    assert expected == {"done": 1, "modified": 1, "missed": 1, "rest": 1, "skipped": 1, "unconfirmed": 1,
                        "planned": 1}
    assert summary["unplanned"] == 1 and summary["empty"] is False


def test_empty_data_note(tmp_path) -> None:
    data = aggregate([], [], start=date(2026, 11, 2), end=date(2026, 11, 29), today=TODAY, sport="running")
    out = tmp_path / "v.png"
    summary = volume.render({**data, "today": TODAY.isoformat()}, out, metric="km")
    assert _png_size(out) == (1200, 675) and summary["empty"] is True

    out = tmp_path / "p.png"
    data = {**match([], [], today=TODAY), "range": {"from": "2026-11-04", "to": "2026-12-02"},
            "today": TODAY.isoformat()}
    summary = plan_chart.render(data, out)
    assert _png_size(out) == (1200, 675)
    assert summary == {"counts": {}, "unplanned": 0, "empty": True}


def test_import_is_lazy() -> None:
    code = ("import sys, pace.cli, pace.charts, pace.commands.chart, pace.commands.schema; "
            "assert 'matplotlib' not in sys.modules, 'matplotlib imported'")
    subprocess.run([sys.executable, "-c", code], check=True)


def _night(day: date, *, deep=2760, light=18480, rem=9300, awake=180, rhr=45, hrv=None, hrv_7d=None, score=None):
    return {"date": day.isoformat(), "sleep_s": None if deep is None else deep + light + rem, "deep_s": deep,
            "light_s": light, "rem_s": rem, "awake_s": awake, "rhr": rhr, "avg_hr": 52, "hrv": hrv,
            "hrv_7d": hrv_7d, "score": score}


def _sleep_data(**kw) -> dict:
    rows = [_night(date(2026, 11, 4) + timedelta(days=i), rhr=44 + i % 3, **kw) for i in range(14)]
    return {"rows": rows, "range": {"from": "2026-11-04", "to": "2026-11-17"}, "today": TODAY.isoformat()}


def test_recovery_hides_hrv_when_null(tmp_path) -> None:
    out = tmp_path / "r.png"
    summary = recovery.render(_sleep_data(), out)
    assert _png_size(out) == (1200, 675)
    assert summary["hrv_available"] is False and summary["empty"] is False
    assert len(summary["days"]) == 14 and "hrv" not in summary["days"][0]
    assert summary["days"][0]["rhr_7d"] == 44.0
    assert summary["days"][6]["rhr_7d"] == round((44 + 45 + 46 + 44 + 45 + 46 + 44) / 7, 2)
    assert summary["rhr_7d_latest"] == summary["days"][-1]["rhr_7d"]


def test_recovery_shows_hrv_with_values(tmp_path) -> None:
    out = tmp_path / "r.png"
    summary = recovery.render(_sleep_data(hrv=62, hrv_7d=60), out)
    assert summary["hrv_available"] is True
    assert summary["days"][0]["hrv"] == 62 and summary["days"][0]["hrv_7d"] == 60
    assert _png_size(out) == (1200, 675)


def test_recovery_lead_in_rows_feed_mean_but_are_not_shown(tmp_path) -> None:
    data = _sleep_data()
    data["rows"] = [_night(date(2026, 11, 1), rhr=50), *data["rows"]]
    summary = recovery.render(data, tmp_path / "r.png")
    assert summary["days"][0]["date"] == "2026-11-04"
    assert summary["days"][0]["rhr_7d"] == round((50 + 44) / 2, 2)


def test_sleep_chart_summary(tmp_path) -> None:
    out = tmp_path / "s.png"
    data = _sleep_data()
    data["rows"][3] = _night(date(2026, 11, 7), deep=None, light=None, rem=None, awake=None, rhr=None)
    summary = sleep.render(data, out)
    assert _png_size(out) == (1200, 675)
    assert len(summary["nights"]) == 14 and summary["empty"] is False and summary["score_available"] is False
    assert summary["nights"][0] == {"date": "2026-11-04", "sleep_h": 8.48, "deep_h": 0.77, "light_h": 5.13,
                                    "rem_h": 2.58, "awake_h": 0.05, "score": None}
    assert summary["nights"][3]["sleep_h"] is None
    assert summary["sleep_7d_mean_h"] == 8.48
    scored = sleep.render(_sleep_data(score=80), tmp_path / "s2.png")
    assert scored["score_available"] is True and scored["nights"][0]["score"] == 80


def test_recovery_empty_note(tmp_path) -> None:
    data = {"rows": [], "range": {"from": "2026-11-04", "to": "2026-11-17"}, "today": TODAY.isoformat()}
    out = tmp_path / "r.png"
    summary = recovery.render(data, out)
    assert _png_size(out) == (1200, 675)
    assert summary == {"days": [], "rhr_7d_latest": None, "hrv_available": False, "empty": True}
    assert sleep.render(data, tmp_path / "s.png")["empty"] is True


def test_volume_without_plan_stops_line_at_today(tmp_path) -> None:
    acts = [_act(1, "2026-11-03", 10), _act(4, "2026-11-17", 12)]
    data = aggregate(acts, [], start=date(2026, 11, 2), end=date(2026, 11, 29), today=TODAY, sport="running")
    data = {**data, "today": TODAY.isoformat()}
    summary = volume.render(data, tmp_path / "v.png", metric="km")
    assert summary["plan_ahead"] is False and summary["empty"] is False
    assert volume.render(_volume_data(), tmp_path / "v2.png", metric="km")["plan_ahead"] is True


def test_volume_no_dashed_segment_without_plan(tmp_path, monkeypatch) -> None:
    from matplotlib.axes import Axes

    styles = []
    orig = Axes.plot

    def spy(self, *a, **kw):
        styles.append(kw.get("linestyle"))
        return orig(self, *a, **kw)

    monkeypatch.setattr(Axes, "plot", spy)
    acts = [_act(4, "2026-11-17", 12)]
    data = aggregate(acts, [], start=date(2026, 11, 2), end=date(2026, 11, 29), today=TODAY, sport="running")
    volume.render({**data, "today": TODAY.isoformat()}, tmp_path / "v.png", metric="km")
    assert styles == [None]
    styles.clear()
    volume.render(_volume_data(), tmp_path / "v2.png", metric="km")
    assert len(styles) == 2 and styles[1] is not None


def test_volume_current_week_label_hidden_when_zero(tmp_path, monkeypatch) -> None:
    from matplotlib.axes import Axes

    labels = []
    orig = Axes.annotate

    def spy(self, text, *a, **kw):
        labels.append(text)
        return orig(self, text, *a, **kw)

    monkeypatch.setattr(Axes, "annotate", spy)
    data = aggregate([_act(1, "2026-11-03", 10)], [], start=date(2026, 11, 2), end=date(2026, 11, 29), today=TODAY,
                     sport="running")
    volume.render({**data, "today": TODAY.isoformat()}, tmp_path / "v.png", metric="km")
    assert not any("/" in t for t in labels)
    volume.render(_volume_data(), tmp_path / "v2.png", metric="km")
    assert any("/" in t for t in labels)


def test_weight_trailing_mean(tmp_path) -> None:
    from pace.charts import weight

    day = date(2026, 11, 10)
    rows = [{"date": (day - timedelta(days=d)).isoformat(), "kg": kg}
            for d, kg in ((8, 70.0), (6, 69.0), (3, 68.0), (0, 67.0))]
    rows.sort(key=lambda r: r["date"])
    data = {"rows": rows, "range": {"from": (day - timedelta(days=3)).isoformat(), "to": day.isoformat()},
            "today": day.isoformat()}
    out = tmp_path / "w.png"
    summary = weight.render(data, out)
    assert _png_size(out) == (1200, 675)
    # Nov 7 averages the lead-in rows 70, 69 and 68 (Nov 1-7); Nov 10 covers Nov 4-10: 69, 68, 67
    assert [p["kg_7d"] for p in summary["points"]] == [69.0, 68.0]
    assert summary["first"] == 68.0 and summary["last"] == 67.0 and summary["mean"] == 67.5
    assert summary["count"] == 2 and summary["empty"] is False


def test_weight_empty_note(tmp_path) -> None:
    from pace.charts import weight

    day = date(2026, 11, 10)
    out = tmp_path / "w.png"
    summary = weight.render({"rows": [], "range": {"from": "2026-11-01", "to": day.isoformat()},
                             "today": day.isoformat()}, out)
    assert _png_size(out) == (1200, 675)
    assert summary == {"points": [], "first": None, "last": None, "mean": None, "count": 0, "empty": True}


def _zoned(aid: int, day: str, typ: str, zones: tuple[float, ...]) -> dict:
    return {"activityId": aid, "startTimeLocal": f"{day} 07:00:00", "activityType": typ,
            **{f"hrTimeInZone_{i}": s for i, s in enumerate(zones, 1)}}


def test_intensity_zone_sums(tmp_path) -> None:
    from pace.charts import intensity

    data = {"activities": [_zoned(1, "2026-11-10", "running", (600, 1200, 300, 0, 0)),
                           _zoned(2, "2026-11-12", "trail_running", (0, 600, 600, 60, 0)),
                           _zoned(3, "2026-11-11", "cycling", (3600, 0, 0, 0, 0))],
            "sessions": [_session(1, "2026-11-11", None, intensity="easy"),
                         _session(2, "2026-11-13", None, intensity="hard"),
                         _session(3, "2026-11-14", None, intensity="easy", status="skipped")],
            "sport": "running", "range": {"from": "2026-11-09", "to": "2026-11-15"}, "today": "2026-11-12"}
    data["sessions"][0]["target"] = {"duration_s": 3000}
    out = tmp_path / "i.png"
    summary = intensity.render(data, out)
    assert _png_size(out) == (1200, 675)
    [week] = summary["weeks"]
    assert week["week"] == "2026-W46" and week["zones_s"] == [600, 1800, 900, 60, 0]
    assert week["planned_s"] == {"easy": 3000, "moderate": 0, "hard": 0} and week["planned_missing"] == 1
    assert week["low_share"] == round(2400 / 3360, 2)
    everything = intensity.render({**data, "sport": "all"}, out)
    assert everything["weeks"][0]["zones_s"][0] == 4200


def _details(speeds: list[float]) -> dict:
    keys = ("directTimestamp", "directHeartRate", "directSpeed", "sumDistance", "directElevation")
    rows = []
    for i, v in enumerate(speeds):
        rows.append({"metrics": [1_700_000_000_000 + i * 60_000, 140 + i, v, sum(speeds[:i]) * 60.0, 100 + i]})
    return {"metricDescriptors": [{"key": k, "metricsIndex": i} for i, k in enumerate(keys)],
            "activityDetailMetrics": rows}


def _activity_data(family_: str, speeds: list[float]) -> dict:
    return {"details": _details(speeds), "family": family_, "id": 9, "range": {"from": "2026-11-10", "to": "2026-11-10"},
            "info": {"summaryDTO": {"distance": 3000.0, "movingDuration": 900.0, "averageSpeed": 3.3333,
                                    "averageHR": 150, "elevationGain": 12.0}}}


def test_activity_chart_running_pace(tmp_path) -> None:
    from pace.charts import activity

    out = tmp_path / "a.png"
    summary = activity.render(_activity_data("running", [3.0, 3.5, 0.2, 3.3, 3.4]), out)
    assert _png_size(out) == (1200, 675)
    assert summary["avg_pace_min_km"] == 5.0 and "avg_speed_kmh" not in summary
    assert summary["distance_km"] == 3.0 and summary["moving_time_s"] == 900
    assert summary["avg_hr"] == 150 and summary["elevation_gain_m"] == 12 and summary["empty"] is False


def test_activity_chart_cycling_speed(tmp_path) -> None:
    from pace.charts import activity

    out = tmp_path / "a.png"
    summary = activity.render(_activity_data("cycling", [6.0, 7.0, 8.0]), out)
    assert _png_size(out) == (1200, 675)
    assert summary["avg_speed_kmh"] == 12.0 and "avg_pace_min_km" not in summary
