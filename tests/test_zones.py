"""Zone store: records, per-threshold current, derivation (lt-5z-v1), H4 memory, due rule, status."""

import json
from datetime import date

import pytest

from pace import zones
from pace.envelope import PaceError

TODAY = date(2026, 11, 17)
H2 = {"a": 60, "b": 25, "v_min": 2.0, "v_max": 4.0}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    return tmp_path


def rec(source="estimate", on="2026-11-01", lt1=None, lt2=None, **kw):
    out = {"date": on, "source": source, "method": "m"}
    if lt1 is not None:
        out["lt1"] = {"hr": lt1[0], "pace_s_per_km": lt1[1] if len(lt1) > 1 else None}
    if lt2 is not None:
        out["lt2"] = {"hr": lt2[0], "pace_s_per_km": lt2[1] if len(lt2) > 1 else None}
    out.update(kw)
    return out


def add(data, **kw):
    return zones.add_record(data, rec(**kw), today=TODAY)


def bad(fn, *a, **kw):
    with pytest.raises(PaceError) as exc:
        fn(*a, **kw)
    assert exc.value.code == "bad_args"
    return exc.value


# --- store ---

def test_missing_file_bootstraps_empty(tmp_path) -> None:
    d = zones.load()
    assert d["records"] == [] and d["current"] == {"lt1": None, "lt2": None}
    assert d["next_id"] == 1 and d["estimates"] == [] and d["hr_max"] is None
    assert d["h4"] == {"streak": 0, "unreliable": False, "since": None, "counted": []}
    assert not (tmp_path / "zones.json").exists()


def test_transaction_round_trip_and_corrupt(tmp_path) -> None:
    with zones.transaction() as d:
        add(d, lt1=(140, 330))
    assert zones.load()["records"][0]["lt1"]["hr"] == 140
    p = tmp_path / "zones.json"
    p.write_text("{nope")
    with pytest.raises(PaceError) as exc:
        zones.load()
    assert exc.value.code == "internal" and str(p) in (exc.value.hint or "")
    assert p.read_text() == "{nope"
    p.write_text(json.dumps({"records": "x"}))
    with pytest.raises(PaceError) as exc:
        zones.load()
    assert exc.value.code == "internal"


def test_transaction_discards_on_error() -> None:
    with pytest.raises(PaceError):
        with zones.transaction() as d:
            add(d, lt1=(140, 330))
            add(d, lt1=(10, 330))
    assert zones.load()["records"] == []


# --- rounding ---

def test_r_rounds_half_up() -> None:
    assert zones.R(312.5) == 313 and round(312.5) == 312
    assert zones.R(142.3) == 142 and zones.R(0.5) == 1


# --- add validation ---

def test_add_validation() -> None:
    d = zones.load()
    bad(zones.add_record, d, rec(source="bogus", lt1=(140,)), today=TODAY)
    bad(zones.add_record, d, rec(on="nope", lt1=(140,)), today=TODAY)
    bad(zones.add_record, d, rec(on="2026-11-18", lt1=(140,)), today=TODAY)
    bad(zones.add_record, d, rec(lt1=(29,)), today=TODAY)
    bad(zones.add_record, d, rec(lt1=(231,)), today=TODAY)
    bad(zones.add_record, d, rec(lt1=(140.5,)), today=TODAY)
    bad(zones.add_record, d, rec(lt1=(160,), lt2=(160,)), today=TODAY)
    bad(zones.add_record, d, rec(lt1=(170,), lt2=(160,)), today=TODAY)
    bad(zones.add_record, d, rec(lt1=(140,), lt2=(160,), max_hr=160), today=TODAY)
    bad(zones.add_record, d, rec(lt1=(140, 250), lt2=(160, 300)), today=TODAY)
    bad(zones.add_record, d, rec(lt1=(140, 300), lt2=(160, 300)), today=TODAY)
    bad(zones.add_record, d, rec(), today=TODAY)
    bad(zones.add_record, d, rec(lt1=(None,)), today=TODAY)
    assert d["records"] == [] and d["next_id"] == 1


def test_add_today_and_pace_only_ok() -> None:
    d = zones.load()
    r = add(d, on="2026-11-17", lt1=(None, 330))
    assert r["id"] == 1 and r["lt2"] is None and r["source"] == "estimate"
    assert r["activities"] == [] and r["details"] == {}


# --- accept / current / remove ---

def test_accept_sets_current_for_all_thresholds() -> None:
    d = zones.load()
    r = add(d, lt1=(142, 330), lt2=(160, 250))
    assert zones.current(d) == {"lt1": None, "lt2": None}
    zones.accept(d, r["id"])
    cur = zones.current(d)
    assert cur["lt1"]["id"] == cur["lt2"]["id"] == r["id"]


def test_accept_lt1_only_keeps_lt2() -> None:
    d = zones.load()
    full = add(d, lt1=(142,), lt2=(160,))
    zones.accept(d, full["id"])
    f1 = add(d, source="field_test", on="2026-11-10", lt1=(145,))
    zones.accept(d, f1["id"])
    cur = zones.current(d)
    assert cur["lt1"]["id"] == f1["id"] and cur["lt2"]["id"] == full["id"]


def test_accept_only() -> None:
    d = zones.load()
    a = add(d, lt1=(140,), lt2=(160,))
    b = add(d, lt1=(144,), lt2=(165,))
    zones.accept(d, a["id"])
    zones.accept(d, b["id"], only="lt2")
    cur = zones.current(d)
    assert cur["lt1"]["id"] == a["id"] and cur["lt2"]["id"] == b["id"]
    c = add(d, lt1=(140,))
    bad(zones.accept, d, c["id"], only="lt2")
    bad(zones.accept, d, c["id"], only="lt3")


def test_accept_refused_when_pair_inverted() -> None:
    d = zones.load()
    a = add(d, lt1=(140,), lt2=(160,))
    zones.accept(d, a["id"])
    low = add(d, lt2=(138,))
    bad(zones.accept, d, low["id"])
    assert zones.current(d)["lt2"]["id"] == a["id"]
    high = add(d, lt1=(165,))
    bad(zones.accept, d, high["id"], only="lt1")
    assert zones.current(d)["lt1"]["id"] == a["id"]


def test_accept_unknown_id_not_found_local_hint() -> None:
    d = zones.load()
    with pytest.raises(PaceError) as exc:
        zones.accept(d, 9)
    assert exc.value.code == "not_found" and "garmin" not in (exc.value.hint or "").lower()


def test_remove_and_force() -> None:
    d = zones.load()
    a = add(d, lt1=(140,), lt2=(160,))
    b = add(d, lt1=(141,))
    zones.accept(d, a["id"])
    zones.remove(d, b["id"], force=False)
    assert [r["id"] for r in d["records"]] == [a["id"]]
    bad(zones.remove, d, a["id"], force=False)
    zones.remove(d, a["id"], force=True)
    assert d["records"] == [] and zones.current(d) == {"lt1": None, "lt2": None}
    with pytest.raises(PaceError) as exc:
        zones.remove(d, 77, force=False)
    assert exc.value.code == "not_found"


# --- lab precedence ---

def test_lab_newer_becomes_current_per_threshold() -> None:
    d = zones.load()
    e = add(d, on="2026-10-01", lt1=(140,), lt2=(160,))
    zones.accept(d, e["id"])
    lab = add(d, source="lab", on="2026-10-20", lt1=(143,), lt2=(163,))
    cur = zones.current(d)
    assert cur["lt1"]["id"] == cur["lt2"]["id"] == lab["id"]


def test_lab_older_does_not_replace_and_lt2_only_leaves_lt1() -> None:
    d = zones.load()
    e = add(d, on="2026-10-01", lt1=(140,), lt2=(160,))
    zones.accept(d, e["id"])
    add(d, source="lab", on="2026-09-01", lt1=(130,), lt2=(150,))
    assert zones.current(d)["lt1"]["id"] == e["id"] and zones.current(d)["lt2"]["id"] == e["id"]
    lab2 = add(d, source="lab", on="2026-10-05", lt2=(162,))
    cur = zones.current(d)
    assert cur["lt1"]["id"] == e["id"] and cur["lt2"]["id"] == lab2["id"]


def test_lab_same_date_becomes_current_and_first_lab_current() -> None:
    d = zones.load()
    first = add(d, source="lab", on="2026-10-01", lt1=(140,), lt2=(160,))
    assert zones.current(d)["lt1"]["id"] == first["id"]
    second = add(d, source="lab", on="2026-10-01", lt1=(141,))
    assert zones.current(d)["lt1"]["id"] == second["id"]


def test_lab_auto_current_refused_if_pair_inverted_and_nothing_stored() -> None:
    d = zones.load()
    e = add(d, lt1=(140,), lt2=(160,))
    zones.accept(d, e["id"])
    bad(zones.add_record, d, rec(source="lab", on="2026-11-05", lt2=(138,)), today=TODAY)
    assert len(d["records"]) == 1


def test_lab_max_hr_raises_hr_max() -> None:
    d = zones.load()
    add(d, source="lab", lt1=(140,), lt2=(160,), max_hr=188)
    assert d["hr_max"]["bpm"] == 188
    add(d, source="lab", on="2026-10-01", lt1=(139,), max_hr=180)
    assert d["hr_max"]["bpm"] == 188


def test_lab_expiry_and_candidates() -> None:
    d = zones.load()
    lab = add(d, source="lab", on="2026-04-10", lt1=(140,), lt2=(160,))
    est = add(d, on="2026-11-01", lt1=(143,), lt2=(162,))
    s = zones.status(d, today=TODAY, garmin_zones=None, runs_since=[])
    assert s["expired"] == {"lt1": True, "lt2": True}
    assert s["current"]["lt1"]["id"] == lab["id"]
    assert [c["id"] for c in s["candidates"]] == [est["id"]]
    assert set(s["candidates"][0]["candidate_for"]) == {"lt1", "lt2"}
    # exactly 183 days is not yet expired
    d2 = zones.load()
    add(d2, source="lab", on="2026-05-18", lt1=(140,))
    s2 = zones.status(d2, today=TODAY, garmin_zones=None, runs_since=[])
    assert s2["expired"] == {"lt1": False, "lt2": False}


# --- derive ---

def test_derive_worked_example() -> None:
    lt1 = {"lt1": {"hr": 142, "pace_s_per_km": 304}, "details": {"h2": H2}}
    lt2 = {"lt2": {"hr": 160, "pace_s_per_km": 250}, "cs_mps": 4.0, "d_prime_m": 180}
    z = zones.derive(lt1, lt2, h2=H2, hr_max=186)
    assert z["rule"] == "lt-5z-v1"
    assert z["floors"] == {"zone1Floor": 114, "zone2Floor": 128, "zone3Floor": 142,
                           "zone4Floor": 160, "zone5Floor": 173, "maxHeartRateUsed": 186}
    assert z["pace_floors"] == {"z1": 463, "z2": 368, "z3": 304, "z4": 250, "z5": 235}
    assert [(x["zone"], x["hr_min"], x["hr_max"]) for x in z["three_hr"]] == [
        (1, None, 141), (2, 142, 159), (3, 160, None)]
    assert len(z["three_pace"]) == 3


def test_derive_hrmax_unknown() -> None:
    lt1 = {"lt1": {"hr": 142, "pace_s_per_km": 304}}
    lt2 = {"lt2": {"hr": 160, "pace_s_per_km": 250}, "cs_mps": 4.0, "d_prime_m": 180}
    z = zones.derive(lt1, lt2, h2=H2, hr_max=None)
    assert z["floors"]["zone5Floor"] == 169 and z["floors"]["maxHeartRateUsed"] is None


def test_derive_z1_null_outside_h2_range() -> None:
    lt1 = {"lt1": {"hr": 142, "pace_s_per_km": 304}}
    lt2 = {"lt2": {"hr": 160, "pace_s_per_km": 250}, "cs_mps": 4.0, "d_prime_m": 180}
    z = zones.derive(lt1, lt2, h2={"a": 60, "b": 25, "v_min": 2.8, "v_max": 3.8}, hr_max=186)
    assert z["pace_floors"]["z1"] is None and z["pace_floors"]["z2"] == 368


def test_derive_lab_only_has_no_h2_pieces() -> None:
    lt1 = {"source": "lab", "lt1": {"hr": 142, "pace_s_per_km": 304}}
    lt2 = {"source": "lab", "lt2": {"hr": 160, "pace_s_per_km": 250}}
    z = zones.derive(lt1, lt2, h2=zones.h2_of(lt1, lt2), hr_max=186)
    assert z["pace_floors"] == {"z1": None, "z2": None, "z3": 304, "z4": 250, "z5": None}
    assert z["floors"]["zone1Floor"] == 114


def test_h2_of_prefers_lt1_record() -> None:
    other = {"a": 1, "b": 2, "v_min": 1, "v_max": 2}
    assert zones.h2_of({"details": {"h2": H2}}, {"details": {"h2": other}}) == H2
    assert zones.h2_of({"details": {}}, {"details": {"h2": other}}) == other
    assert zones.h2_of(None, None) is None


def test_derive_partial_records_give_nulls() -> None:
    lt1 = {"lt1": {"hr": 142, "pace_s_per_km": None}}
    z = zones.derive(lt1, None, h2=None, hr_max=186)
    assert z["floors"] is None and z["three_hr"] is None and z["three_pace"] is None
    assert z["pace_floors"] == {k: None for k in ("z1", "z2", "z3", "z4", "z5")}
    both_hr = zones.derive({"lt1": {"hr": 142, "pace_s_per_km": None}},
                           {"lt2": {"hr": 160, "pace_s_per_km": None}}, h2=None, hr_max=186)
    assert both_hr["floors"]["zone4Floor"] == 160 and both_hr["three_pace"] is None
    only_pace = zones.derive({"lt1": {"hr": None, "pace_s_per_km": 304}},
                             {"lt2": {"hr": None, "pace_s_per_km": 250}}, h2=None, hr_max=None)
    assert only_pace["floors"] is None and only_pace["pace_floors"]["z3"] == 304
    assert only_pace["three_pace"] is not None
    nothing = zones.derive(None, None, h2=None, hr_max=None)
    assert nothing["floors"] is None and nothing["three_hr"] is None


# --- hr_max ---

def test_hr_max_only_rises() -> None:
    d = zones.load()
    assert zones.update_hr_max(d, 180, activity=5, day=date(2026, 11, 1)) is True
    assert zones.update_hr_max(d, 179, activity=6, day=date(2026, 11, 2)) is False
    assert zones.update_hr_max(d, 180, activity=7, day=date(2026, 11, 3)) is False
    assert d["hr_max"] == {"bpm": 180, "activity": 5, "date": "2026-11-01"}
    assert zones.update_hr_max(d, 183, activity=8, day=date(2026, 11, 4)) is True
    assert d["hr_max"]["bpm"] == 183 and d["hr_max"]["activity"] == 8


# --- H4 memory ---

def test_h4_streak() -> None:
    d = zones.load()
    day = date(2026, 11, 1)
    zones.record_alpha1_outcome(d, "not_monotonic", day=day, activity=1)
    assert d["h4"]["streak"] == 1 and not d["h4"]["unreliable"]
    zones.record_alpha1_outcome(d, "one_sided", day=day, activity=2)
    assert d["h4"]["streak"] == 1
    zones.record_alpha1_outcome(d, "usable", day=day, activity=3)
    assert d["h4"]["streak"] == 0
    zones.record_alpha1_outcome(d, "not_monotonic", day=day, activity=4)
    zones.record_alpha1_outcome(d, "not_monotonic", day=date(2026, 11, 5), activity=5)
    assert d["h4"]["streak"] == 2 and d["h4"]["unreliable"] and d["h4"]["since"] == "2026-11-05"
    assert d["h4"]["counted"] == [1, 2, 3, 4, 5]


def test_h4_counted_activity_never_counts_twice() -> None:
    d = zones.load()
    day = date(2026, 11, 1)
    zones.record_alpha1_outcome(d, "not_monotonic", day=day, activity=1)
    zones.record_alpha1_outcome(d, "not_monotonic", day=day, activity=1)
    assert d["h4"]["streak"] == 1 and not d["h4"]["unreliable"]
    zones.record_alpha1_outcome(d, "usable", day=day, activity=1)
    assert d["h4"]["streak"] == 1


def test_h4_bad_outcome_and_retry() -> None:
    d = zones.load()
    bad(zones.record_alpha1_outcome, d, "weird", day=TODAY)
    zones.record_alpha1_outcome(d, "not_monotonic", day=TODAY, activity=1)
    zones.record_alpha1_outcome(d, "not_monotonic", day=TODAY, activity=2)
    zones.reset_h4(d)
    assert d["h4"]["streak"] == 0 and not d["h4"]["unreliable"] and d["h4"]["since"] is None
    assert d["h4"]["counted"] == [1, 2]


# --- due ---

def est(on, lt1=None, lt2=None, pace=None, partial=False):
    return {"date": on, "lt1_hr": lt1, "lt2_hr": lt2, "lt2_pace": pace,
            "methods": [], "confidence": "low", "partial": partial}


def test_due_estimate_rules() -> None:
    d = zones.load()
    r = zones.due(d, today=TODAY, runs_since=[])
    assert r["estimate"] is True and r["reasons"]
    zones.log_estimate(d, est("2026-11-10"))
    assert zones.due(d, today=TODAY, runs_since=[])["estimate"] is False
    d["estimates"][-1]["date"] = "2026-10-20"  # 28 days
    assert zones.due(d, today=TODAY, runs_since=[])["estimate"] is True
    d["estimates"][-1]["date"] = "2026-10-21"  # 27 days
    assert zones.due(d, today=TODAY, runs_since=[])["estimate"] is False


def test_due_skips_partial_estimates() -> None:
    d = zones.load()
    zones.log_estimate(d, est("2026-09-01"))
    zones.log_estimate(d, est("2026-11-15", partial=True))
    r = zones.due(d, today=TODAY, runs_since=[])
    assert r["estimate"] is True  # last complete one is 77 days old


def test_due_race_and_training_effect() -> None:
    d = zones.load()
    zones.log_estimate(d, est("2026-11-10"))
    race = {"startTimeLocal": "2026-11-12 08:00:00", "eventType": {"typeKey": "race"}}
    hard = {"startTimeLocal": "2026-11-13 08:00:00", "aerobicTrainingEffect": 4.0}
    easy = {"startTimeLocal": "2026-11-14 08:00:00", "aerobicTrainingEffect": 3.9,
            "eventType": {"typeKey": "uncategorized"}}
    assert zones.due(d, today=TODAY, runs_since=[easy])["estimate"] is False
    assert zones.due(d, today=TODAY, runs_since=[easy, race])["estimate"] is True
    assert zones.due(d, today=TODAY, runs_since=[hard])["estimate"] is True
    assert zones.due(d, today=TODAY, runs_since=[{"eventType": None}])["estimate"] is False


def test_due_detraining() -> None:
    d = zones.load()
    zones.log_estimate(d, est("2026-10-25"))

    def runs(*days):
        return [{"startTimeLocal": f"{x} 07:00:00"} for x in days]
    # 15-day break, then training from 11-02 to today (15 days) -> due
    assert zones.due(d, today=TODAY, runs_since=runs("2026-10-18", "2026-11-02", "2026-11-09"))["estimate"]
    # break then only 10 days back -> not yet
    assert not zones.due(d, today=date(2026, 11, 12),
                         runs_since=runs("2026-10-18", "2026-11-02"))["estimate"]
    # 13-day gap only -> not a break
    assert not zones.due(d, today=TODAY, runs_since=runs("2026-10-18", "2026-10-31", "2026-11-13"))["estimate"]


def test_due_field_test_worked_example() -> None:
    d = zones.load()
    ft = add(d, source="field_test", on="2026-08-01", lt2=(160, 250))
    zones.accept(d, ft["id"])
    r = zones.due(d, today=TODAY, runs_since=[])
    assert r["field_test"] == {"lt1": False, "lt2": True}
    assert any("lt2" in x and "108" in x for x in r["reasons"])


def test_due_old_field_test_without_a_current_record() -> None:
    """The 84-day rule belongs to the latest field test, not to the current record: it holds with no current."""
    d = zones.load()
    add(d, source="field_test", on="2026-08-01", lt2=(160, 250))   # saved, never accepted: no current LT2
    assert zones.current(d)["lt2"] is None
    r = zones.due(d, today=TODAY, runs_since=[])
    assert r["field_test"] == {"lt1": False, "lt2": True}
    assert any("lt2" in x and "108" in x for x in r["reasons"])
    add(d, source="field_test", on="2026-11-01", lt2=(161, 249))
    assert zones.due(d, today=TODAY, runs_since=[])["field_test"]["lt2"] is False


def test_due_field_test_estimate_drift_rule() -> None:
    d = zones.load()
    ft = add(d, source="field_test", on="2026-11-01", lt2=(160, 250))
    zones.accept(d, ft["id"])
    zones.log_estimate(d, est("2026-11-05", lt2=165))
    zones.log_estimate(d, est("2026-11-12", lt2=164))
    assert zones.due(d, today=TODAY, runs_since=[])["field_test"]["lt2"] is True
    d["estimates"][-2]["lt2_hr"] = 163
    d["estimates"][-1]["lt2_hr"] = 165
    assert zones.due(d, today=TODAY, runs_since=[])["field_test"]["lt2"] is False
    assert zones.due(d, today=TODAY, runs_since=[])["field_test"]["lt1"] is False
    # pace rule for lt2: 3% of 250 = 7.5 s/km
    d["estimates"][-2].update(lt2_hr=None, lt2_pace=258)
    d["estimates"][-1].update(lt2_hr=None, lt2_pace=242)
    assert zones.due(d, today=TODAY, runs_since=[])["field_test"]["lt2"] is True
    d["estimates"][-1]["lt2_pace"] = 255
    assert zones.due(d, today=TODAY, runs_since=[])["field_test"]["lt2"] is False
    # partial entries are skipped
    d["estimates"][-1].update(lt2_pace=242, partial=True)
    assert zones.due(d, today=TODAY, runs_since=[])["field_test"]["lt2"] is False


def test_due_lt1_computed_separately_and_estimate_record_rule() -> None:
    d = zones.load()
    e = add(d, on="2026-11-10", lt1=(140,), lt2=(160,))
    zones.accept(d, e["id"])
    r = zones.due(d, today=TODAY, runs_since=[])
    assert r["field_test"] == {"lt1": True, "lt2": True}  # estimate current, no field test
    ft1 = add(d, source="field_test", on="2026-11-11", lt1=(141,))
    zones.accept(d, ft1["id"])
    r = zones.due(d, today=TODAY, runs_since=[])
    assert r["field_test"] == {"lt1": False, "lt2": True}
    zones.log_estimate(d, est("2026-11-05", lt1=146))
    zones.log_estimate(d, est("2026-11-12", lt1=147))
    assert zones.due(d, today=TODAY, runs_since=[])["field_test"]["lt1"] is True


# --- status ---

def test_status_records_order_current_for_and_sync() -> None:
    d = zones.load()
    a = add(d, on="2026-10-01", lt1=(142, 304), lt2=(160, 250), details={"h2": H2},
            cs_mps=4.0, d_prime_m=180)
    b = add(d, on="2026-11-01", lt1=(130,))
    zones.accept(d, a["id"])
    zones.update_hr_max(d, 186, activity=1, day=date(2026, 11, 1))
    s = zones.status(d, today=TODAY, garmin_zones=None, runs_since=[])
    assert [r["id"] for r in s["records"]] == [b["id"], a["id"]]
    assert s["records"][1]["current_for"] == ["lt1", "lt2"] and s["records"][0]["current_for"] == []
    assert s["zones"]["floors"]["zone5Floor"] == 173 and s["in_sync"] is None and s["garmin"] is None
    assert s["hr_max"]["bpm"] == 186 and s["h4"]["streak"] == 0
    assert s["current"]["lt1"] == {"id": a["id"], "source": "estimate", "date": "2026-10-01",
                                   "method": "m", "confidence": None}
    assert "estimate" in s["due"] and "field_test" in s["due"]
    g = {"sport": "RUNNING", "zone1Floor": 114, "zone2Floor": 128, "zone3Floor": 142,
         "zone4Floor": 160, "zone5Floor": 173, "maxHeartRateUsed": 186}
    assert zones.status(d, today=TODAY, garmin_zones=[{"sport": "DEFAULT"}, g], runs_since=[])["in_sync"] is True
    g2 = {**g, "zone5Floor": 170}
    s2 = zones.status(d, today=TODAY, garmin_zones=[g2], runs_since=[])
    assert s2["in_sync"] is False and s2["garmin"] == g2


def test_status_empty() -> None:
    s = zones.status(zones.load(), today=TODAY, garmin_zones=[], runs_since=[])
    assert s["records"] == [] and s["zones"]["floors"] is None and s["in_sync"] is None
    assert s["current"] == {"lt1": None, "lt2": None}
