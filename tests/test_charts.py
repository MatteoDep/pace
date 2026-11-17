import struct
import subprocess
import sys
from datetime import date
from pathlib import Path

from pace.charts import plan as plan_chart
from pace.charts import volume
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
