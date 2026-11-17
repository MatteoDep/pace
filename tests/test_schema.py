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


def test_limits_exit_codes_and_notes_are_declared() -> None:
    """An agent plans a fan-out from these, so they must track the code, not literals."""
    from pace.factory import MAX_DAYS
    from pace.ratelimit import CONCURRENCY, limiter

    doc = build_schema(None)
    assert doc["limits"] == {"calls_per_minute": limiter.per_minute, "min_interval_s": 2,
                             "concurrency": CONCURRENCY, "max_days": MAX_DAYS,
                             "cold_cache_seconds_per_day": 6}
    assert doc["exit_codes"]["130"] == "aborted by user"
    assert "--help" in doc["global_flags"]
    assert any("default_fields" in n for n in doc["notes"])
    assert "-1/-2" in doc["units"]["sentinels"]


def test_dated_entries_expose_range_flags() -> None:
    sleep = next(c for c in build_schema(None)["commands"] if c["name"] == "wellness sleep")
    assert sleep["keyed"] == "date" and {"--from", "--to", "--days"} <= set(sleep["flags"])
    solar = next(c for c in build_schema(None)["commands"] if c["name"] == "device solar")
    assert solar["keyed"] == "id+date" and solar["args"] == ["DEVICE_ID"]


def test_unknown_group_is_bad_args() -> None:
    res = runner.invoke(app, ["schema", "nope"])
    assert res.exit_code == 2 and json.loads(res.stdout)["error"]["code"] == "bad_args"


def test_writes_and_dsl_in_schema() -> None:
    data = build_schema(None)
    assert data["workout_dsl"]["example"]["name"]
    assert set(data["writes"]) == {"dry_run", "result", "invalidation", "idempotency", "confirm"}
    assert "not idempotent" in data["writes"]["idempotency"]
    assert {"update", "link", "unlink"} <= set(data["writes"]["result"]["action"].split(" | "))
    assert {"activity_gear", "activity_effort"} <= set(data["writes"]["result"]["resource"].split(" | "))
    assert "link" in data["writes"]["idempotency"]
    assert "link" in data["groups"]["gear"] and "effort" in data["groups"]["activity"]
    writes = {c["name"] for c in data["commands"] if c.get("write")}
    assert writes == {"workout push", "workout schedule", "workout delete", "log weight add", "log weight delete",
                      "log hydration", "log bp add", "log bp delete", "activity effort",
                      "gear link", "gear unlink"}
    for c in data["commands"]:
        if c.get("write"):
            assert "--dry-run" in c["flags"], c["name"]
    assert {"workout", "log"} <= set(data["groups"])


def test_required_flags_are_declared() -> None:
    """A required option is indistinguishable from an optional one in a flat flag list,
    so an agent needs it spelled out."""
    entries = {c["name"]: c for c in build_schema(None)["commands"]}
    assert entries["log weight delete"]["required_flags"] == ["--date"]
    assert entries["log bp delete"]["required_flags"] == ["--date"]
    assert entries["activity export-range"]["required_flags"] == ["--from"]
    for name, entry in entries.items():
        for flag in entry.get("required_flags", []):
            assert flag in entry["flags"], name
    assert any("required_flags" in n for n in build_schema(None)["notes"])


def test_retry_hints_warn_about_non_idempotent_writes() -> None:
    """`network` and `rate_limited` both say "retry once", which is only safe for reads."""
    from pace.envelope import HINTS
    for code in ("network", "rate_limited"):
        assert "verify with the matching read" in HINTS[code], code


def test_diary_in_schema() -> None:
    data = build_schema(None)
    entries = {c["name"]: c for c in data["commands"]}
    names = {"diary show", "diary set", "diary unset", "diary add", "diary remove", "diary set-due", "diary resolve"}
    assert names <= set(entries)
    for name in names:
        assert "write" not in entries[name], name
        assert bool(entries[name].get("local_write")) is (name != "diary show"), name
    assert entries["diary add"]["required_flags"] == ["--kind"]
    assert "Garmin" in data["writes"]["confirm"]
    assert data["diary"]["kinds"][-1] == "decision"
    assert "dashboard.url" in data["diary"]["profile_keys"]
    assert "diary" in data["groups"]
    res = runner.invoke(app, ["schema", "diary"])
    assert res.exit_code == 0
    assert {c["name"].split(" ")[0] for c in json.loads(res.stdout)["data"]["commands"]} == {"diary"}


def test_plan_in_schema() -> None:
    from pace.plan import (INTENSITIES, MODIFIED_TOLERANCE, PHASES, SESSION_FIELDS, SETTABLE_STATUSES, STATUSES,
                           WORKOUT_PREFIX)
    from pace.sports import PUSHABLE, SESSION_SPORTS, UNTRACKED
    data = build_schema(None)
    entries = {c["name"]: c for c in data["commands"]}
    names = {"plan new", "plan show", "plan meta", "plan block add", "plan block set", "plan block remove",
             "plan add", "plan set", "plan remove"}
    assert names <= set(entries)
    for name in names:
        assert "write" not in entries[name], name
        assert bool(entries[name].get("local_write")) is (name != "plan show"), name
    assert entries["plan new"]["required_flags"] == ["--name"]
    assert entries["plan block add"]["required_flags"] == ["--name", "--phase", "--start", "--end"]
    for entry in entries.values():
        for flag in entry.get("required_flags", []):
            assert flag in entry["flags"], entry["name"]
    assert "plan" in data["groups"]
    plan = data["plan"]
    assert plan["path"] == "$PACE_HOME/plan.json"
    assert plan["sports"] == SESSION_SPORTS and plan["pushable"] == PUSHABLE and plan["untracked"] == UNTRACKED
    assert plan["intensities"] == INTENSITIES and plan["statuses"] == STATUSES
    assert plan["settable_statuses"] == SETTABLE_STATUSES
    assert plan["derived_statuses"] == ["done", "modified", "missed", "unconfirmed", "rest"]
    assert plan["phases"] == PHASES and plan["session_fields"] == SESSION_FIELDS
    assert plan["modified_tolerance"] == MODIFIED_TOLERANCE and plan["workout_name_prefix"] == WORKOUT_PREFIX
    res = runner.invoke(app, ["schema", "plan"])
    assert res.exit_code == 0
    assert {c["name"].split(" ")[0] for c in json.loads(res.stdout)["data"]["commands"]} == {"plan"}
