import json

import pytest
from typer.testing import CliRunner

from pace.commands.log import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")


@pytest.fixture
def fake_api(monkeypatch):
    state = {"calls": [], "resp": {"samplePk": 123, "version": 9}, "sample_pk": 123, "weight_kg": None}

    async def safe_call(method_name, *args, **kwargs):
        state["calls"].append((method_name, args, kwargs))
        if method_name in ("add_weigh_in", "add_body_composition"):
            # Garmin's add response carries no samplePk -- the day view resolves it.
            state["weight_kg"] = kwargs.get("weight")
            return True, {}, None
        if method_name == "get_daily_weigh_ins":
            if state["sample_pk"] is None:
                return True, {}, None
            return True, {"dateWeightList": [{"samplePk": state["sample_pk"], "sourceType": "MANUAL",
                                              "weight": float(state["weight_kg"] or 0) * 1000}]}, None
        return True, state["resp"], None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    monkeypatch.setattr("pace.writes.safe_call", safe_call)
    monkeypatch.setattr("pace.commands.log.safe_call", safe_call)
    return state


def _doc(res):
    assert res.stdout.count("\n") == 1, res.stdout
    return json.loads(res.stdout)


def test_weight_only_uses_add_weigh_in(fake_api) -> None:
    doc = _doc(runner.invoke(app, ["weight", "add", "66.3", "--date", "2026-09-06"]))
    assert doc["data"] == {"action": "create", "resource": "weight", "id": 123, "echo": {"weight_kg": 66.3, "date": "2026-09-06"}}
    assert fake_api["calls"][0] == ("add_weigh_in", (), {"weight": 66.3, "unitKey": "kg", "timestamp": "2026-09-06T12:00:00"})


def test_weight_with_composition_uses_body_composition(fake_api) -> None:
    doc = _doc(runner.invoke(app, ["weight", "add", "66.3", "--date", "2026-09-06", "--fat", "12.5", "--muscle", "31"]))
    m, a, kw = fake_api["calls"][0]
    assert m == "add_body_composition" and a == ()
    assert kw == {"timestamp": "2026-09-06T12:00:00", "weight": 66.3, "percent_fat": 12.5, "muscle_mass": 31.0}
    assert doc["data"]["echo"]["fat_pct"] == 12.5
    # add_body_composition returns no id either, so the day-view lookup must resolve it
    assert doc["data"]["id"] == 123
    assert fake_api["calls"][1][0] == "get_daily_weigh_ins"


def test_weight_dry_run_shows_method_and_kwargs(fake_api) -> None:
    doc = _doc(runner.invoke(app, ["weight", "add", "70", "--date", "2026-09-06", "--dry-run"]))
    assert doc["data"]["dry_run"] is True and doc["data"]["payload"]["method"] == "add_weigh_in"
    assert fake_api["calls"] == []


def test_weight_validation(fake_api) -> None:
    res = runner.invoke(app, ["weight", "add", "5"])
    assert res.exit_code == 2 and "KG" in _doc(res)["error"]["message"]
    res = runner.invoke(app, ["weight", "add", "70", "--fat", "150"])
    assert res.exit_code == 2 and "fat" in _doc(res)["error"]["message"]


def test_weight_list_single_range_call(fake_api) -> None:
    fake_api["resp"] = {
        "dailyWeightSummaries": [{"summaryDate": "2026-09-06", "latestWeight": {"weight": 66300, "samplePk": 1}, "x": None}],
        "totalAverage": {"weight": 66300},
        "dateWeightList": None,
        "junk": 1,
    }
    doc = _doc(runner.invoke(app, ["weight", "list", "--to", "2026-09-06", "--days", "7"]))
    assert fake_api["calls"][0] == ("get_weigh_ins", ("2026-08-31", "2026-09-06"), {})
    assert doc["data"] == {
        "dailyWeightSummaries": [{"summaryDate": "2026-09-06", "latestWeight": {"weight": 66300, "samplePk": 1}}],
        "totalAverage": {"weight": 66300},
    }
    assert doc["args"]["from"] == "2026-08-31" and doc["command"] == "log weight list"


def test_weight_add_resolves_samplepk_via_daily_lookup(monkeypatch) -> None:
    calls = []

    async def safe_call(method_name, *args, **kwargs):
        calls.append(method_name)
        if method_name == "get_daily_weigh_ins":
            return True, {"dateWeightList": [
                {"samplePk": 1, "weight": 70000, "sourceType": "MANUAL"},
                {"samplePk": 5, "weight": 69950, "sourceType": "MANUAL"},   # within 100 g: ours
                {"samplePk": 9, "weight": 81000, "sourceType": "MANUAL"},   # another person's
            ]}, None
        return True, {}, None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    monkeypatch.setattr("pace.writes.safe_call", safe_call)
    monkeypatch.setattr("pace.commands.log.safe_call", safe_call)

    doc = _doc(runner.invoke(app, ["weight", "add", "70", "--date", "2026-09-06"]))
    assert doc["data"]["id"] == 5
    assert calls.count("get_daily_weigh_ins") == 1

    calls.clear()
    _doc(runner.invoke(app, ["weight", "add", "70", "--date", "2026-09-06", "--dry-run"]))
    assert calls == []


def test_weight_delete(fake_api) -> None:
    fake_api["resp"] = None
    doc = _doc(runner.invoke(app, ["weight", "delete", "123", "--date", "2026-09-06"]))
    assert doc["data"] == {"action": "delete", "resource": "weight", "id": None, "echo": {"sample_pk": "123", "date": "2026-09-06"}}
    assert fake_api["calls"][0] == ("delete_weigh_in", (), {"weight_pk": "123", "cdate": "2026-09-06"})


def test_hydration(fake_api) -> None:
    doc = _doc(runner.invoke(app, ["hydration", "500", "--date", "2026-09-06"]))
    assert doc["command"] == "log hydration"
    assert fake_api["calls"][0] == ("add_hydration_data", (), {"value_in_ml": 500.0, "cdate": "2026-09-06"})
    assert doc["data"]["resource"] == "hydration" and doc["data"]["id"] is None
    assert runner.invoke(app, ["hydration", "0"]).exit_code == 2


def test_bp_add_and_list(fake_api) -> None:
    doc = _doc(runner.invoke(app, ["bp", "add", "118", "76", "52", "--date", "2026-09-06", "--notes", "morning"]))
    assert fake_api["calls"][0] == ("set_blood_pressure", (118, 76, 52), {"timestamp": "2026-09-06T12:00:00", "notes": "morning"})
    assert doc["data"] == {"action": "create", "resource": "bp", "id": 9,
                           "echo": {"systolic": 118, "diastolic": 76, "pulse": 52, "date": "2026-09-06", "notes": "morning"}}
    fake_api["resp"] = {"measurementSummaries": [{"systolic": 118}], "from": "x"}
    doc = _doc(runner.invoke(app, ["bp", "list", "--to", "2026-09-06"]))
    assert fake_api["calls"][1] == ("get_blood_pressure", ("2026-09-06", "2026-09-06"), {})
    assert doc["data"] == {"measurementSummaries": [{"systolic": 118}]}
    assert runner.invoke(app, ["bp", "add", "400", "76", "52"]).exit_code == 2


def test_bp_delete(fake_api) -> None:
    fake_api["resp"] = None
    doc = _doc(runner.invoke(app, ["bp", "delete", "9", "--date", "2026-09-06"]))
    assert doc["data"] == {"action": "delete", "resource": "bp", "id": None, "echo": {"version": "9", "date": "2026-09-06"}}
    assert fake_api["calls"][0] == ("delete_blood_pressure", (), {"version": "9", "cdate": "2026-09-06"})


def test_bp_bounds_match_garminconnect(fake_api) -> None:
    """garminconnect's `set_blood_pressure` raises ValueError outside [70,260] / [40,150] /
    [20,250], so pace must reject exactly the same readings -- otherwise a --dry-run that
    looks fine fails live."""
    for args, field in ((["299", "299", "249"], "SYS"), (["118", "160", "52"], "DIA"),
                        (["118", "76", "300"], "PULSE")):
        res = runner.invoke(app, ["bp", "add", *args, "--dry-run"])
        assert res.exit_code == 2, args
        assert field in _doc(res)["error"]["message"], args
    # the bounds are inclusive on both ends
    assert runner.invoke(app, ["bp", "add", "70", "40", "20", "--dry-run"]).exit_code == 0
    assert runner.invoke(app, ["bp", "add", "260", "150", "250", "--dry-run"]).exit_code == 0


def test_body_composition_extras_are_validated(fake_api) -> None:
    """A g-for-kg slip (`--muscle 31000`) used to go straight to Garmin."""
    for flag, value, field in (("--muscle", "31000", "muscle"), ("--bone", "3000", "bone"),
                               ("--bmi", "1", "bmi")):
        res = runner.invoke(app, ["weight", "add", "66", flag, value, "--dry-run"])
        assert res.exit_code == 2, flag
        assert field in _doc(res)["error"]["message"], flag
    assert runner.invoke(app, ["weight", "add", "66", "--muscle", "31", "--bone", "3", "--bmi", "21",
                              "--dry-run"]).exit_code == 0


def test_hydration_bounds_are_inclusive_but_exclude_zero(fake_api) -> None:
    assert runner.invoke(app, ["hydration", "5000", "--dry-run"]).exit_code == 0
    assert runner.invoke(app, ["hydration", "--dry-run", "--", "-5000"]).exit_code == 0
    assert runner.invoke(app, ["hydration", "5001", "--dry-run"]).exit_code == 2
    res = runner.invoke(app, ["hydration", "0", "--dry-run"])
    assert res.exit_code == 2 and "non-zero" in _doc(res)["error"]["message"]


def test_weight_id_ignores_entries_that_are_not_the_one_just_written(monkeypatch) -> None:
    """A household scale syncing into the same day must not lend its samplePk to our
    weigh-in: `log weight delete` would then delete the wrong row."""
    async def safe_call(method_name, *args, **kwargs):
        if method_name == "get_daily_weigh_ins":
            return True, {"dateWeightList": [
                {"samplePk": 9, "weight": 81000, "sourceType": "MANUAL"},        # wrong weight
                {"samplePk": 8, "weight": 70000, "sourceType": "INDEX_SCALE"},   # not manual
                {"samplePk": 7},                                                 # no weight at all
            ]}, None
        return True, {}, None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    monkeypatch.setattr("pace.writes.safe_call", safe_call)
    monkeypatch.setattr("pace.commands.log.safe_call", safe_call)
    doc = _doc(runner.invoke(app, ["weight", "add", "70", "--date", "2026-09-06"]))
    assert doc["ok"] is True and doc["data"]["id"] is None


def test_weight_id_from_the_response_skips_the_lookup(monkeypatch) -> None:
    """If Garmin ever starts returning a samplePk, trust it and spend no extra call."""
    calls = []

    async def safe_call(method_name, *args, **kwargs):
        calls.append(method_name)
        return True, {"samplePk": 4242}, None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    monkeypatch.setattr("pace.writes.safe_call", safe_call)
    monkeypatch.setattr("pace.commands.log.safe_call", safe_call)
    doc = _doc(runner.invoke(app, ["weight", "add", "70", "--date", "2026-09-06"]))
    assert doc["data"]["id"] == 4242 and calls == ["add_weigh_in"]
