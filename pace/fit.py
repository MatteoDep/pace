"""FIT files: download, in-memory parsing to derived JSON, RR alignment, and the no-RR memory.

Only derived JSON is stored, at $PACE_HOME/fit/<id>.json (`start`, per-second records without GPS, RR values and
the position of each `hrv` message; no device or serial fields). The original zip is read in memory and never
extracted. $PACE_HOME/fit/no_rr.json maps activity ids to `no_hrv_messages` | `rr_rejected` | `fit_unreadable`.
Alignment rule: plan 5 "Task 0 results" and methods doc section 6.
"""

import bisect
import io
import struct
import time
import zipfile
import zlib
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

import fitdecode
from garminconnect import Garmin

from pace import api, config, store
from pace.cache import CacheTracker
from pace.envelope import PaceError
from pace.rules import GAP_RR_S, STOP_GAP_S, STOP_SLOW_S, STOP_SPEED_MPS

NO_RR_REASONS = ("no_hrv_messages", "rr_rejected", "fit_unreadable")
RR_INVALID_S = 65.5          # 0xFFFF / 1000: FIT padding
RESYNC_S = 3.0               # beat clock vs record clock tolerance
SANITY_SPAN_FRAC = 0.02
SANITY_BPM = 5.0
MAX_FIT_BYTES = 64 * 1024 * 1024
_KEEP = ("session", "record", "hrv")


class FitFileError(ValueError):
    """The downloaded file is not a readable FIT file (or zip with a single .fit member)."""


class FitUnreadable(PaceError):
    """`internal` error for a downloaded file that cannot be parsed; only this marks a run `fit_unreadable`
    (any other `internal` error on a download is transient and marks nothing)."""

    def __init__(self, activity_id: int, detail: object) -> None:
        super().__init__("internal", f"FIT file of activity {activity_id} unreadable: {detail}",
                         hint="Nothing was cached; the run is skipped for RR methods.")


class RRRejected(ValueError):
    """The RR series fails the section 6 sanity check; the caller marks the run `rr_rejected`."""


def fit_dir() -> Path:
    d = config.PACE_HOME / "fit"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


# --- parsing ---

def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return None


def _seconds(delta: float) -> float | int:
    return int(delta) if float(delta).is_integer() else delta


def _first(fields: dict[str, Any], *names: str) -> Any:
    for name in names:
        if fields.get(name) is not None:
            return fields[name]
    return None


def parse_messages(messages: Iterable[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """Pure: (name, fields) FIT data messages -> derived JSON.

    `t` is elapsed seconds since the session start (pauses leave gaps); speed and altitude prefer the enhanced
    fields; cadence is steps/min = 2 x (cadence + fractional_cadence); GPS and device fields are dropped.
    `rr_msgs` holds one [t of the preceding record, number of RR values] per `hrv` message (the alignment input).
    """
    start: datetime | None = None
    raw: list[tuple[datetime, dict[str, Any]]] = []
    rr: list[float] = []
    anchors: list[tuple[int, int]] = []
    for name, fields in messages:
        if name == "session" and start is None:
            start = _dt(fields.get("start_time"))
        elif name == "record":
            ts = _dt(fields.get("timestamp"))
            if ts is not None:
                raw.append((ts, fields))
        elif name == "hrv":
            values = fields.get("time")
            values = values if isinstance(values, (list, tuple)) else [values]
            vals = [float(v) for v in values if v is not None and 0 < v < RR_INVALID_S]
            if vals:
                rr.extend(vals)
                anchors.append((len(raw) - 1, len(vals)))
    if start is None and raw:
        start = raw[0][0]
    records = []
    for ts, f in raw:
        cad = f.get("cadence")
        rec = {"t": _seconds((ts - start).total_seconds()), "hr": f.get("heart_rate"),
               "speed": _first(f, "enhanced_speed", "speed"), "dist": f.get("distance"),
               "alt": _first(f, "enhanced_altitude", "altitude"),
               "cadence": None if cad is None else 2 * (cad + (f.get("fractional_cadence") or 0))}
        if f.get("temperature") is not None:
            rec["temp"] = f["temperature"]
        records.append(rec)
    rr_msgs = [[records[i]["t"] if i >= 0 else 0, n] for i, n in anchors]
    return {"start": start.isoformat() if start else None, "records": records, "rr_s": rr, "rr_msgs": rr_msgs}


def _messages(data: bytes) -> Iterator[tuple[str, dict[str, Any]]]:
    reader = fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.RAISE,
                                 error_handling=fitdecode.ErrorHandling.RAISE)
    with reader:
        for frame in reader:
            if isinstance(frame, fitdecode.FitDataMessage) and frame.name in _KEEP:
                yield frame.name, {f.name: f.value for f in frame.fields}


def read_fit_bytes(data: bytes) -> dict[str, Any]:
    """Thin fitdecode adapter: a zip (its single .fit member, read in memory) or a bare FIT file."""
    fit_bytes = data
    if zipfile.is_zipfile(io.BytesIO(data)):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                members = [i for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".fit")]
                if len(members) != 1:
                    raise FitFileError(f"expected one .fit member in the zip, found {len(members)}")
                if members[0].file_size > MAX_FIT_BYTES:
                    raise FitFileError("the .fit member is larger than 64 MB")
                fit_bytes = zf.read(members[0])
        except (zipfile.BadZipFile, OSError, EOFError, zlib.error, RuntimeError, NotImplementedError) as exc:
            raise FitFileError(f"bad zip: {exc}") from None
    try:
        return parse_messages(list(_messages(fit_bytes)))
    except (fitdecode.FitError, ValueError, KeyError, IndexError, TypeError, struct.error, EOFError) as exc:
        raise FitFileError(f"{type(exc).__name__}: {exc}") from None


# --- alignment ---

def _stops(records: list[dict[str, Any]], dt: float) -> list[tuple[float, float]]:
    """Section 1 stops as [from, to) intervals: record gaps > 5 s, and > 10 s stretches below 1.0 m/s."""
    out = []
    for a, b in zip(records, records[1:], strict=False):
        if b["t"] - a["t"] > STOP_GAP_S:
            out.append((a["t"] + dt, b["t"]))
    run: list[dict[str, Any]] = []
    for r in [*records, None]:
        if r is not None and r["speed"] is not None and r["speed"] < STOP_SPEED_MPS:
            run.append(r)
            continue
        if run and run[-1]["t"] + dt - run[0]["t"] > STOP_SLOW_S:
            out.append((run[0]["t"], run[-1]["t"] + dt))
        run = []
    return sorted(out)


def _sanity(records: list[dict[str, Any]], rr: list[float], msgs: list[list[float]], dt: float) -> None:
    """Section 6 sanity: RR between the first and last anchors vs that record span; mean RR HR vs record HR."""
    r0, r_last = msgs[0][0], msgs[-1][0]
    rr_sum = sum(rr[:len(rr) - int(msgs[-1][1])])  # beats of the last message lie after the last anchor
    pause = sum(max(0.0, min(b["t"], r_last) - a["t"] - dt)
                for a, b in zip(records, records[1:], strict=False)
                if b["t"] - a["t"] > STOP_GAP_S and r0 <= a["t"] < r_last)
    span = r_last - r0 - pause
    if span > 0 and abs(rr_sum - span) > SANITY_SPAN_FRAC * span:
        raise RRRejected(f"RR sum {rr_sum:.1f} s differs by more than {SANITY_SPAN_FRAC:.0%} from the record "
                         f"span {span:.1f} s")
    hrs = [r["hr"] for r in records if r0 <= r["t"] <= r_last and r["hr"] is not None]
    if hrs:
        rr_hr, rec_hr = 60 / (sum(rr) / len(rr)), sum(hrs) / len(hrs)
        if abs(rr_hr - rec_hr) > SANITY_BPM:
            raise RRRejected(f"RR heart rate {rr_hr:.1f} bpm differs from the record heart rate {rec_hr:.1f} bpm "
                             f"by more than {SANITY_BPM:g} bpm")


def aligned_rr(fit: dict[str, Any]) -> list[dict[str, Any]]:
    """Beats on the elapsed clock (Task 0 rule), each with the record values at its time.

    The beat clock starts at the t of the record preceding the first `hrv` message and adds each RR; at each
    later `hrv` message it resyncs to the preceding record's t when the offset exceeds 3 s (marking a gap).
    After a backward resync (clock ahead), the beats that overlap earlier beats are gaps too, and the output is
    sorted by `t_s`.
    `gap` is also set on raw RR > 2 s; `stop` marks beats inside a section 1 stop. Raises RRRejected on the
    section 6 sanity check. Returns [] without RR data.
    """
    records, rr, msgs = fit["records"], fit["rr_s"], fit.get("rr_msgs") or []
    if not rr or not records or not msgs:
        return []
    if sum(int(n) for _, n in msgs) != len(rr):
        raise RRRejected("hrv message positions do not match the RR values")
    rt = [r["t"] for r in records]
    dt = median([b - a for a, b in zip(rt, rt[1:], strict=False)] or [1.0])
    _sanity(records, rr, msgs, dt)
    stops = _stops(records, dt)
    starts = [a for a, _ in stops]
    out: list[dict[str, Any]] = []
    tb: float | None = None
    overlap_until = float("-inf")  # after a backward resync, beats up to the old clock overlap earlier beats
    pos = 0
    for anchor, n in msgs:
        jump = False
        if tb is None:
            tb = float(anchor)
        elif abs(tb - anchor) > RESYNC_S:
            if anchor < tb:
                overlap_until = max(overlap_until, tb)
            tb, jump = float(anchor), True
        for v in rr[pos:pos + int(n)]:
            tb += v
            jump = jump or tb <= overlap_until
            rec = records[max(0, bisect.bisect_right(rt, tb) - 1)]
            si = bisect.bisect_right(starts, tb) - 1
            out.append({"t_s": tb, "rr_s": v, "hr": rec["hr"], "speed": rec["speed"], "dist": rec["dist"],
                        "alt": rec["alt"], "cadence": rec["cadence"], "gap": jump or v > GAP_RR_S,
                        "stop": si >= 0 and tb < stops[si][1]})
            jump = False
        pos += int(n)
    out.sort(key=lambda b: b["t_s"])  # stable; keeps t_s monotonic for windows() after a backward resync
    return out


# --- cache and no_rr memory ---

def _no_rr_path() -> Path:
    return fit_dir() / "no_rr.json"


def no_rr(activity_id: int) -> str | None:
    return store.load_json(_no_rr_path(), {}, "no_rr.json").get(str(activity_id))


def mark_no_rr(activity_id: int, reason: str) -> None:
    if reason not in NO_RR_REASONS:
        raise ValueError(f"no_rr reason must be one of {NO_RR_REASONS}")
    path = _no_rr_path()
    with store.locked(path):
        data = store.load_json(path, {}, "no_rr.json")
        data[str(activity_id)] = reason
        store.save_json(path, data)


async def get_fit(activity_id: int, *, tracker: CacheTracker | None) -> dict[str, Any] | None:
    """Derived FIT data of one activity: the cached <id>.json (no call), else one ORIGINAL download.

    None, without a call, when the run is in no_rr.json and not cached. A file without `hrv` messages is cached
    (its records still serve temperature) and marked `no_hrv_messages`. A parse error raises `internal` and
    caches nothing (`FitUnreadable`); download errors raise as `PaceError.from_api`.
    """
    path = fit_dir() / f"{activity_id}.json"
    if path.exists():
        data = store.load_json(path, {}, f"FIT cache {path.name}")
        if tracker is not None:
            tracker.add(True, max(0, int(time.time() - path.stat().st_mtime)))
        return data
    if no_rr(activity_id) is not None:
        return None
    ok, raw, err = await api.safe_call("download_activity", str(activity_id), Garmin.ActivityDownloadFormat.ORIGINAL)
    if tracker is not None:
        tracker.add(False, 0)
    if not ok:
        assert err is not None
        raise PaceError.from_api(err)
    try:
        data = read_fit_bytes(raw)
    except FitFileError as exc:
        raise FitUnreadable(activity_id, exc) from None
    if not data["rr_s"]:
        mark_no_rr(activity_id, "no_hrv_messages")
    store.save_json(path, data)
    return data
