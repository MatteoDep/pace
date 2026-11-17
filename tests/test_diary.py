"""Diary model: load/save, profile, entries, show filters, corruption."""

import json
from datetime import date, datetime

import pytest

from pace import diary
from pace.envelope import PaceError


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    return tmp_path


def _add(data, **kw):
    args = {"kind": "note", "text": "t", "on": "2026-11-17", "tags": [], "refs": [], "due": None}
    return diary.add_entry(data, **{**args, **kw})


def test_missing_file_bootstraps_empty(tmp_path) -> None:
    assert diary.load() == {"profile": {}, "entries": [], "next_id": 1}
    assert not (tmp_path / "diary.json").exists()


def test_save_round_trips_and_corrupt_untouched(tmp_path) -> None:
    with diary.transaction() as d:
        _add(d, text="hello", tags=["x"])
    assert diary.load()["entries"][0]["text"] == "hello"
    p = tmp_path / "diary.json"
    p.write_text("{not json")
    with pytest.raises(PaceError) as exc:
        diary.load()
    assert exc.value.code == "internal" and str(p) in (exc.value.hint or "")
    assert p.read_text() == "{not json"


def test_set_profile_keys_and_updated() -> None:
    data = diary.load()
    prof = diary.set_profile(data, "goal", "sub-1:45 half")
    diary.set_profile(data, "event.name", "Verona HM")
    diary.set_profile(data, "event.date", "2026-11-15")
    assert prof["goal"] == "sub-1:45 half" and prof["event"] == {"name": "Verona HM", "date": "2026-11-15"}
    assert prof["updated"] == date.today().isoformat()
    with pytest.raises(PaceError) as exc:
        diary.set_profile(data, "weight", "70")
    assert exc.value.code == "bad_args"
    with pytest.raises(PaceError):
        diary.set_profile(data, "event.date", "soon")
    with pytest.raises(PaceError):
        diary.set_profile(data, "goal", "   ")


def test_new_profile_keys() -> None:
    data = diary.load()
    for key in ("background", "other_sports", "strength", "preferences"):
        assert diary.set_profile(data, key, " v ")[key] == "v"
    diary.set_profile(data, "checkin.weight_days", "10")
    assert data["profile"]["checkin"]["weight_days"] == 10
    for bad in ("0", "x"):
        with pytest.raises(PaceError) as exc:
            diary.set_profile(data, "checkin.weight_days", bad)
        assert exc.value.code == "bad_args"
    diary.set_profile(data, "dashboard.url", "https://claude.ai/artifact/abc")
    assert data["profile"]["dashboard"]["url"] == "https://claude.ai/artifact/abc"
    with pytest.raises(PaceError) as exc:
        diary.set_profile(data, "dashboard.url", "http://example.com")
    assert exc.value.code == "bad_args"
    diary.set_profile(data, "checkin.last", "now")
    last = datetime.fromisoformat(data["profile"]["checkin"]["last"])
    assert last.utcoffset() is not None
    with pytest.raises(PaceError) as exc:
        diary.set_profile(data, "checkin.last", "yesterday")
    assert exc.value.code == "bad_args"


def test_checkin_last_normalized() -> None:
    data = diary.load()
    diary.set_profile(data, "checkin.last", "2026-11-17T08:00")
    stored = data["profile"]["checkin"]["last"]
    assert datetime.fromisoformat(stored).utcoffset() is not None
    assert stored.startswith("2026-11-17T08:00:00")
    for bad in ("1_0", "91"):
        with pytest.raises(PaceError) as exc:
            diary.set_profile(data, "checkin.weight_days", bad)
        assert exc.value.code == "bad_args"


def test_unset_and_updated() -> None:
    data = diary.load()
    diary.set_profile(data, "event.name", "n")
    diary.set_profile(data, "event.date", "2026-11-15")
    diary.unset_profile(data, "event.name")
    assert data["profile"]["event"] == {"date": "2026-11-15"}
    diary.unset_profile(data, "event.date")
    assert "event" not in data["profile"]
    with pytest.raises(PaceError) as exc:
        diary.unset_profile(data, "nope")
    assert exc.value.code == "bad_args"
    data["profile"]["updated"] = "sentinel"
    diary.set_profile(data, "checkin.last", "now")
    assert data["profile"]["updated"] == "sentinel"
    diary.set_profile(data, "goal", "g")
    assert data["profile"]["updated"] == date.today().isoformat()


def test_add_assigns_incrementing_ids_never_reused() -> None:
    data = diary.load()
    a = _add(data, kind="plan", text="Tue intervals", on=None)
    b = _add(data, kind="feedback", text="felt heavy", on="2026-09-05", tags=["legs"])
    assert (a["id"], b["id"]) == (1, 2) and a["date"] == date.today().isoformat()
    assert b == {"id": 2, "date": "2026-09-05", "kind": "feedback", "text": "felt heavy",
                 "tags": ["legs"], "refs": [], "due": None, "resolved": None}
    removed = diary.remove_entry(data, 2)
    assert removed["id"] == 2 and [e["id"] for e in data["entries"]] == [1]
    assert _add(data)["id"] == 3
    with pytest.raises(PaceError) as exc:
        diary.remove_entry(data, 2)
    assert exc.value.code == "not_found"


def test_add_validation() -> None:
    data = diary.load()
    with pytest.raises(PaceError) as exc:
        _add(data, kind="rant")
    assert exc.value.code == "bad_args" and "kind" in exc.value.message
    with pytest.raises(PaceError):
        _add(data, text="   ")
    assert data["next_id"] == 1


def test_decision_kind_refs_and_due() -> None:
    data = diary.load()
    text = "advised: 2 quality / chose: 3 / because: race in May / check: HRV after 3 weeks"
    e = _add(data, kind="decision", text=text, refs=["session:12"], due="2026-12-08", on="2026-11-17")
    assert e == {"id": 1, "date": "2026-11-17", "kind": "decision", "text": text, "tags": [],
                 "refs": ["session:12"], "due": "2026-12-08", "resolved": None}


def test_bad_refs_rejected() -> None:
    data = diary.load()
    for bad in ("activity:abc", "run:12", "", "session:0", "activity:5\n"):
        with pytest.raises(PaceError) as exc:
            _add(data, refs=[bad])
        assert exc.value.code == "bad_args"
    assert data["entries"] == [] and data["next_id"] == 1
    assert _add(data, refs=["entry:3"])["refs"] == ["entry:3"]


def test_show_filters_and_active() -> None:
    data = diary.load()
    diary.set_profile(data, "goal", "g")
    _add(data, kind="constraint", text="no Mondays", on="2026-08-01")
    _add(data, kind="injury", text="left achilles", on="2026-08-20")
    for i in range(25):
        _add(data, kind="recommendation", text=f"r{i}", on=f"2026-09-{(i % 6) + 1:02d}")
    out, truncated = diary.show(data, since=None, kind=None, ref=None, limit=20)
    assert truncated
    assert out["profile"]["goal"] == "g"
    assert [e["text"] for e in out["active"]] == ["no Mondays", "left achilles"]
    assert len(out["entries"]) == 20
    dates = [e["date"] for e in out["entries"]]
    assert dates == sorted(dates, reverse=True)
    same_day = [e["id"] for e in out["entries"] if e["date"] == dates[0]]
    assert same_day == sorted(same_day, reverse=True)
    only, _ = diary.show(data, since="2026-09-06", kind="recommendation", ref=None, limit=50)
    assert only["entries"] and all(e["date"] >= "2026-09-06" for e in only["entries"])
    inj, _ = diary.show(data, since=None, kind="injury", ref=None, limit=5)
    assert inj["entries"][0]["text"] == "left achilles"
    with pytest.raises(PaceError):
        diary.show(data, since=None, kind=None, ref=None, limit=0)


def test_show_truncated_flag() -> None:
    data = diary.load()
    for _ in range(3):
        _add(data)
    assert diary.show(data, since=None, kind=None, ref=None, limit=2)[1] is True
    assert diary.show(data, since=None, kind=None, ref=None, limit=3)[1] is False


def test_show_due_and_ref_filter() -> None:
    data = diary.load()
    first = _add(data, due="2026-11-10", refs=["activity:5"])
    second = _add(data, due="2026-11-17")
    _add(data, due="2026-11-24", refs=["activity:5", "session:1"])
    _add(data)
    out, _ = diary.show(data, since=None, kind=None, ref=None, limit=20, today=date(2026, 11, 17))
    assert [e["id"] for e in out["due"]] == [first["id"], second["id"]]
    diary.set_due(data, first["id"], None)
    out, _ = diary.show(data, since=None, kind=None, ref=None, limit=20, today=date(2026, 11, 17))
    assert [e["id"] for e in out["due"]] == [second["id"]]
    out, _ = diary.show(data, since=None, kind=None, ref="activity:5", limit=20)
    assert [e["id"] for e in out["entries"]] == [3, 1]
    with pytest.raises(PaceError):
        diary.set_due(data, 99, None)


def test_old_file_backfills_fields(tmp_path) -> None:
    p = tmp_path / "diary.json"
    old = {"id": 4, "date": "2026-01-01", "kind": "note", "text": "x", "tags": []}
    p.write_text(json.dumps({"profile": {}, "entries": [old], "next_id": 2}))
    data = diary.load()
    e = data["entries"][0]
    assert e["refs"] == [] and e["due"] is None and e["resolved"] is None
    assert data["next_id"] == 5
    p.write_text(json.dumps({"profile": {}, "entries": {}, "next_id": 1}))
    with pytest.raises(PaceError) as exc:
        diary.load()
    assert exc.value.code == "internal" and str(p) in (exc.value.hint or "")


def test_resolve_and_reopen() -> None:
    data = diary.load()
    inj = _add(data, kind="injury", text="achilles", on="2026-03-01")
    note = _add(data)
    diary.resolve(data, inj["id"], "2026-04-05")
    out, _ = diary.show(data, since=None, kind=None, ref=None, limit=20)
    assert out["active"] == []
    assert [e for e in out["entries"] if e["id"] == inj["id"]][0]["resolved"] == "2026-04-05"
    with pytest.raises(PaceError) as exc:
        diary.resolve(data, inj["id"], "2026-02-01")
    assert exc.value.code == "bad_args"
    with pytest.raises(PaceError) as exc:
        diary.resolve(data, note["id"], None)
    assert exc.value.code == "bad_args"
    diary.reopen(data, inj["id"])
    out, _ = diary.show(data, since=None, kind=None, ref=None, limit=20)
    assert [e["id"] for e in out["active"]] == [inj["id"]]


def test_fresh_pace_home(tmp_path, monkeypatch) -> None:
    home = tmp_path / "new"
    monkeypatch.setattr("pace.config.PACE_HOME", home)
    with diary.transaction() as d:
        _add(d)
    assert (home / "diary.json").exists()


def test_transaction_saves_only_on_success(tmp_path) -> None:
    with diary.transaction() as d:
        _add(d, text="keep")
    p = tmp_path / "diary.json"
    before = p.read_text()
    with pytest.raises(RuntimeError), diary.transaction() as d:
        _add(d, text="lost")
        raise RuntimeError("boom")
    assert p.read_text() == before


def test_unicode_text_round_trip() -> None:
    text = "gambe pesanti, perché ho dormito poco"
    with diary.transaction() as d:
        _add(d, kind="feedback", text=text)
    assert diary.load()["entries"][0]["text"] == text
