"""`zones estimate` orchestrator: run selection, call budget, deferred reads, H4 memory (methods doc 14.7).

A fake fetcher with an in-memory cache counts Garmin calls; the method functions are stubbed where only the
selection and the budget are under test (they have their own tests in test_thresholds.py).
"""

import asyncio
import copy
import sqlite3
from datetime import date, timedelta

import pytest

from pace import config, fit, hrv, thresholds, zones
from pace.cache import Cache
from pace.charts.sources import sleep_chunks
from pace.commands import zones as ze
from pace.envelope import PaceError

TODAY = date(2026, 11, 17)
EMPTY_FIT = {"records": [], "rr_s": [], "rr_msgs": [], "start": None}
REAL = {name: getattr(thresholds, name) for name in ("hr_speed", "drift_segment", "critical_speed")}
CS_OK = {"status": "ok", "cs_mps": 4.0, "d_prime_m": 180.0, "pace_s_per_km": 250, "windows": [{}, {}, {}],
         "points": 3, "confidence": "medium", "reasons": [], "runs": []}
CS_SHORT = {"status": "insufficient_data", "cs_mps": None, "d_prime_m": None, "windows": [{}],
            "reasons": ["no valid 420 s window", "no valid 720 s window"], "runs": []}
H2_OK = {"status": "ok", "a": 60.0, "b": 25.0, "see": 0.0, "v_min": 2.8, "v_max": 3.8, "n": 12, "n_runs": 3,
         "segments": [], "wrist_included": False, "reasons": [], "runs": []}
H2_NONE = {"status": "insufficient_data", "n": 0, "n_runs": 0, "segments": [], "reasons": ["0 steady segments"],
           "runs": []}
# methods doc section 6 "Observed on this athlete": rho = +0.49, 3 points above 0.75 -> not monotonic
OBSERVED = [(126, 0.54), (133, 0.65), (136, 0.45), (139, 0.35), (142, 0.37), (148, 0.35), (151, 0.46),
            (156, 0.61), (159, 0.63), (164, 1.03), (170, 1.13), (176, 0.99), (180, 0.35)]
NOT_MONOTONIC = [{"hr": h, "alpha1": a, "speed": 3.0, "cadence": None, "artifact_share": 0.01} for h, a in OBSERVED]


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")


def row(rid, day, *, kind="running", dur=2400, hr=140.0, speed=3.0, max_hr=150, gain=0.0, race=False):
    r = {"activityId": rid, "startTimeLocal": f"{day} 08:00:00", "startTimeGMT": f"{day} 07:00:00",
         "activityType": {"typeKey": kind}, "duration": dur, "elapsedDuration": dur, "averageHR": hr,
         "maxHR": max_hr, "averageSpeed": speed, "distance": speed * dur, "elevationGain": gain}
    if race:
        r["eventType"] = {"typeKey": "race"}
    return r


def d(month, day):
    return date(2026, month, day).isoformat()


class FakeFetcher:
    """In-memory cache keyed per read; a miss is one call; at the cap a miss raises Deferred."""

    def __init__(self, rows, *, wrist=(), peaks=None, fits=None, cache=None, errors=None):
        self.rows, self.wrist, self.peaks, self.fits = rows, set(wrist), peaks or {}, fits or {}
        self.errors = errors or {}   # key -> PaceError code raised by that call (the call is counted)
        self.cache = {} if cache is None else cache
        self.calls, self.max_calls, self.log, self.list_range = 0, None, [], None

    async def _read(self, key, make):
        if key in self.cache:
            return self.cache[key]
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise ze.Deferred(str(key))
        self.calls += 1
        self.log.append(key)
        if key in self.errors:
            if self.errors[key] == "fit_unreadable":
                raise fit.FitUnreadable(key[1], "injected parse failure")
            raise PaceError(self.errors[key], f"injected {self.errors[key]}")
        self.cache[key] = value = make()
        return value

    async def list_rows(self, start, end):
        self.list_range = (start, end)
        return await self._read(("list",), lambda: [r for r in self.rows
                                                    if start.isoformat() <= r["startTimeLocal"][:10] <= end.isoformat()])

    async def activity(self, rid):
        sensors = None if rid in self.wrist else [{"bleDeviceType": "HEART_RATE"}]
        return await self._read(("activity", rid), lambda: {"activityId": rid, "metadataDTO": {"sensors": sensors}})

    async def details(self, rid, *, maxchart):
        assert 0 < maxchart <= 20000
        return await self._read(("details", rid), lambda: {"id": rid, "peak": self.peaks.get(rid)})

    async def prs(self):
        return await self._read(("prs",), lambda: [])

    async def sleep(self, start, end):
        out = []
        for a, b in sleep_chunks(start, end):
            out += await self._read(("sleep", a, b), lambda b=b: [{"date": b.isoformat(), "rhr": 45}])
        return out

    async def fit(self, rid):
        return await self._read(("fit", rid), lambda: self.fits.get(rid, EMPTY_FIT))

    def fit_cached(self, rid):
        return ("fit", rid) in self.cache


def table_for(details):
    """Stub samples(): a marker row carrying the id, plus a 300..309 s plateau at `peak` HR when given."""
    rid, peak = details["id"], details.get("peak")
    out = [{"t": 0, "hr": None, "speed": None, "dist": None, "alt": None, "cadence": None, "id": rid}]
    if peak:
        out += [{"t": t, "hr": peak, "speed": 3.0, "dist": 3.0 * t, "alt": 0, "cadence": 170, "id": rid}
                for t in range(300, 310)]
    return out


SCORES: dict[int, float] = {}


@pytest.fixture
def stubs(monkeypatch):
    SCORES.clear()
    monkeypatch.setattr(thresholds, "samples", table_for)
    monkeypatch.setattr(thresholds, "critical_speed", lambda runs, *, prs: copy.deepcopy(CS_OK if runs else CS_SHORT))
    monkeypatch.setattr(thresholds, "hr_speed",
                        lambda runs, *, include_wrist=False: copy.deepcopy(H2_OK if runs else H2_NONE))
    monkeypatch.setattr(thresholds, "drift_segment",
                        lambda run, *, fit_records, include_wrist=False: {"id": run["id"], "rejected": "stub"})
    monkeypatch.setattr(thresholds, "fit_span_score", lambda table, ref: SCORES.get(table[0]["id"], 0.0))
    return monkeypatch


def worked_rows():
    """Methods doc 14.7 worked example: 40 rows in 8 weeks (29 candidates), 60 in 12 weeks (14 drift candidates)."""
    rows = [row(101, d(10, 4), race=True, max_hr=175, speed=3.6, hr=160),
            row(102, d(10, 18), race=True, max_hr=175, speed=3.6, hr=160)]
    rows += [row(103 + i, d(10, 6 + 2 * i), max_hr=185 - i, speed=3.5, hr=150) for i in range(4)]
    for i in range(22):  # steady pool: speeds 2.50..3.55; ranks 11 (211) and 16 (216) are 70-min runs near 142
        day = (date(2026, 9, 24) + timedelta(days=2 * i)).isoformat()
        long_hr = {10: 139.0, 15: 143.0}.get(i)
        rows.append(row(201 + i, day, speed=round(2.50 + 0.05 * i, 2), dur=4200 if long_hr else 2400,
                        hr=long_hr or 130.0))
    rows.append(row(301, d(11, 10), dur=6000, hr=138.0))
    rows += [row(401 + i, d(11, 1 + i), kind="treadmill_running") for i in range(3)]
    rows += [row(404 + i, d(11, 4 + i), kind="trail_running") for i in range(2)]
    rows += [row(406 + i, d(11, 6 + i), dur=900) for i in range(5)]
    rows.append(row(411, d(11, 11), hr=None))
    # weeks 9-12: three more drift picks before 2026-09-01, eight farther drift candidates, nine short runs
    rows += [row(302, d(8, 26), dur=4200, hr=145.0), row(303, d(8, 27), dur=4200, hr=136.0),
             row(304, d(8, 28), dur=4200, hr=147.0)]
    rows += [row(305 + i, d(8, 29) if i < 3 else d(9, i), dur=4200, hr=float(h))
             for i, h in enumerate((120, 122, 124, 160, 162, 164, 166, 168))]
    rows += [row(501 + i, d(9, 9 + i), dur=1500) for i in range(9)]
    assert len(rows) == 60
    return rows


def worked_fetcher(**kw):
    SCORES.update({101: 10.0, 103: 10.0, 203: 10.0, 207: 10.0, 220: 10.0})
    f = FakeFetcher(worked_rows(), **kw)
    f.cache.setdefault(("fit", 220), EMPTY_FIT)  # one of the five scoring runs is cached
    return f


def run(f, data=None, **kw):
    data = data if data is not None else zones.load()
    return asyncio.run(ze.run_estimate(f, data, today=TODAY, **kw)), data


# --- estimated_seconds ---

@pytest.mark.parametrize(("calls", "secs"), [(0, 0), (1, 0), (40, 198), (101, 3600)])
def test_estimated_seconds(calls, secs):
    assert ze.estimated_seconds(calls, min_interval=2.0, per_minute=10, per_hour=100) == secs


# --- 14.7 worked example ---

def test_worked_example_40_calls(stubs):
    f = worked_fetcher()
    res, data = run(f)
    assert res["calls"] == f.calls == 40 and res["estimated_seconds"] == 198
    assert res["deferred"] == []
    assert f.log[0] == ("list",) and f.log[1] == ("prs",) and [k[0] for k in f.log[2:4]] == ["sleep", "sleep"]
    history = {101, 102, 103, 104, 105, 106, 203, 207, 211, 216, 220, 222}
    detailed = {k[1] for k in f.log if k[0] == "details"}
    assert detailed == history | {301, 302, 303, 304}
    assert {k[1] for k in f.log if k[0] == "fit"} == {101, 103, 203, 207}
    assert res["lt1"]["hr"] == 142 and res["lt1"]["method"] == "H3" and res["lt2"]["hr"] == 160
    assert res["ref_hr"] == 142
    assert data["estimates"][-1]["partial"] is False
    assert res["rest_hr"] == 45


def test_worked_example_capped_then_rerun(stubs):
    full, _ = run(worked_fetcher())
    f = worked_fetcher()
    res, data = run(f, max_calls=30)
    assert res["calls"] == f.calls == 30
    runs = sorted(x["id"] for x in res["deferred"] if x["kind"] == "run")
    fits = sorted(x["id"] for x in res["deferred"] if x["kind"] == "fit")
    assert runs == [302, 303, 304] and fits == [101, 103, 203, 207]
    assert res["summary"]["partial"] is True and data["estimates"][-1]["partial"] is True
    again = FakeFetcher(worked_rows(), cache=f.cache)
    res2, data = run(again, data, max_calls=30)
    assert res2["calls"] == 10 and res2["deferred"] == []
    assert sorted({k[1] for k in again.log}) == [101, 103, 203, 207, 302, 303, 304]
    assert res2["summary"]["partial"] is False and len(data["estimates"]) == 2
    assert res2["summary"] == full["summary"] and res2["lt1"] == full["lt1"] and res2["lt2"] == full["lt2"]


def test_zero_cap_defers_everything(stubs):
    f = worked_fetcher()
    res, data = run(f, max_calls=0)
    assert f.calls == 0 and res["calls"] == 0 and res["estimated_seconds"] == 0
    assert [x["kind"] for x in res["deferred"]][:3] == ["list", "prs", "sleep"]
    assert data["estimates"][-1]["partial"] is True and res["record"] is None


# --- weeks and extension ---

def ext_rows():
    return [row(1, d(11, 10), max_hr=180), row(2, d(11, 3), max_hr=179),       # weeks 1-4
            row(3, d(10, 5), max_hr=178), row(4, d(9, 30), max_hr=177),        # weeks 5-8 (extension)
            row(5, d(9, 5), max_hr=176)]                                       # week 11: never history


def test_weeks_4_reads_12_weeks_and_extends_to_8(stubs):
    stubs.setattr(thresholds, "critical_speed", lambda runs, *, prs: copy.deepcopy(CS_SHORT))
    f = FakeFetcher(ext_rows())
    res, _ = run(f, weeks=4)
    assert f.list_range == (TODAY - timedelta(weeks=12), TODAY)
    assert res["extended"] is True
    assert {k[1] for k in f.log if k[0] == "details"} == {1, 2, 3, 4}
    assert res["methods"]["H1"]["status"] == "insufficient_data"


def test_extension_stays_within_the_cap(stubs):
    stubs.setattr(thresholds, "critical_speed", lambda runs, *, prs: copy.deepcopy(CS_SHORT))
    f = FakeFetcher(ext_rows())
    res, data = run(f, weeks=4, max_calls=10)  # list, prs, 2 sleep, 2 runs, the first extension run
    assert f.calls == res["calls"] == 10
    assert [(x["kind"], x["id"]) for x in res["deferred"]] == [("run", 4)]
    assert data["estimates"][-1]["partial"] is True


def test_list_read_covers_twice_the_weeks_when_longer(stubs):
    f = FakeFetcher(ext_rows())
    run(f, weeks=10)
    assert f.list_range == (TODAY - timedelta(weeks=20), TODAY)


# --- wrist runs ---

def test_wrist_steady_and_drift_picks_are_replaced(stubs):
    f = worked_fetcher(wrist={207, 301})
    res, _ = run(f)
    keys = set(f.log)
    assert ("activity", 207) in keys and ("details", 207) not in keys
    assert ("details", 208) in keys          # nearest untaken to the 2.80 m/s target (tie: newest)
    assert ("activity", 301) in keys and ("details", 301) not in keys
    assert ("details", 307) in keys          # next drift candidate below the ref (124 bpm)
    skipped = {x["id"]: x["reason"] for x in res["runs_skipped"]}
    assert "wrist" in skipped[207] and "wrist" in skipped[301]


def test_wrist_effort_run_is_fetched_in_detail(stubs):
    f = worked_fetcher(wrist={103})
    run(f)
    assert ("details", 103) in set(f.log)


def test_all_wrist_candidates_give_no_hr_methods(stubs):
    stubs.setattr(thresholds, "hr_speed", REAL["hr_speed"])
    stubs.setattr(thresholds, "drift_segment", REAL["drift_segment"])
    rows = [row(1, d(11, 10), max_hr=180), row(2, d(11, 3)), row(3, d(10, 27)),
            row(4, d(10, 20), dur=4200, hr=139.0), row(5, d(10, 13), dur=4200, hr=146.0)]
    f = FakeFetcher(rows, wrist={1, 2, 3, 4, 5})
    SCORES.update({1: 10.0})
    res, _ = run(f)
    for name in ("H2", "H4", "H5"):
        m = res["methods"][name]
        assert m["status"] == "insufficient_data", name
        assert any("wrist" in r for r in m["reasons"]), (name, m["reasons"])
    assert not [k for k in f.log if k[0] == "fit"]


# --- FIT, RR and the H4 memory ---

def h4_rows():
    return [row(11, d(11, 10)), row(12, d(11, 12))]


def test_rr_rejected_is_marked(stubs):
    seen = []

    def boom(data):
        seen.append(1)
        raise fit.RRRejected("RR sum differs")

    stubs.setattr(fit, "aligned_rr", boom)
    f = FakeFetcher(h4_rows())
    f.cache[("fit", 11)] = {**EMPTY_FIT, "rr_s": [0.4]}
    res, data = run(f)
    assert fit.no_rr(11) == "rr_rejected"
    assert any(x["id"] == 11 and x["reason"] == "rr_rejected" for x in res["methods"]["H4"]["rejected"])
    run(FakeFetcher(h4_rows(), cache=f.cache), data)
    assert len(seen) == 1  # a rejected run is never aligned again


def _inject_windows(monkeypatch, by_id):
    monkeypatch.setattr(fit, "aligned_rr", lambda data: [{"id": data["id"]}])
    monkeypatch.setattr(hrv, "windows", lambda aligned: {"windows": by_id[aligned[0]["id"]], "dropped_coupling": 0})


def test_cached_run_does_not_raise_the_streak_twice(stubs):
    _inject_windows(stubs, {11: NOT_MONOTONIC})
    f = FakeFetcher(h4_rows())
    f.cache[("fit", 11)] = {**EMPTY_FIT, "id": 11}
    res, data = run(f)
    assert data["h4"]["streak"] == 1 and data["h4"]["counted"] == [11]
    assert res["methods"]["H4"]["status"] == "alpha1_unusable"
    run(FakeFetcher(h4_rows(), cache=f.cache), data)
    assert data["h4"]["streak"] == 1 and not data["h4"]["unreliable"]


def test_two_not_monotonic_runs_mark_h4_unreliable_then_skip(stubs):
    _inject_windows(stubs, {11: NOT_MONOTONIC, 12: NOT_MONOTONIC})
    f = FakeFetcher(h4_rows())
    f.cache[("fit", 11)] = {**EMPTY_FIT, "id": 11}
    f.cache[("fit", 12)] = {**EMPTY_FIT, "id": 12}
    _, data = run(f)
    assert data["h4"]["unreliable"] is True and data["h4"]["counted"] == [11, 12]
    # next estimate: H4 skipped, no FIT download even for a scoring run that is not cached
    rows = h4_rows() + [row(13, d(11, 14))]
    SCORES.update({13: 10.0})
    g = FakeFetcher(rows)
    res, data = run(g, data)
    assert res["methods"]["H4"]["status"] == "skipped" and res["methods"]["H4"]["reasons"]
    assert not [k for k in g.log if k[0] == "fit"]
    # --retry-h4 clears the mark and downloads again
    _inject_windows(stubs, {13: []})
    h = FakeFetcher(rows, fits={13: {**EMPTY_FIT, "id": 13}})
    res, data = run(h, data, retry_h4=True)
    assert ("fit", 13) in h.log and data["h4"]["unreliable"] is False
    assert res["methods"]["H4"]["status"] != "skipped"


# --- HRmax, log, record ---

def test_hr_max_updated_from_strap_runs_only(stubs):
    f = worked_fetcher(wrist={104}, peaks={103: 188, 104: 199})
    res, data = run(f)
    assert data["hr_max"] == {"bpm": 188, "activity": 103, "date": d(10, 6)}
    assert res["hr_max"]["bpm"] == 188


def test_summary_appended_and_record_storable(stubs):
    f = worked_fetcher()
    res, data = run(f)
    s = data["estimates"][-1]
    assert s == res["summary"]
    assert s["date"] == TODAY.isoformat() and s["lt1_hr"] == 142 and s["lt2_hr"] == 160 and s["lt2_pace"] == 250
    assert set(s) == {"date", "lt1_hr", "lt2_hr", "lt2_pace", "methods", "confidence", "partial"}
    rec = res["record"]
    assert rec["source"] == "estimate" and rec["details"]["h2"] == {"a": 60.0, "b": 25.0, "v_min": 2.8, "v_max": 3.8}
    stored = zones.add_record(data, rec, today=TODAY)
    assert stored["lt1"] == {"hr": 142, "pace_s_per_km": 304} and stored["lt2"]["hr"] == 160
    assert stored["rest_hr"] == 45 and stored["cs_mps"] == 4.0
    run(FakeFetcher(worked_rows(), cache=f.cache), data)
    assert len(data["estimates"]) == 2


def test_current_lt1_record_is_the_drift_ref(stubs):
    data = zones.load()
    zones.accept(data, zones.add_record(data, {"date": d(10, 1), "source": "lab", "lt1": {"hr": 150}},
                                        today=TODAY)["id"])
    res, _ = run(worked_fetcher(), data)
    assert res["ref_hr"] == 150


# --- the real fetcher ---

def test_garmin_fetcher_cap_cache_and_keys(monkeypatch):
    made = []

    async def safe_call(method, *args, **kwargs):
        made.append((method, args))
        if method == "get_activities_by_date":
            return True, [row(7, d(10, 1)), row(8, TODAY.isoformat()), {"activityId": None}], None
        return True, {"activityId": args[0]}, None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)

    async def go():
        cache = Cache()
        try:
            g = ze.GarminFetcher(cache, today=TODAY)
            g.max_calls = 3
            rows = await g.list_rows(TODAY - timedelta(weeks=16), TODAY)
            await g.activity(7)
            await g.details(8, maxchart=100)
            with pytest.raises(ze.Deferred):
                await g.activity(8)
            await g.activity(7)  # cached: served at the cap
            return rows, g.calls
        finally:
            cache.close()

    rows, calls = asyncio.run(go())
    assert [r["activityId"] for r in rows] == [7, 8] and calls == 3 and len(made) == 3
    conn = sqlite3.connect(str(config.CACHE_DB))
    ttl = dict(conn.execute("SELECT key, ttl FROM cache").fetchall())
    conn.close()
    assert ttl["zones:get_activity:7"] == 60 * 86400
    assert ttl["zones:get_activity_details:8:maxchart=100"] < 60 * 86400


def test_garmin_fetcher_fit(monkeypatch):
    got = []

    async def get_fit(activity_id, *, tracker):
        got.append(activity_id)
        return EMPTY_FIT

    monkeypatch.setattr(fit, "get_fit", get_fit)

    async def go():
        cache = Cache()
        try:
            g = ze.GarminFetcher(cache, today=TODAY)
            g.max_calls = 1
            (fit.fit_dir() / "21.json").write_text("{}")
            assert g.fit_cached(21) and not g.fit_cached(22)
            await g.fit(21)            # cached: no call counted
            await g.fit(22)            # one download
            with pytest.raises(ze.Deferred):
                await g.fit(23)
            fit.mark_no_rr(24, "no_hrv_messages")
            assert await g.fit(24) is None
            return g.calls
        finally:
            cache.close()

    assert asyncio.run(go()) == 1 and got == [21, 22]


# --- fix round 1: Garmin errors, skip lists, wrist reasons, suggestions, sleep chunks ---

def test_rate_limited_mid_estimate_is_partial_and_rerun_completes(stubs):
    full, _ = run(worked_fetcher())
    f = worked_fetcher(errors={("details", 207): "rate_limited"})
    res, data = run(f)
    assert f.log[-1] == ("details", 207) and res["calls"] == f.calls == 4 + 2 * 6 + 2 * 2
    assert {"kind": "run", "id": 207, "group": "steady", "error": "rate_limited"} in res["deferred"]
    assert {x["id"] for x in res["deferred"] if x["kind"] == "run"} >= {211, 216, 220, 222, 301, 302, 303, 304}
    assert res["summary"]["partial"] is True and data["estimates"][-1]["partial"] is True
    again = FakeFetcher(worked_rows(), cache=f.cache)
    res2, _ = run(again, data)
    assert res2["deferred"] == [] and res2["calls"] == 40 - 4 - 2 * 6 - 2 - 1
    assert res2["summary"] == full["summary"]


def test_auth_error_propagates(stubs):
    f = worked_fetcher(errors={("activity", 101): "auth_required"})
    with pytest.raises(PaceError) as exc:
        run(f)
    assert exc.value.code == "auth_required"


def test_unreadable_fit_is_skipped_and_marked(stubs):
    f = worked_fetcher(errors={("fit", 101): "fit_unreadable"})
    res, data = run(f)
    assert fit.no_rr(101) == "fit_unreadable"
    assert any(x["id"] == 101 and x["reason"] == "fit_unreadable" for x in res["methods"]["H4"]["rejected"])
    assert {k[1] for k in f.log if k[0] == "fit"} == {101, 103, 203, 207} and res["deferred"] == []
    g = FakeFetcher(worked_rows(), cache=f.cache)
    run(g, data)
    assert ("fit", 101) not in g.log


def test_internal_download_error_does_not_mark_the_run(stubs):
    """A transient `internal` error on a FIT download is not a parse failure: nothing is marked, the read is
    deferred with its error, and a rerun tries the download again."""
    f = worked_fetcher(errors={("fit", 101): "internal"})
    res, data = run(f)
    assert fit.no_rr(101) is None
    assert res["summary"]["partial"] is True
    assert {"kind": "fit", "id": 101, "error": "internal"} in res["deferred"]
    assert not any(x["id"] == 101 for x in res["methods"]["H4"]["rejected"])
    g = worked_fetcher(cache=f.cache)
    run(g, data)
    assert ("fit", 101) in g.log


def test_not_found_run_is_skipped(stubs):
    f = worked_fetcher(errors={("activity", 203): "not_found"})
    res, _ = run(f)
    assert any(x["id"] == 203 and x["group"] == "steady" and "not found" in x["reason"] for x in res["runs_skipped"])
    assert 203 not in {r["id"] for r in res["runs_used"]} and res["deferred"] == []


def test_runs_skipped_never_lists_used_runs(stubs):
    res, _ = run(worked_fetcher())
    skipped = {x["id"] for x in res["runs_skipped"]}
    used = {r["id"] for r in res["runs_used"]}
    assert not skipped & {101, 102, 103, 104, 105, 106, 203, 207, 220, 222}
    assert not skipped & used
    assert {x["group"] for x in res["runs_skipped"]} <= {"history", "drift"}
    assert any(x["id"] == 401 and x["group"] == "history" for x in res["runs_skipped"])


def test_wrist_effort_run_without_drift_candidates_gives_no_h5_wrist_reason(stubs):
    f = FakeFetcher([row(1, d(11, 10)), row(2, d(11, 3))], wrist={1})
    res, _ = run(f)
    assert res["methods"]["H5"]["status"] == "insufficient_data"
    assert ze.ALL_WRIST not in res["methods"]["H5"]["reasons"]


def test_strap_runs_before_rr_data_give_no_h4_wrist_reason(stubs):
    f = FakeFetcher([row(1, d(8, 28)), row(2, d(8, 29))], wrist={2})
    res, _ = run(f, weeks=12)
    assert {r["id"] for r in res["runs_used"]} == {1, 2}
    assert ze.ALL_WRIST not in res["methods"]["H4"]["reasons"]


def test_failed_extension_suggests_field_tests(stubs):
    stubs.setattr(thresholds, "critical_speed", lambda runs, *, prs: copy.deepcopy(CS_SHORT))
    stubs.setattr(thresholds, "hr_speed", lambda runs, *, include_wrist=False: copy.deepcopy(H2_NONE))
    res, _ = run(FakeFetcher(ext_rows()), weeks=4)
    m = res["methods"]
    assert res["extended"] is True
    assert m["H1"]["suggest"] == {"test": "F2"} and res["lt2"]["suggest"] == {"test": "F2"}
    assert m["H2"]["suggest"] == {"test": "F1"} and m["H5"]["suggest"] == {"test": "F1"}


def test_no_suggestion_without_extension(stubs):
    res, _ = run(worked_fetcher())
    assert res["extended"] is False
    assert all("suggest" not in res["methods"][k] for k in ("H1", "H2", "H5")) and "suggest" not in res["lt2"]


def test_served_sleep_chunk_kept_when_the_second_is_deferred(stubs):
    f = worked_fetcher()
    res, data = run(f, max_calls=3)  # list, prs, the first sleep chunk
    assert res["rest_hr"] == 45
    assert {"kind": "sleep", "id": "2026-11-08"} in res["deferred"]
    assert data["estimates"][-1]["partial"] is True
