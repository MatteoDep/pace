"""`pace zones` commands: show, estimate, analyze, test-workout, add, accept, remove (Garmin faked, never real)."""

import copy
import json
from datetime import date, datetime, timedelta

import pytest
from typer.testing import CliRunner

from pace import fit, thresholds, zones
from pace.api import ApiError
from pace.cli import app
from pace.commands import zones as ze

runner = CliRunner()
TODAY = date.today()
ZONES_PATH = "/biometric-service/heartRateZones"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")
    return tmp_path


def day(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


# --- a fake Garmin ---

STRAP = {"metadataDTO": {"sensors": [{"bleDeviceType": "HEART_RATE"}]}}


def details_for(n, hr, speed, *, start=1_700_000_000_000):
    """get_activity_details payload: one row per second; hr(t) and speed(t) are functions of the elapsed second."""
    keys = ["directTimestamp", "directHeartRate", "directSpeed", "sumDistance", "directElevation", "directRunCadence"]
    rows, dist = [], 0.0
    for t in range(n):
        v = speed(t)
        dist += v
        rows.append({"metrics": [start + t * 1000, hr(t), v, dist, 100.0, 85.0]})
    return {"metricDescriptors": [{"key": k, "metricsIndex": i} for i, k in enumerate(keys)],
            "activityDetailMetrics": rows}


def laps_for(*lengths):
    out, t0 = [], 0
    for n in lengths:
        start = datetime(2026, 11, 10, 7, 0, 0) + timedelta(seconds=t0)
        out.append({"startTimeGMT": start.isoformat(), "duration": float(n),
                    "elapsedDuration": float(n), "distance": 3.2 * n})
        t0 += n
    return {"lapDTOs": out}


@pytest.fixture
def garmin(monkeypatch):
    state = {"calls": [], "rows": [], "zones": [], "activity": {**STRAP, "summaryDTO": {
        "startTimeLocal": f"{day(3)}T08:00:00.0", "elapsedDuration": 4800.0}},
        "details": None, "splits": None, "errors": {}}

    def payload(method, args, kwargs):
        if method in state["errors"]:
            return state["errors"][method]
        if method == "get_activities_by_date":
            a, b = args[:2]
            return [r for r in state["rows"] if a <= r["startTimeLocal"][:10] <= b]
        if method == "connectapi" and args[0] == ZONES_PATH:
            return state["zones"]
        if method == "get_activity":
            return state["activity"]
        if method == "get_activity_details":
            return state["details"]
        if method == "get_activity_splits":
            return state["splits"]
        return []

    async def safe_call(method, *args, **kwargs):
        state["calls"].append((method, args, kwargs))
        p = payload(method, args, kwargs)
        if isinstance(p, ApiError):
            return False, None, p
        return True, p, None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    monkeypatch.setattr("pace.api.safe_call", safe_call)
    return state


def methods(g):
    return [c[0] for c in g["calls"]]


def invoke(*args):
    res = runner.invoke(app, ["zones", *args])
    assert res.stdout.count("\n") == 1, res.stdout
    return res.exit_code, json.loads(res.stdout)


def run(*args):
    code, doc = invoke(*args)
    assert code == 0 and doc["ok"], doc
    return doc["data"]


def fails(*args, code="bad_args"):
    status, doc = invoke(*args)
    assert not doc["ok"] and doc["error"]["code"] == code, doc
    return doc["error"]


def usage(*args):
    """A click usage error: exit 2 (the real entry point turns it into a bad_args envelope)."""
    res = runner.invoke(app, ["zones", *args])
    assert res.exit_code == 2, res.output
    return res.output


def stored():
    return zones.load()


def lab(*extra, on=None):
    return run("add", "--source", "lab", "--date", on or day(10), "--lt1-hr", "140", "--lt1-pace", "5:30",
               "--lt2-hr", "165", "--lt2-pace", "4:10", *extra)


# --- add / accept / remove ---

def test_add_lab_is_current_per_threshold_and_older_lab_is_not() -> None:
    out = lab("--max-hr", "190", "--method", "VT1/VT2", "--confidence", "high", "--activity", "5", "--notes", "lab")
    rec = out["record"]
    assert rec["id"] == 1 and rec["source"] == "lab" and rec["lt1"] == {"hr": 140, "pace_s_per_km": 330}
    assert rec["lt2"] == {"hr": 165, "pace_s_per_km": 250} and rec["activities"] == [5] and rec["max_hr"] == 190
    assert out["current"]["lt1"]["id"] == 1 and out["current"]["lt2"]["source"] == "lab"
    assert stored()["hr_max"]["bpm"] == 190
    older = run("add", "--source", "lab", "--date", day(100), "--lt2-hr", "160")
    assert older["record"]["id"] == 2 and older["current"]["lt2"]["id"] == 1
    newer = run("add", "--source", "lab", "--date", day(1), "--lt2-hr", "167")
    assert newer["current"]["lt2"]["id"] == 3 and newer["current"]["lt1"]["id"] == 1


def test_add_validation() -> None:
    fails("add", "--source", "lab", "--lt1-hr", "170", "--lt2-hr", "160")
    fails("add", "--source", "bogus", "--lt1-hr", "140")
    fails("add", "--source", "lab")
    fails("add", "--source", "lab", "--lt1-pace", "fast")
    fails("add", "--source", "lab", "--lt1-hr", "140", "--date", "2999-01-01")
    assert "--source" in usage("add", "--lt1-hr", "140")
    assert not (zones.zones_path()).exists()


def test_accept_and_only() -> None:
    lab(on=day(60))
    est = ze_record(date_=day(2), lt1=(142, 320), lt2=(168, 245))
    out = run("accept", str(est), "--only", "lt2")
    assert out["current"]["lt2"]["id"] == est and out["current"]["lt1"]["id"] == 1
    out = run("accept", str(est))
    assert out["current"]["lt1"]["id"] == est
    fails("accept", "99", code="not_found")
    f1 = ze_record(date_=day(1), lt1=(120, 360), lt2=None)
    fails("accept", str(f1), "--only", "lt2")
    high = ze_record(date_=day(1), lt1=(175, 300), lt2=None)       # LT1 HR above the current LT2 HR
    err = fails("accept", str(high))
    assert "LT1" in err["message"]
    assert stored()["current"]["lt1"] == est


def ze_record(*, date_, lt1, lt2, source="estimate"):
    with zones.transaction() as data:
        rec = {"date": date_, "source": source, "method": "t"}
        if lt1:
            rec["lt1"] = {"hr": lt1[0], "pace_s_per_km": lt1[1]}
        if lt2:
            rec["lt2"] = {"hr": lt2[0], "pace_s_per_km": lt2[1]}
        return zones.add_record(data, rec, today=TODAY)["id"]


def test_remove_refuses_current_unless_force() -> None:
    lab()
    err = fails("remove", "1")
    assert "current" in err["message"] and "Garmin" not in (err["hint"] or "")
    out = run("remove", "1", "--force")
    assert out["removed"]["id"] == 1 and out["current"] == {"lt1": None, "lt2": None}
    err = fails("remove", "1", code="not_found")
    assert "Garmin" not in (err["hint"] or "") and "zones show" in err["hint"]
    other = ze_record(date_=day(1), lt1=(140, 330), lt2=None)
    assert run("remove", str(other))["removed"]["id"] == other


# --- show ---

FLOORS = {"sport": "RUNNING", "zone1Floor": 112, "zone2Floor": 126, "zone3Floor": 140, "zone4Floor": 165,
          "zone5Floor": 178, "maxHeartRateUsed": 190}


def test_show_empty(garmin) -> None:
    garmin["zones"] = [FLOORS]
    out = run("show")
    assert set(out) == {"current", "expired", "zones", "records", "candidates", "due", "hr_max", "h4", "garmin",
                        "in_sync"}
    assert out["current"] == {"lt1": None, "lt2": None} and out["records"] == [] and out["in_sync"] is None
    assert out["garmin"]["zone4Floor"] == 165 and out["due"]["estimate"] is True
    assert sorted(methods(garmin)) == ["connectapi", "get_activities_by_date"]
    assert not zones.zones_path().exists()


def test_show_combines_local_and_garmin(garmin) -> None:
    garmin["zones"] = [FLOORS]
    lab("--max-hr", "190")
    out = run("show")
    assert out["current"]["lt1"] == {"id": 1, "source": "lab", "date": day(10), "method": None, "confidence": None}
    assert [r["current_for"] for r in out["records"]] == [["lt1", "lt2"]]
    assert out["zones"]["floors"]["zone4Floor"] == 165 and out["in_sync"] is True
    assert out["zones"]["floor_errors"] == []
    assert out["expired"] == {"lt1": False, "lt2": False} and out["hr_max"]["bpm"] == 190
    est = ze_record(date_=day(1), lt1=(142, 320), lt2=None)
    out = run("show")
    assert [c["id"] for c in out["candidates"]] == [est] and out["candidates"][0]["candidate_for"] == ["lt1"]
    garmin["zones"] = [{**FLOORS, "zone2Floor": 100}]
    assert run("show", "--no-cache")["in_sync"] is False


def test_show_reports_floor_errors(garmin) -> None:
    # methods doc 14.5: zone5Floor must stay below HRmax; show reports it instead of hiding the floors
    lab()
    with zones.transaction() as data:
        data["hr_max"] = {"bpm": 166, "activity": None, "date": day(1)}
    out = run("show")
    assert out["zones"]["floors"]["zone5Floor"] == 166
    assert out["zones"]["floor_errors"] == ["zone5Floor 166 is not below max HR 166"]


def test_add_pace_is_whole_seconds_per_km() -> None:
    rec = run("add", "--source", "lab", "--date", day(10), "--lt2-pace", "4:10")["record"]
    assert rec["lt2"]["pace_s_per_km"] == 250 and isinstance(rec["lt2"]["pace_s_per_km"], int)
    assert ze._pace_s("4:10", "--lt2-pace") == 250 and isinstance(ze._pace_s("4:10", "--lt2-pace"), int)


def test_show_without_watch_zones_still_answers(garmin) -> None:
    garmin["errors"]["connectapi"] = ApiError("endpoint_unavailable", "no", 403)
    out = run("show")
    assert out["garmin"] is None and out["in_sync"] is None


def test_show_auth_error_propagates(garmin) -> None:
    garmin["errors"]["connectapi"] = ApiError("auth_required", "no tokens", 401)
    fails("show", code="auth_required")


def test_show_due_sees_a_break_straddling_the_estimate(garmin) -> None:
    """Runs: 50 d ago (before the estimate), then 16 d ago and 5 d ago; estimate 20 d ago."""
    with zones.transaction() as data:
        zones.log_estimate(data, {"date": day(20), "lt1_hr": None, "lt2_hr": None, "lt2_pace": None,
                                  "methods": {}, "confidence": {}, "partial": False})
    garmin["rows"] = [{"activityId": i, "startTimeLocal": f"{day(n)} 08:00:00", "activityType": {"typeKey": "running"}}
                      for i, n in ((1, 50), (2, 16), (3, 5))]
    garmin["rows"].append({"activityId": 4, "startTimeLocal": f"{day(18)} 08:00:00",
                           "activityType": {"typeKey": "cycling"}})
    out = run("show")
    assert out["due"]["estimate"] is True
    assert any("break of 34 days" in r for r in out["due"]["reasons"]), out["due"]
    listing = next(c for c in garmin["calls"] if c[0] == "get_activities_by_date")
    assert listing[1][0] == day(20 + 56)


# --- test-workout ---

def test_test_workout_is_pure(garmin) -> None:
    out = run("test-workout", "time_trial")
    assert out["workout"]["name"].startswith("LT2 30-min") and garmin["calls"] == []
    assert not zones.zones_path().exists()
    out = run("test-workout", "drift")
    assert out["workout"]["steps"]
    fails("test-workout", "vo2")


def test_test_workout_uses_current_record_and_estimates() -> None:
    rec = run("add", "--source", "lab", "--date", day(5), "--lt1-hr", "140", "--lt1-pace", "5:30")["record"]
    ramp = run("test-workout", "ramp")["workout"]
    text = json.dumps(ramp)
    assert "pace" in text or "hr" in text.lower()
    assert rec["id"] == 1
    base = thresholds.test_workout("ramp", lt1_record=zones.current(stored())["lt1"], estimate_lt1_hr=None, h2=None)
    assert ramp == base
    with zones.transaction() as data:
        data["current"]["lt1"] = None
        zones.log_estimate(data, {"date": day(1), "lt1_hr": 133, "lt2_hr": None, "partial": False})
    est_ramp = run("test-workout", "ramp")["workout"]
    assert est_ramp == thresholds.test_workout("ramp", lt1_record=None, estimate_lt1_hr=133, h2=None)


# --- estimate ---

SUMMARY = {"date": TODAY.isoformat(), "lt1_hr": 141, "lt2_hr": 166, "lt2_pace": 250,
           "methods": {"lt1": "H3", "lt2": "H1"}, "confidence": {"lt1": "low", "lt2": "medium"}, "partial": False}
CANDIDATE = {"date": TODAY.isoformat(), "source": "estimate", "method": "lt1 H3, lt2 H1",
             "lt1": {"hr": 141, "pace_s_per_km": 320}, "lt2": {"hr": 166, "pace_s_per_km": 250},
             "max_hr": 188, "rest_hr": 45, "cs_mps": 4.0, "d_prime_m": 180.0, "activities": [1, 2],
             "confidence": "low", "notes": None, "details": {"h2": None}}


@pytest.fixture
def stub_estimate(monkeypatch):
    seen = {}

    async def stub(fetcher, data, *, today, weeks, max_calls, include_wrist, retry_h4):
        seen.update(weeks=weeks, max_calls=max_calls, include_wrist=include_wrist, retry_h4=retry_h4,
                    max_calls_on_fetcher=fetcher.max_calls, today=today)
        zones.log_estimate(data, SUMMARY)
        zones.update_hr_max(data, 188, activity=1, day=today)
        zones.record_alpha1_outcome(data, "not_monotonic", day=today, activity=1)
        if seen.get("concurrent"):   # another pace process writes a record while the estimate runs
            seen["concurrent"]()
        return {"lt1": {}, "lt2": {}, "runs_used": [{"id": 1, "date": today.isoformat()}],
                "methods": {"H4": {"runs": [{"id": 1, "outcome": "not_monotonic"}]}}, "runs_skipped": [], "deferred": [],
                "calls": 3, "estimated_seconds": 4, "hr_max": data["hr_max"], "rest_hr": 45, "h4": dict(data["h4"]),
                "weeks": weeks, "extended": False, "ref_hr": None, "summary": SUMMARY,
                "record": copy.deepcopy(seen.get("record", CANDIDATE))}

    monkeypatch.setattr(ze, "run_estimate", stub)
    return seen


def test_estimate_logs_but_stores_no_record_without_save(garmin, stub_estimate) -> None:
    out = run("estimate", "--weeks", "6", "--max-calls", "12", "--include-wrist", "--retry-h4")
    assert stub_estimate["weeks"] == 6 and stub_estimate["max_calls"] == 12
    assert stub_estimate["include_wrist"] and stub_estimate["retry_h4"] and stub_estimate["today"] == TODAY
    assert out["saved"] is None and out["record"]["source"] == "estimate" and out["calls"] == 3
    d = stored()
    assert d["records"] == [] and d["estimates"] == [SUMMARY]
    assert d["hr_max"]["bpm"] == 188 and d["h4"]["streak"] == 1


def test_estimate_defaults(garmin, stub_estimate) -> None:
    run("estimate")
    assert stub_estimate["weeks"] == 8 and stub_estimate["max_calls"] == 40
    assert not stub_estimate["include_wrist"] and not stub_estimate["retry_h4"]


def test_estimate_save_stores_a_full_record_and_keeps_concurrent_writes(garmin, stub_estimate) -> None:
    def concurrent():
        with zones.transaction() as data:
            zones.add_record(data, {"date": day(10), "source": "lab", "lt1": {"hr": 140, "pace_s_per_km": 330},
                                    "lt2": {"hr": 165, "pace_s_per_km": 250}, "max_hr": 190}, today=TODAY)

    stub_estimate["concurrent"] = concurrent
    out = run("estimate", "--save")
    d = stored()
    assert out["saved"]["id"] == 2 and out["record"]["id"] == 2
    assert [r["source"] for r in d["records"]] == ["lab", "estimate"]
    assert d["current"] == {"lt1": 1, "lt2": 1}        # the lab record entered meanwhile is untouched
    assert d["estimates"] == [SUMMARY] and d["hr_max"]["bpm"] == 190 and d["h4"]["streak"] == 1
    assert d["records"][1]["lt2"]["hr"] == 166 and d["records"][1]["details"] == {"h2": None}


def test_estimate_save_without_values_stores_nothing(garmin, stub_estimate) -> None:
    stub_estimate["record"] = None
    out = run("estimate", "--save")
    assert out["saved"] is None and out["record"] is None
    assert stored()["records"] == [] and len(stored()["estimates"]) == 1


def test_estimate_save_refused_record_still_logs(garmin, stub_estimate) -> None:
    stub_estimate["record"] = {**CANDIDATE, "lt1": {"hr": 170, "pace_s_per_km": 320}}
    out = run("estimate", "--save")
    assert out["saved"] is None and "LT1 HR" in out["save_refused"] and "logged" in out["save_refused"]
    assert out["calls"] == 3 and out["record"]["lt1"]["hr"] == 170
    d = stored()
    assert d["records"] == [] and len(d["estimates"]) == 1


def test_estimate_merge_keeps_h4_written_meanwhile(garmin, stub_estimate) -> None:
    def concurrent():   # an analyze ramp lands between the snapshot and the merge
        with zones.transaction() as data:
            zones.record_alpha1_outcome(data, "not_monotonic", day=TODAY - timedelta(days=1), activity=99)

    stub_estimate["concurrent"] = concurrent
    out = run("estimate")
    h4 = stored()["h4"]
    assert h4["counted"] == [99, 1] and h4["streak"] == 2 and h4["unreliable"] is True
    assert out["h4"] == h4


def test_estimate_retry_h4_resets_then_reapplies(garmin, stub_estimate) -> None:
    with zones.transaction() as data:
        zones.record_alpha1_outcome(data, "not_monotonic", day=TODAY, activity=7)
        zones.record_alpha1_outcome(data, "not_monotonic", day=TODAY, activity=8)
    assert stored()["h4"]["unreliable"] is True
    run("estimate", "--retry-h4")
    h4 = stored()["h4"]
    assert h4["unreliable"] is False and h4["streak"] == 1 and h4["counted"] == [1]   # reset clears counted ids


def test_show_tolerates_a_failing_list_read(garmin) -> None:
    garmin["zones"] = [FLOORS]
    garmin["errors"]["get_activities_by_date"] = ApiError("rate_limited", "slow down", 429)
    out = run("show")
    assert out["due"] is None and "rate_limited" in out["due_error"] and out["garmin"]["zone4Floor"] == 165
    garmin["errors"]["get_activities_by_date"] = ApiError("auth_required", "no tokens", 401)
    fails("show", "--no-cache", code="auth_required")


def test_estimate_arguments_are_checked(garmin, stub_estimate) -> None:
    fails("estimate", "--weeks", "0")
    fails("estimate", "--max-calls", "-1")


def test_estimate_real_run_on_an_empty_account(garmin) -> None:
    out = run("estimate")
    assert out["calls"] == len(garmin["calls"]) and out["deferred"] == [] and out["record"] is None
    d = stored()
    assert len(d["estimates"]) == 1 and d["records"] == []


# --- analyze ---

def drift_garmin(g, *, hr2=146, hr1=140, n=4800, speed=lambda t: 3.2):
    g["details"] = details_for(n, lambda t: hr1 if t < 2700 else hr2, speed)
    g["splits"] = laps_for(900, 3600, 300)


def test_analyze_drift_at_lt1_without_save(garmin) -> None:
    drift_garmin(garmin)
    out = run("analyze", "77", "--test", "drift")
    assert out["status"] == "ok" and out["verdict"] == "at LT1" and out["lt1"]["hr"] == 140
    assert out["test"] == "drift" and out["activity_id"] == 77 and out["hr_source"] == "strap"
    assert out["saved"] is None and out["record"]["source"] == "field_test"
    assert sorted(set(methods(garmin))) == ["get_activity", "get_activity_details", "get_activity_splits"]
    d = stored()
    assert d["records"] == [] and d["hr_max"]["bpm"] >= 146 and d["hr_max"]["activity"] == 77


def test_analyze_drift_save(garmin) -> None:
    drift_garmin(garmin)
    out = run("analyze", "77", "--test", "drift", "--save")
    rec = out["saved"]
    assert rec["id"] == 1 and rec["source"] == "field_test" and rec["confidence"] == "medium"
    assert rec["lt1"]["hr"] == 140 and rec["lt2"] is None and rec["activities"] == [77]
    assert rec["date"] == day(3) and rec["details"]["test"] == "F1" and rec["details"]["drift_pct"] > 3.5
    assert stored()["current"] == {"lt1": None, "lt2": None}


def test_analyze_drift_confidence_low_when_f3_disagrees(garmin) -> None:
    ze_record(date_=day(5), lt1=(150, 300), lt2=None, source="field_test")
    with zones.transaction() as data:
        data["records"][0]["details"] = {"test": "F3"}
    drift_garmin(garmin)
    rec = run("analyze", "77", "--test", "drift", "--save")["saved"]
    assert rec["confidence"] == "low" and "F3" in (rec["notes"] or "")


def test_analyze_drift_confidence_stays_medium_when_f3_agrees(garmin) -> None:
    ze_record(date_=day(5), lt1=(142, 300), lt2=None, source="field_test")
    with zones.transaction() as data:
        data["records"][0]["details"] = {"test": "F3"}
    drift_garmin(garmin)
    rec = run("analyze", "77", "--test", "drift", "--save")["saved"]
    assert rec["confidence"] == "medium" and "agrees" in (rec["notes"] or "")


def test_analyze_drift_old_f3_is_ignored(garmin) -> None:
    ze_record(date_=day(40), lt1=(150, 300), lt2=None, source="field_test")
    with zones.transaction() as data:
        data["records"][0]["details"] = {"test": "F3"}
    drift_garmin(garmin)
    assert run("analyze", "77", "--test", "drift", "--save")["saved"]["confidence"] == "medium"


@pytest.mark.parametrize(("hr2", "verdict", "next_hr"), [(141, "below LT1", 145), (156, "above LT1", 135)])
def test_analyze_drift_save_refuses_other_verdicts(garmin, hr2, verdict, next_hr) -> None:
    drift_garmin(garmin, hr2=hr2)
    out = run("analyze", "77", "--test", "drift", "--save")
    assert out["saved"] is None and out["verdict"] == verdict and out["retest_hr"] == next_hr   # full analysis
    assert verdict in out["save_refused"] and str(next_hr) in out["save_refused"]
    d = stored()
    assert d["records"] == [] and d["hr_max"] is not None        # the local write of hr_max still happened


def test_analyze_drift_save_refuses_uneven_pace(garmin) -> None:
    drift_garmin(garmin, speed=lambda t: 3.2 + (0.6 if (t // 60) % 2 else -0.6))
    out = run("analyze", "77", "--test", "drift")
    assert out["uneven_pace"] is True and out["saveable"] is False
    out = run("analyze", "77", "--test", "drift", "--save")
    assert out["saved"] is None and "uneven" in out["save_refused"]
    assert stored()["records"] == []


def test_analyze_drift_rejected_save_refused(garmin) -> None:
    drift_garmin(garmin, n=1500)
    garmin["splits"] = laps_for(600, 900)
    out = run("analyze", "77", "--test", "drift", "--save")
    assert out["status"] == "rejected" and out["saved"] is None and "rejected" in out["save_refused"]
    assert run("analyze", "77", "--test", "drift")["save_refused"] is None    # nothing asked, nothing refused


def test_analyze_time_trial(garmin) -> None:
    garmin["details"] = details_for(2100, lambda t: 155 if t < 900 else 168, lambda t: 3.0 if t < 300 else 4.0)
    garmin["splits"] = laps_for(300, 1800)
    out = run("analyze", "88", "--test", "time_trial")
    assert out["status"] == "ok" and out["lt2"] == {"hr": 168, "pace_s_per_km": 250}
    assert out["saved"] is None and stored()["records"] == []
    out = run("analyze", "88", "--test", "time_trial", "--save")
    rec = out["saved"]
    assert rec["source"] == "field_test" and rec["lt2"]["hr"] == 168 and rec["lt1"] is None
    assert rec["confidence"] == "medium" and rec["details"]["test"] == "F2"


def test_analyze_time_trial_fade_saves_low_and_rejected_is_refused(garmin) -> None:
    garmin["details"] = details_for(2100, lambda t: 160, lambda t: 3.0 if t < 300 else (4.4 if t < 1200 else 3.6))
    garmin["splits"] = laps_for(300, 1800)
    rec = run("analyze", "88", "--test", "time_trial", "--save")["saved"]
    assert rec["confidence"] == "low"
    garmin["details"] = details_for(900, lambda t: 160, lambda t: 3.0)
    garmin["splits"] = laps_for(300, 600)
    out = run("analyze", "89", "--test", "time_trial", "--save")
    assert out["saved"] is None and "rejected" in out["save_refused"] and len(stored()["records"]) == 1


def test_analyze_wrist_run_is_low_confidence(garmin) -> None:
    garmin["activity"] = {"metadataDTO": {"sensors": None}, "summaryDTO": {"startTimeLocal": f"{day(3)}T08:00:00.0"}}
    garmin["details"] = details_for(2100, lambda t: 155 if t < 900 else 168, lambda t: 3.0 if t < 300 else 4.0)
    garmin["splits"] = laps_for(300, 1800)
    out = run("analyze", "88", "--test", "time_trial", "--save")
    assert out["hr_source"] == "wrist" and out["saved"]["confidence"] == "low"
    assert stored()["hr_max"] is None                      # a wrist run never raises HRmax


@pytest.fixture
def ramp_stub(monkeypatch, _home):
    seen = {"result": None}
    (_home / "fit").mkdir()
    (_home / "fit" / "55.json").write_text(json.dumps({"start": "s", "records": [], "rr_s": [0.4],
                                                       "rr_msgs": [[0, 1]]}))
    monkeypatch.setattr(fit, "aligned_rr", lambda fd: [{"t_s": 1.0}])

    def ramp(aligned, *, laps, treadmill):
        seen["treadmill"], seen["laps"] = treadmill, len(laps)
        return seen["result"]

    monkeypatch.setattr(thresholds, "ramp", ramp)
    return seen


OK_RAMP = {"status": "ok", "outcome": "usable", "lt1": {"hr": 145, "pace_s_per_km": 294}, "confidence": "medium",
           "bracketed": True, "saveable": True, "steps": [], "reasons": [], "notes": [], "gate": {}, "fit": {}}
BAD_RAMP = {"status": "alpha1_unusable", "outcome": "not_monotonic", "lt1": None, "confidence": None,
            "bracketed": False, "saveable": False, "steps": [], "reasons": ["not monotonic, rho = 0.49"],
            "notes": [], "gate": {}, "fit": None}


def ramp_garmin(g):
    g["details"] = details_for(3000, lambda t: 140, lambda t: 3.0)
    g["splits"] = laps_for(600, 240, 240, 240)


def test_analyze_ramp_without_save_records_the_gate_outcome(garmin, ramp_stub) -> None:
    ramp_garmin(garmin)
    ramp_stub["result"] = BAD_RAMP
    out = run("analyze", "55", "--test", "ramp", "--treadmill")
    assert out["status"] == "alpha1_unusable" and out["saved"] is None and ramp_stub["treadmill"] is True
    assert "download_activity" not in methods(garmin)       # the FIT file was cached
    d = stored()
    assert d["records"] == [] and d["h4"]["streak"] == 1 and d["h4"]["counted"] == [55]
    run("analyze", "55", "--test", "ramp")
    assert stored()["h4"]["streak"] == 1                     # the same activity counts once
    assert out["h4"]["streak"] == 1


def test_analyze_ramp_save_and_refusal(garmin, ramp_stub) -> None:
    ramp_garmin(garmin)
    ramp_stub["result"] = OK_RAMP
    rec = run("analyze", "55", "--test", "ramp", "--save")["saved"]
    assert rec["source"] == "field_test" and rec["lt1"] == {"hr": 145, "pace_s_per_km": 294}
    assert rec["confidence"] == "medium" and rec["details"]["test"] == "F3" and rec["activities"] == [55]
    d = stored()
    assert d["h4"]["counted"] == [55] and d["h4"]["streak"] == 0
    ramp_stub["result"] = BAD_RAMP
    out = run("analyze", "55", "--test", "ramp", "--save")
    assert out["saved"] is None and "alpha1_unusable" in out["save_refused"] and "rho" in out["save_refused"]
    assert len(stored()["records"]) == 1


def test_analyze_ramp_refusal_still_counts_toward_h4(garmin, ramp_stub, _home) -> None:
    ramp_garmin(garmin)
    ramp_stub["result"] = BAD_RAMP
    (_home / "fit" / "56.json").write_text((_home / "fit" / "55.json").read_text())
    run("analyze", "55", "--test", "ramp", "--save")
    run("analyze", "56", "--test", "ramp", "--save")
    h4 = stored()["h4"]
    assert h4["unreliable"] is True and h4["counted"] == [55, 56]


def test_analyze_ramp_downloads_the_fit_when_not_cached(garmin, ramp_stub, monkeypatch) -> None:
    ramp_garmin(garmin)
    ramp_stub["result"] = OK_RAMP
    seen = {}

    async def get_fit(rid, *, tracker):
        seen["id"] = rid
        return {"records": [], "rr_s": [0.4], "rr_msgs": [[0, 1]]}

    monkeypatch.setattr(fit, "get_fit", get_fit)
    run("analyze", "60", "--test", "ramp")
    assert seen["id"] == 60


def test_analyze_ramp_unreadable_fit_is_marked_and_reported(garmin, ramp_stub, monkeypatch) -> None:
    ramp_garmin(garmin)

    async def get_fit(rid, *, tracker):
        raise fit.FitUnreadable(rid, "bad zip")

    monkeypatch.setattr(fit, "get_fit", get_fit)
    out = run("analyze", "61", "--test", "ramp")
    assert out["status"] == "rejected" and any("unreadable" in r for r in out["reasons"])
    assert fit.no_rr(61) == "fit_unreadable" and stored()["h4"]["counted"] == []
    out = run("analyze", "61", "--test", "ramp")                 # remembered: no second download
    assert "no RR data" in " ".join(out["reasons"])


def test_analyze_ramp_rr_rejected_is_marked(garmin, ramp_stub, monkeypatch) -> None:
    ramp_garmin(garmin)

    def bad(fd):
        raise fit.RRRejected("positions do not match")

    monkeypatch.setattr(fit, "aligned_rr", bad)
    out = run("analyze", "55", "--test", "ramp")
    assert out["status"] == "rejected" and fit.no_rr(55) == "rr_rejected"


def test_analyze_arguments(garmin) -> None:
    fails("analyze", "77", "--test", "vo2")
    assert "--test" in usage("analyze", "77")
    usage("analyze", "--test", "drift")
    garmin["errors"]["get_activity"] = ApiError("not_found", "gone", 404)
    drift_garmin(garmin)
    fails("analyze", "77", "--test", "drift", code="not_found")
