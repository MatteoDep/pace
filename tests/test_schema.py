import json

import click
from typer.main import get_command
from typer.testing import CliRunner

from pace.cli import app
from pace.commands.schema import build_schema

runner = CliRunner()

EXEMPT = {"schema", "auth login"}  # login is human-only; schema describes itself


def _walk(cmd: click.Command, prefix: str = "") -> set[str]:
    if isinstance(cmd, click.Group):
        out = set()
        for name, sub in cmd.commands.items():
            out |= _walk(sub, f"{prefix}{name} ".strip() + " ")
        return out
    return {prefix.strip()}


def test_every_cli_command_is_in_schema() -> None:
    cli_cmds = _walk(get_command(app)) - EXEMPT
    schema_cmds = {c["name"] for c in build_schema(None)["commands"]}
    assert cli_cmds <= schema_cmds, cli_cmds - schema_cmds
    assert schema_cmds <= cli_cmds | EXEMPT, schema_cmds - cli_cmds


def test_schema_command_output_and_group_filter() -> None:
    res = runner.invoke(app, ["schema"])
    doc = json.loads(res.stdout)
    assert res.exit_code == 0 and doc["ok"] and doc["data"]["version"] == "2.0.0"
    assert doc["data"]["units"]["distance"] == "m"
    assert "bad_args" in doc["data"]["error_codes"]
    res = runner.invoke(app, ["schema", "wellness"])
    names = [c["name"] for c in json.loads(res.stdout)["data"]["commands"]]
    assert names and all(n.startswith("wellness ") for n in names)


def test_dated_entries_expose_range_flags() -> None:
    sleep = next(c for c in build_schema(None)["commands"] if c["name"] == "wellness sleep")
    assert sleep["keyed"] == "date" and {"--from", "--to", "--days"} <= set(sleep["flags"])
    solar = next(c for c in build_schema(None)["commands"] if c["name"] == "device solar")
    assert solar["keyed"] == "id+date" and solar["args"] == ["DEVICE_ID"]


def test_unknown_group_is_bad_args() -> None:
    res = runner.invoke(app, ["schema", "nope"])
    assert res.exit_code == 2 and json.loads(res.stdout)["error"]["code"] == "bad_args"
