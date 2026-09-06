import json

import pytest
from typer.testing import CliRunner

from pace.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    # PACE_HOME too: Cache() calls ensure_home(), which would otherwise mkdir the
    # developer's real ~/.pace during a test run.
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")


@pytest.fixture
def fake_api(monkeypatch):
    calls = []

    async def safe_call(method, *args, **kwargs):
        calls.append((method, args))
        return True, {"method": method, "unitSystem": "metric", "fullName": "A B"}, None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    return calls


@pytest.mark.parametrize("argv", [
    ["wellness", "sleep", "--to", "2026-01-01"],
    ["training", "readiness", "--to", "2026-01-01"],
    ["device", "list"],
    ["goal", "personal-records"],
    ["profile", "settings"],
])
def test_groups_are_wired(fake_api, argv) -> None:
    res = runner.invoke(app, argv)
    assert res.exit_code == 0, res.stdout
    doc = json.loads(res.stdout)
    assert doc["ok"] and doc["command"] == " ".join(argv[:2])


def test_profile_show_is_composite(fake_api) -> None:
    res = runner.invoke(app, ["profile", "show"])
    doc = json.loads(res.stdout)
    assert doc["ok"] and set(doc["data"]) == {"fullName", "profile", "unitSystem"}
    assert {m for m, _ in fake_api} == {"get_full_name", "get_user_profile", "get_unit_system"}


def test_removed_commands_gone() -> None:
    for argv in (["wellness", "trend"], ["wellness", "today"], ["profile", "unit-system"], ["today"], ["browse"]):
        assert runner.invoke(app, argv).exit_code == 2
