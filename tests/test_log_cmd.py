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
    state = {"calls": [], "resp": {"samplePk": 123, "version": 9}}

    async def safe_call(method_name, *args, **kwargs):
        state["calls"].append((method_name, args, kwargs))
        if method_name == "get_daily_weigh_ins":
            # `weight add`'s post-write samplePk lookup: derive it from state["resp"]
            # so existing tests that assert an id keep working unchanged.
            pk = state["resp"].get("samplePk") if isinstance(state["resp"], dict) else None
            return True, ({"dateWeightList": [{"samplePk": pk}]} if pk is not None else {}), None
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
            return True, {"dateWeightList": [{"samplePk": 1}, {"samplePk": 5}]}, None
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
