import json
from datetime import datetime

import click.exceptions
import pytest
from typer.testing import CliRunner

from pace.cli import app, main

runner = CliRunner()


@pytest.fixture(autouse=True)
def _tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")
    monkeypatch.setattr("pace.config.CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr("pace.config.TOKEN_STORE", tmp_path / "tokens")
    monkeypatch.setattr("pace.auth.TOKEN_STORE", tmp_path / "tokens")


def test_usage_error_is_bad_args_envelope(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["pace", "wellness", "sleep", "--nope"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["error"]["code"] == "bad_args" and "--nope" in doc["error"]["message"]
    assert doc["command"] == "wellness sleep"


def test_root_flag_does_not_hide_the_command_name(monkeypatch, capsys) -> None:
    """Flags are filtered before the first two tokens are taken."""
    monkeypatch.setattr("sys.argv", ["pace", "-v", "wellness", "sleep", "--nope"])
    with pytest.raises(SystemExit):
        main()
    assert json.loads(capsys.readouterr().out)["command"] == "wellness sleep"


def test_unknown_command_is_bad_args(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["pace", "frobnicate"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2 and json.loads(capsys.readouterr().out)["error"]["code"] == "bad_args"


def test_command_failure_exit_code_propagates(monkeypatch, capsys) -> None:
    """A `typer.Exit` raised inside a command (via `envelope.fail`) must still
    set the process exit code when `main()` runs the app with
    `standalone_mode=False` -- click returns that exit code instead of
    raising it, so `main()` must propagate the return value explicitly.
    """
    monkeypatch.setattr("sys.argv", ["pace", "wellness", "sleep", "--bucket", "7m"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["error"]["code"] == "bad_args"
    assert doc["command"] == "wellness sleep"


def test_abort_is_envelope_130(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["pace", "cache", "path"])
    monkeypatch.setattr(
        "pace.commands.cache.run", lambda *a, **k: (_ for _ in ()).throw(click.exceptions.Abort())
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 130
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["error"]["code"] == "internal"


def test_auth_status_not_logged_in() -> None:
    doc = json.loads(runner.invoke(app, ["auth", "status"]).stdout)
    assert doc["ok"] and doc["data"]["logged_in"] is False
    assert doc["data"].get("oauth2_expires_at") is None


def test_auth_status_reports_oauth2_expiry(tmp_path) -> None:
    """garth refreshes oauth2 from oauth1, so the expiry is informational -- but an agent
    should be able to see it without reading the token store itself."""
    store = tmp_path / "tokens"
    store.mkdir()
    (store / "oauth2_token.json").write_text(json.dumps({"expires_at": 1788595260, "access_token": "x"}))
    doc = json.loads(runner.invoke(app, ["auth", "status"]).stdout)
    assert doc["data"]["logged_in"] is True
    assert doc["data"]["oauth2_expires_at"].startswith(
        datetime.fromtimestamp(1788595260).astimezone().isoformat()[:19])


def test_auth_status_survives_a_corrupt_oauth2_token(tmp_path) -> None:
    store = tmp_path / "tokens"
    store.mkdir()
    (store / "oauth2_token.json").write_text("not json")
    doc = json.loads(runner.invoke(app, ["auth", "status"]).stdout)
    assert doc["ok"] and doc["data"]["logged_in"] is True and doc["data"]["oauth2_expires_at"] is None


def test_auth_logout_reports_removed(tmp_path) -> None:
    (tmp_path / "tokens").mkdir()
    doc = json.loads(runner.invoke(app, ["auth", "logout"]).stdout)
    assert doc["data"] == {"removed": True}
    doc = json.loads(runner.invoke(app, ["auth", "logout"]).stdout)
    assert doc["data"] == {"removed": False}


def test_cache_commands(tmp_path) -> None:
    doc = json.loads(runner.invoke(app, ["cache", "stats"]).stdout)
    assert doc["data"]["entries"] == 0 and doc["data"]["path"] == str(tmp_path / "cache.db")
    doc = json.loads(runner.invoke(app, ["cache", "clear"]).stdout)
    assert doc["data"] == {"removed": 0}
    doc = json.loads(runner.invoke(app, ["cache", "path"]).stdout)
    assert doc["data"] == {"path": str(tmp_path / "cache.db")}


def test_config_show_and_path(tmp_path) -> None:
    (tmp_path / "config.toml").write_text('[cache_ttl_overrides]\n"wellness.sleep" = 5\n')
    from pace.config import load_config
    load_config.cache_clear()
    doc = json.loads(runner.invoke(app, ["config", "show"]).stdout)
    assert doc["data"] == {"cache_ttl_overrides": {"wellness.sleep": 5}}
    doc = json.loads(runner.invoke(app, ["config", "path"]).stdout)
    assert doc["data"]["path"].endswith("config.toml")


def test_version_flag() -> None:
    res = runner.invoke(app, ["--version"])
    assert res.exit_code == 0 and json.loads(res.stdout)["data"]["version"] == "2.0.0"


def test_three_level_command_name_is_reported(monkeypatch, capsys) -> None:
    """`log weight add` is three levels deep, so a missing KG must still name the command
    the user meant -- not just its first two tokens."""
    monkeypatch.setattr("sys.argv", ["pace", "log", "weight", "add"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["error"]["code"] == "bad_args" and doc["command"] == "log weight add"


def test_command_name_stops_at_the_first_non_command_token(monkeypatch, capsys) -> None:
    """Only tokens that resolve in the click tree count, so a positional value cannot
    be mistaken for a third command level."""
    monkeypatch.setattr("sys.argv", ["pace", "activity", "show", "12345", "--nope"])
    with pytest.raises(SystemExit):
        main()
    assert json.loads(capsys.readouterr().out)["command"] == "activity show"
