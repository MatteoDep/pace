"""FIT parsing, RR alignment (plan 5 Task 0 rule), the derived-JSON cache and no_rr.json."""

import asyncio
import copy
import io
import json
import os
import stat
import struct
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fitdecode.utils import compute_crc

from pace import fit
from pace.api import ApiError
from pace.cache import CacheTracker
from pace.envelope import PaceError

FIXTURE = Path(__file__).parent / "fixtures" / "fit_rr_small.json"
FIT_EPOCH = 631065600  # 1989-12-31T00:00:00Z


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path / "home")
    return tmp_path / "home"


def messages() -> list[tuple[str, dict]]:
    return [(n, f) for n, f in json.loads(FIXTURE.read_text())["messages"]]


# --- a minimal FIT encoder (synthetic files only) ---

def encode_fit(msgs: list[tuple[int, list[tuple[int, int, str, object]]]]) -> bytes:
    """msgs: (global message number, [(field number, base type, struct format, value)])."""
    body = b""
    for gnum, fields in msgs:
        body += struct.pack("<BBBHB", 0x40, 0, 0, gnum, len(fields))
        for num, base, fmt, _ in fields:
            body += struct.pack("<BBB", num, struct.calcsize("<" + fmt), base)
        body += b"\x00" + b"".join(
            struct.pack("<" + fmt, *(v if isinstance(v, tuple) else (v,))) for _, _, fmt, v in fields)
    header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(body), b".FIT")
    header += struct.pack("<H", compute_crc(header))
    out = header + body
    return out + struct.pack("<H", compute_crc(out))


def fit_ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp()) - FIT_EPOCH


def synthetic_fit(*, with_hrv: bool = True) -> bytes:
    start = fit_ts("2026-10-01T08:00:00+00:00")
    msgs = [(23, [(3, 0x8C, "I", 123456789)])]  # device_info serial_number: must be dropped
    for i in range(4):
        msgs.append((20, [(253, 0x86, "I", start + i), (3, 0x02, "B", 150), (5, 0x86, "I", 300 * i),
                          (73, 0x86, "I", 3000), (78, 0x86, "I", 2560), (4, 0x02, "B", 89), (53, 0x02, "B", 64),
                          (0, 0x85, "i", 455000000), (1, 0x85, "i", 120000000), (13, 0x01, "b", 21)]))
        if with_hrv and i % 2 == 1:
            msgs.append((78, [(0, 0x84, "5H", (400, 400, 400, 400, 0xFFFF))]))
    msgs.append((18, [(2, 0x86, "I", start)]))  # session last, as on the watch
    return encode_fit(msgs)


def zipped(name: str, data: bytes, extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, data)
        for k, v in (extra or {}).items():
            zf.writestr(k, v)
    return buf.getvalue()


def corrupt_deflate() -> bytes:
    """A zip whose deflated .fit member has damaged compressed bytes (zlib.error on read)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.fit", synthetic_fit() * 3)
    data = bytearray(buf.getvalue())
    start = 30 + len("a.fit")  # local header + name, no extra field
    for i in range(start, start + 20):
        data[i] ^= 0xFF
    return bytes(data)


# --- parse_messages ---

def test_parse_messages_fixture() -> None:
    out = fit.parse_messages(messages())
    assert out["start"] == "2026-10-01T08:00:00+00:00"
    recs = out["records"]
    assert len(recs) == 50
    assert recs[0] == {"t": 0, "hr": 150, "speed": 3.0, "dist": 0.0, "alt": 12.0, "cadence": 179.0}
    assert [r["t"] for r in recs[29:31]] == [29, 50]  # the pause leaves a gap on the elapsed clock
    assert recs[-1]["t"] == 69 and recs[-1]["hr"] == 152
    assert len(out["rr_s"]) == 125 and set(out["rr_s"]) == {0.4}
    assert out["rr_msgs"][0] == [1, 5] and out["rr_msgs"][15] == [51, 5] and len(out["rr_msgs"]) == 25


def test_parse_messages_prefers_enhanced_and_drops_gps_and_devices() -> None:
    msgs = messages()
    rec = copy.deepcopy(msgs[2][1])
    assert msgs[2][0] == "record"
    rec.update(speed=2.0, enhanced_speed=3.25, altitude=5.0, enhanced_altitude=12.5, position_lat=1, position_long=2,
               temperature=21, fractional_cadence=0.25)
    plain = {k: v for k, v in rec.items() if not k.startswith("enhanced_")}
    plain["timestamp"] = "2026-10-01T08:00:01+00:00"
    out = fit.parse_messages([("device_info", {"serial_number": 42}), ("record", rec), ("record", plain),
                              ("hrv", {"time": [0.4, None, 65.535]})])
    a, b = out["records"]
    assert a == {"t": 0, "hr": 150, "speed": 3.25, "dist": 0.0, "alt": 12.5, "cadence": 178.5, "temp": 21}
    assert b["speed"] == 2.0 and b["alt"] == 5.0 and b["t"] == 1
    assert out["start"] == "2026-10-01T08:00:00+00:00"  # no session: first record timestamp
    assert out["rr_s"] == [0.4] and out["rr_msgs"] == [[1, 1]]  # padding (None, 0xFFFF) dropped
    assert "42" not in json.dumps(out)


def test_parse_messages_accepts_datetimes() -> None:
    t0 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    out = fit.parse_messages([("record", {"timestamp": t0.replace(second=5), "heart_rate": 140}),
                              ("session", {"start_time": t0})])
    assert out["start"] == "2026-10-01T08:00:00+00:00" and out["records"][0]["t"] == 5
    assert out["records"][0]["cadence"] is None and out["rr_s"] == [] and out["rr_msgs"] == []


# --- aligned_rr (Task 0 rule) ---

def test_aligned_rr_fixture() -> None:
    beats = fit.aligned_rr(fit.parse_messages(messages()))
    assert len(beats) == 125
    assert beats[0] == {"t_s": pytest.approx(1.4), "rr_s": 0.4, "hr": 150, "speed": 3.0, "dist": 3.0, "alt": 12.0,
                        "cadence": 179.0, "gap": False, "stop": False}
    assert beats[74]["t_s"] == pytest.approx(31.0)  # last beat before the pause, no resync while the offset <= 3 s
    assert beats[75]["t_s"] == pytest.approx(51.4) and beats[75]["gap"] is True  # one resync, after the pause
    assert beats[75]["hr"] == 152 and beats[75]["speed"] == 3.1
    assert [i for i, b in enumerate(beats) if b["gap"]] == [75]
    assert beats[-1]["t_s"] == pytest.approx(71.0)
    assert beats[70]["stop"] is False and beats[74]["stop"] is True  # 30.6..31.0 s lies in the 29 -> 50 s record gap


def test_aligned_rr_resyncs_on_drift_and_flags_long_rr() -> None:
    recs = [{"t": t, "hr": 120, "speed": 3.0, "dist": 3.0 * t, "alt": 1.0, "cadence": 170.0} for t in range(300)]
    rr, msgs = [], []
    for m in range(149):  # one message per 2 s, anchored at records 1, 3, ..., 297
        vals = [2.1] if m == 2 else [0.5, 0.5, 0.5] if m % 20 == 19 else [0.5] * 4  # every 20th misses a beat
        rr += vals
        msgs.append([1 + 2 * m, len(vals)])
    beats = fit.aligned_rr({"start": "2026-10-01T08:00:00+00:00", "records": recs, "rr_s": rr, "rr_msgs": msgs})
    assert len(beats) == len(rr)
    long_rr = next(i for i, b in enumerate(beats) if b["rr_s"] == 2.1)
    resync = next(i for i, b in enumerate(beats) if b["t_s"] == pytest.approx(281.5))
    # 7 missed beats leave the clock 3.4 s behind the records at message 140: one resync to record 281
    assert [i for i, b in enumerate(beats) if b["gap"]] == [long_rr, resync]
    assert beats[resync - 1]["t_s"] == pytest.approx(277.6) and beats[resync]["hr"] == 120


def test_aligned_rr_backward_resync_keeps_beats_sorted() -> None:
    recs = [{"t": t, "hr": 120, "speed": 3.0, "dist": 3.0 * t, "alt": 1.0, "cadence": 170.0} for t in range(300)]
    rr, msgs = [], []
    for m in range(149):
        vals = [0.5] * 12 if m == 50 else [0.5] * 4  # message 50 carries 4 s of extra beats
        rr += vals
        msgs.append([1 + 2 * m, len(vals)])
    beats = fit.aligned_rr({"start": "2026-10-01T08:00:00+00:00", "records": recs, "rr_s": rr, "rr_msgs": msgs})
    times = [b["t_s"] for b in beats]
    assert len(beats) == len(rr) and times == sorted(times)
    # the clock is 4 s ahead at message 51 (anchor 103): it jumps back from 107 to 103
    gaps = [b["t_s"] for b in beats if b["gap"]]
    assert gaps == pytest.approx([103.5, 104.0, 104.5, 105.0, 105.5, 106.0, 106.5, 107.0])
    assert not any(b["gap"] for b in beats if b["t_s"] > 107.0 or b["t_s"] < 103.0)


def test_aligned_rr_sanity_rejects() -> None:
    data = fit.parse_messages(messages())
    data["rr_s"] = [v * 1.03 for v in data["rr_s"]]  # 3% more beat time than the record span
    with pytest.raises(fit.RRRejected, match="span"):
        fit.aligned_rr(data)
    data = fit.parse_messages(messages())
    for r in data["records"]:
        r["hr"] = 160  # RR says 150 bpm
    with pytest.raises(fit.RRRejected, match="bpm"):
        fit.aligned_rr(data)
    assert fit.aligned_rr({"start": "x", "records": data["records"], "rr_s": [], "rr_msgs": []}) == []


# --- read_fit_bytes ---

def test_read_fit_bytes_from_zip_drops_gps_and_serials() -> None:
    out = fit.read_fit_bytes(zipped("123_ACTIVITY.fit", synthetic_fit()))
    assert out["start"] == "2026-10-01T08:00:00+00:00"
    assert out["records"][1] == {"t": 1, "hr": 150, "speed": 3.0, "dist": 3.0, "alt": 12.0, "cadence": 179.0,
                                 "temp": 21}
    assert out["rr_s"] == [0.4] * 8 and out["rr_msgs"] == [[1, 4], [3, 4]]
    text = json.dumps(out)
    assert "123456789" not in text and "455000000" not in text and "lat" not in text


def test_read_fit_bytes_path_traversal_member_never_touches_disk(tmp_path, monkeypatch) -> None:
    work = tmp_path / "a" / "b"
    work.mkdir(parents=True)
    monkeypatch.chdir(work)
    before = sorted(p for p in tmp_path.rglob("*"))
    out = fit.read_fit_bytes(zipped("../../evil.fit", synthetic_fit()))
    assert len(out["records"]) == 4
    assert sorted(p for p in tmp_path.rglob("*")) == before


def test_read_fit_bytes_errors() -> None:
    assert len(fit.read_fit_bytes(synthetic_fit())["records"]) == 4  # a bare FIT file is accepted too
    for bad in (b"not a fit file at all", zipped("a.txt", b"x"),
                zipped("a.fit", synthetic_fit(), {"b.fit": synthetic_fit()}),
                zipped("a.fit", synthetic_fit()[:-7]), corrupt_deflate()):
        with pytest.raises(fit.FitFileError):
            fit.read_fit_bytes(bad)


# --- get_fit and no_rr ---

@pytest.fixture
def fake_api(monkeypatch):
    state = {"calls": [], "payload": zipped("1.fit", synthetic_fit())}

    async def safe_call(method_name, *args, **kwargs):
        state["calls"].append((method_name, args, kwargs))
        p = state["payload"]
        if isinstance(p, ApiError):
            return False, None, p
        return True, p, None

    monkeypatch.setattr("pace.api.safe_call", safe_call)
    return state


def test_get_fit_downloads_once_and_caches_atomically(fake_api, _home) -> None:
    tracker = CacheTracker()
    out = asyncio.run(fit.get_fit(9001, tracker=tracker))
    assert len(fake_api["calls"]) == 1
    method, args, _ = fake_api["calls"][0]
    assert method == "download_activity" and args[0] == "9001" and "ORIGINAL" in str(args[1])
    path = _home / "fit" / "9001.json"
    assert json.loads(path.read_text()) == out
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((_home / "fit").stat().st_mode) == 0o700
    assert sorted(p.name for p in (_home / "fit").iterdir()) == ["9001.json"]  # no tmp left
    assert tracker.report() == {"hit": False, "oldest_age_s": 0}
    again = CacheTracker()
    assert asyncio.run(fit.get_fit(9001, tracker=again)) == out
    assert len(fake_api["calls"]) == 1 and again.report()["hit"] is True
    assert fit.no_rr(9001) is None


def test_get_fit_uses_existing_cache_without_a_call(fake_api, _home) -> None:
    d = fit.fit_dir()
    assert d == _home / "fit" and stat.S_IMODE(d.stat().st_mode) == 0o700
    (d / "77.json").write_text(json.dumps({"start": "s", "records": [], "rr_s": [0.4], "rr_msgs": [[0, 1]]}))
    assert asyncio.run(fit.get_fit(77, tracker=None))["rr_s"] == [0.4]
    assert fake_api["calls"] == []


def test_get_fit_parse_error_caches_nothing(fake_api, _home) -> None:
    fake_api["payload"] = zipped("1.fit", b"garbage" * 10)
    with pytest.raises(PaceError) as exc:
        asyncio.run(fit.get_fit(5, tracker=None))
    assert exc.value.code == "internal" and "5" in str(exc.value)
    assert not (_home / "fit" / "5.json").exists() and fit.no_rr(5) is None


def test_get_fit_api_error(fake_api, _home) -> None:
    fake_api["payload"] = ApiError("not_found", "nope")
    with pytest.raises(PaceError) as exc:
        asyncio.run(fit.get_fit(5, tracker=None))
    assert exc.value.code == "not_found"
    assert not (_home / "fit" / "5.json").exists()


def test_get_fit_without_hrv_marks_no_rr(fake_api, _home) -> None:
    fake_api["payload"] = zipped("1.fit", synthetic_fit(with_hrv=False))
    out = asyncio.run(fit.get_fit(6, tracker=None))
    assert out["rr_s"] == [] and len(out["records"]) == 4  # records still cached (temperature, H5)
    assert fit.no_rr(6) == "no_hrv_messages"
    assert json.loads((_home / "fit" / "no_rr.json").read_text()) == {"6": "no_hrv_messages"}


def test_get_fit_skips_known_no_rr_without_cache(fake_api, _home) -> None:
    fit.mark_no_rr(8, "rr_rejected")
    assert asyncio.run(fit.get_fit(8, tracker=None)) is None
    assert fake_api["calls"] == []


def test_mark_no_rr_round_trip(_home) -> None:
    assert fit.no_rr(1) is None
    fit.mark_no_rr(1, "rr_rejected")
    fit.mark_no_rr(2, "no_hrv_messages")
    fit.mark_no_rr(3, "fit_unreadable")
    assert fit.no_rr(1) == "rr_rejected" and fit.no_rr(2) == "no_hrv_messages" and fit.no_rr(3) == "fit_unreadable"
    path = _home / "fit" / "no_rr.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        fit.mark_no_rr(3, "other")
    assert os.path.exists(path)
