"""`pace schema`: machine-readable manifest of every command."""

from typing import Any

import typer

from pace import __version__
from pace.commands.activity import LIST_FIELDS
from pace.envelope import EXIT_CODES, HINTS, PaceError, Result, fail, run
from pace.spec import GROUPS, REGISTRY, EndpointSpec

UNITS = {
    "distance": "m", "elevation": "m", "duration": "s", "speed": "m/s", "heart_rate": "bpm",
    "timestamps": "epoch ms GMT; fields ending in Local are ISO local time",
    "note": "Garmin returns SI regardless of the user's display preferences.",
}

SHAPE_FLAGS = ["--fields", "--bucket", "--raw", "--no-cache"]
RANGE_FLAGS = ["--from", "--to", "--days"]

GLOBAL_FLAGS = {
    "--fields": "default | all | comma-separated dotted paths",
    "--bucket": "auto | none | 1m 5m 15m 1h 3h 1d (time series only; auto targets <=120 rows)",
    "--raw": "Garmin JSON verbatim inside the envelope",
    "--no-cache": "bypass the local cache for this call",
    "--verbose": "(root) log fetch/cache events to stderr",
    "--from/--to/--days": "date-keyed commands; --from overrides --days; dates: YYYY-MM-DD | today | yesterday | -Nd",
}

STATIC_COMMANDS: list[dict[str, Any]] = [
    {"name": "activity list", "help": "Recent activities, or every activity in a date range.", "keyed": "date",
     "args": [], "flags": RANGE_FLAGS + ["--type", "--limit"] + SHAPE_FLAGS,
     "default_fields": list(LIST_FIELDS),
     "series": [], "ttl_s": 600},
    {"name": "activity show", "help": "One activity: summary metrics.", "keyed": "id", "args": ["ACTIVITY_ID"],
     "flags": SHAPE_FLAGS, "default_fields": [], "series": [], "ttl_s": 86400},
    {"name": "activity splits", "help": "Laps / splits of one activity.", "keyed": "id", "args": ["ACTIVITY_ID"],
     "flags": SHAPE_FLAGS, "default_fields": [], "series": [], "ttl_s": 86400},
    {"name": "activity details", "help": "Per-sample metrics as one bucketed table (cols: t, hr, speed_mps, dist_m, elev_m, cadence, power_w).",
     "keyed": "id", "args": ["ACTIVITY_ID"], "flags": ["--bucket", "--raw", "--no-cache"], "default_fields": [],
     "series": ["table"], "ttl_s": 86400},
    {"name": "activity weather", "help": "Weather during one activity.", "keyed": "id", "args": ["ACTIVITY_ID"],
     "flags": SHAPE_FLAGS, "default_fields": [], "series": [], "ttl_s": 86400},
    {"name": "activity last", "help": "Most recent activity.", "keyed": "none", "args": [], "flags": SHAPE_FLAGS,
     "default_fields": [], "series": [], "ttl_s": 600},
    {"name": "activity export", "help": "Download one activity file; data.files lists written paths.", "keyed": "id",
     "args": ["ACTIVITY_ID"], "flags": ["--format", "--out"], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "activity export-range", "help": "Download every activity in a range into --out-dir.", "keyed": "date",
     "args": [], "flags": ["--from", "--to", "--format", "--out-dir", "--type"], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "profile show", "help": "Name, profile and unit system.", "keyed": "none", "args": [], "flags": SHAPE_FLAGS,
     "default_fields": [], "series": [], "ttl_s": 604800},
    {"name": "auth status", "help": "Whether tokens exist. If false, the USER must run `pace auth login`.", "keyed": "none",
     "args": [], "flags": [], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "auth logout", "help": "Delete cached tokens.", "keyed": "none", "args": [], "flags": [], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "cache stats", "help": "Cache entry count and size.", "keyed": "none", "args": [], "flags": [], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "cache clear", "help": "Delete all cached responses.", "keyed": "none", "args": [], "flags": [], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "cache path", "help": "Cache database path.", "keyed": "none", "args": [], "flags": [], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "config show", "help": "Effective configuration.", "keyed": "none", "args": [], "flags": [], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "config path", "help": "Config file path.", "keyed": "none", "args": [], "flags": [], "default_fields": [], "series": [], "ttl_s": 0},
]


def _keyed(spec: EndpointSpec) -> str:
    if spec.id_arg and spec.dated:
        return "id+date"
    if spec.id_arg:
        return "id"
    if spec.dated:
        return "date"
    return "none"


def _entry(spec: EndpointSpec) -> dict[str, Any]:
    return {
        "name": spec.command, "help": spec.help, "keyed": _keyed(spec),
        "args": [spec.id_arg] if spec.id_arg else [],
        "flags": (RANGE_FLAGS if spec.dated else []) + SHAPE_FLAGS,
        "default_fields": list(spec.default_fields),
        "series": [s.path for s in spec.series],
        "ttl_s": spec.ttl,
    }


def build_schema(group: str | None) -> dict[str, Any]:
    commands = [_entry(s) for s in REGISTRY] + STATIC_COMMANDS
    if group is not None:
        commands = [c for c in commands if c["name"].split(" ")[0] == group]
    commands.sort(key=lambda c: c["name"])
    return {
        "version": __version__,
        "units": UNITS,
        "exit_codes": {"0": "ok", "1": "api or internal error", **{str(v): k for k, v in EXIT_CODES.items()}},
        "error_codes": list(HINTS),
        "global_flags": GLOBAL_FLAGS,
        "groups": {**GROUPS, "activity": "Workouts and activity history.", "auth": "Authentication.",
                   "cache": "Local cache.", "config": "User configuration."},
        "commands": commands,
    }


def schema(group: str | None = typer.Argument(None, help="Restrict to one group.")) -> None:
    """Machine-readable manifest of every command, flag, unit and error code."""
    known = set(GROUPS) | {"activity", "auth", "cache", "config"}
    if group is not None and group not in known:
        fail("schema", PaceError("bad_args", f"Unknown group {group!r}. Groups: {', '.join(sorted(known))}."))

    async def work() -> Result:
        return Result(build_schema(group))

    run("schema", {"group": group}, work)
