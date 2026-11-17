"""`pace schema`: machine-readable manifest of every command."""

from typing import Any

import typer

from pace import __version__
from pace.commands.activity import ACTIVITY_GEAR_FIELDS, LIST_FIELDS
from pace.commands.workout import WORKOUT_LIST_FIELDS
from pace.diary import KINDS, PROFILE_KEYS, REF_PATTERN, WEIGHT_DAYS_DEFAULT
from pace.envelope import EXIT_CODES, HINTS, PaceError, Result, fail, run
from pace.factory import MAX_DAYS
from pace.plan import (
    INTENSITIES, MODIFIED_TOLERANCE, PHASES, SESSION_FIELDS, SETTABLE_STATUSES, STATUSES, WORKOUT_PREFIX,
)
from pace.ratelimit import CONCURRENCY, limiter
from pace.sports import PUSHABLE, SESSION_SPORTS, UNTRACKED
from pace.spec import GROUPS, REGISTRY, EndpointSpec
from pace.workout import DSL_REFERENCE

UNITS = {
    "distance": "m", "elevation": "m", "duration": "s", "speed": "m/s", "heart_rate": "bpm",
    "timestamps": "epoch ms GMT; fields ending in Local are ISO local time",
    "note": "Garmin returns SI regardless of the user's display preferences.",
    "sentinels": "Garmin encodes missing/off-wrist stress and body battery as -1/-2; "
                 "pace drops them from bucketed series",
}

#: What a caller can plan around before it starts a long fan-out.
LIMITS: dict[str, Any] = {
    "calls_per_minute": limiter.per_minute,
    "min_interval_s": int(limiter.min_interval) if float(limiter.min_interval).is_integer()
                      else limiter.min_interval,
    "concurrency": CONCURRENCY,
    "max_days": MAX_DAYS,
    # The per-minute cap, not the interval or the concurrency, is what a cold range costs.
    "cold_cache_seconds_per_day": round(60 / limiter.per_minute),
}

NOTES: list[str] = [
    "empty default_fields means all fields",
    "--from/--to swapped if inverted",
    "--help prints plain text (not JSON)",
    "auto buckets are scored against the whole requested range, not one day",
    "required_flags lists options that must be given; a command without the key has none",
]

SHAPE_FLAGS = ["--fields", "--bucket", "--raw", "--no-cache"]
RANGE_FLAGS = ["--from", "--to", "--days"]

#: The write safety contract, shared by every mutating command.
WRITES: dict[str, Any] = {
    "dry_run": "--dry-run on every write: no network, data.payload = exact Garmin body",
    "result": {"action": "create | delete | update | link | unlink",
               "resource": "workout | weight | hydration | bp | activity_gear | activity_effort",
               "id": "Garmin-assigned id when the response carries one, else null",
               "echo": "the values you sent, for confirmation"},
    "invalidation": "successful writes drop the affected cache keys",
    "idempotency": "Writes are not idempotent: a retry after an ambiguous failure duplicates the "
                   "workout/weigh-in/hydration. Verify with the matching read (workout list, "
                   "log weight list, log bp list) before retrying. gear link/unlink and activity effort set a "
                   "state, so repeating them is harmless.",
    "confirm": "agents must show the planned Garmin write and get an explicit yes before calling; "
               "local_write commands (diary) are outside this rule; the coaching skill decides when to ask about them.",
}

GLOBAL_FLAGS = {
    "--fields": "default | all | comma-separated dotted paths",
    "--bucket": "auto | none | 1m 5m 15m 1h 3h 1d (time series only; auto targets <=120 rows)",
    "--raw": "Garmin JSON verbatim inside the envelope",
    "--no-cache": "bypass the local cache for this call",
    "--verbose": "(root) log fetch/cache events to stderr",
    "--from/--to/--days": "date-keyed commands; --from overrides --days; dates: YYYY-MM-DD | today | yesterday | -Nd",
    "--help": "plain-text usage, not JSON; exit 0",
    "required_flags": "not a flag: a command entry's `required_flags` lists the options that must be given",
}

STATIC_COMMANDS: list[dict[str, Any]] = [
    {"name": "activity list", "help": "Recent activities, or every activity in a date range.", "keyed": "date",
     "args": [], "flags": RANGE_FLAGS + ["--type", "--limit"] + SHAPE_FLAGS,
     "default_fields": list(LIST_FIELDS),
     "series": [], "ttl_s": 600},
    {"name": "activity show", "help": "One activity: summary metrics, plus rpe (1-10) and feel (label) when set.",
     "keyed": "id", "args": ["ACTIVITY_ID"], "flags": SHAPE_FLAGS, "default_fields": [], "series": [], "ttl_s": 86400},
    {"name": "activity gear", "help": "Gear linked to one activity.", "keyed": "id", "args": ["ACTIVITY_ID"],
     "flags": SHAPE_FLAGS, "default_fields": list(ACTIVITY_GEAR_FIELDS), "series": [], "ttl_s": 300},
    {"name": "activity effort", "help": "Set perceived exertion and/or feel on one activity; only the given field(s) are sent.",
     "keyed": "id", "args": ["ACTIVITY_ID"], "flags": ["--rpe", "--feel", "--dry-run"], "default_fields": [],
     "series": [], "ttl_s": 0, "write": True},
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
     "args": [], "flags": ["--from", "--to", "--format", "--out-dir", "--type"], "required_flags": ["--from"],
     "default_fields": [], "series": [], "ttl_s": 0},
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
    {"name": "workout list",
     "help": "Saved workouts. truncated is true when the result length equals --limit; raise --limit to see more.",
     "keyed": "none", "args": [],
     "flags": ["--limit"] + SHAPE_FLAGS, "default_fields": list(WORKOUT_LIST_FIELDS), "series": [], "ttl_s": 600},
    {"name": "workout show", "help": "One saved workout, full structure.", "keyed": "id", "args": ["WORKOUT_ID"],
     "flags": SHAPE_FLAGS, "default_fields": [], "series": [], "ttl_s": 86400},
    {"name": "workout push", "help": "Create a workout on Garmin Connect from the compact DSL (see `pace schema` -> workout_dsl).",
     "keyed": "none", "args": ["FILE"], "flags": ["--dry-run"], "default_fields": [], "series": [], "ttl_s": 0, "write": True},
    {"name": "workout schedule", "help": "Put a saved workout on the Garmin calendar (syncs to the watch).",
     "keyed": "id+date", "args": ["WORKOUT_ID", "DATE"], "flags": ["--dry-run"], "default_fields": [], "series": [],
     "ttl_s": 0, "write": True},
    {"name": "workout delete", "help": "Delete a saved workout (only workouts; activities are never deleted).",
     "keyed": "id", "args": ["WORKOUT_ID"], "flags": ["--dry-run"], "default_fields": [], "series": [], "ttl_s": 0,
     "write": True},
    {"name": "gear link", "help": "Link one gear item to one activity (other linked gear is kept).", "keyed": "id",
     "args": ["ACTIVITY_ID", "GEAR_UUID"], "flags": ["--dry-run"], "default_fields": [], "series": [], "ttl_s": 0,
     "write": True},
    {"name": "gear unlink", "help": "Unlink one gear item from one activity.", "keyed": "id",
     "args": ["ACTIVITY_ID", "GEAR_UUID"], "flags": ["--dry-run"], "default_fields": [], "series": [], "ttl_s": 0,
     "write": True},
    {"name": "log weight add", "help": "Record a weigh-in (plus optional body composition).", "keyed": "none",
     "args": ["KG"], "flags": ["--date", "--fat", "--water", "--muscle", "--bone", "--bmi", "--dry-run"],
     "default_fields": [], "series": [], "ttl_s": 0, "write": True},
    {"name": "log weight list", "help": "Weigh-ins in a date range (one Garmin call).", "keyed": "date", "args": [],
     "flags": RANGE_FLAGS + SHAPE_FLAGS, "default_fields": ["dailyWeightSummaries", "totalAverage"], "series": [], "ttl_s": 600},
    {"name": "log weight delete", "help": "Delete one weigh-in.", "keyed": "id+date", "args": ["SAMPLE_PK"],
     "flags": ["--date", "--dry-run"], "required_flags": ["--date"], "default_fields": [], "series": [],
     "ttl_s": 0, "write": True},
    {"name": "log hydration", "help": "Add water intake for a day.", "keyed": "none", "args": ["ML"],
     "flags": ["--date", "--dry-run"], "default_fields": [], "series": [], "ttl_s": 0, "write": True},
    {"name": "log bp add", "help": "Record a blood pressure reading.", "keyed": "none", "args": ["SYS", "DIA", "PULSE"],
     "flags": ["--date", "--notes", "--dry-run"], "default_fields": [], "series": [], "ttl_s": 0, "write": True},
    {"name": "log bp delete", "help": "Delete one blood pressure reading.", "keyed": "id+date", "args": ["VERSION"],
     "flags": ["--date", "--dry-run"], "required_flags": ["--date"], "default_fields": [], "series": [],
     "ttl_s": 0, "write": True},
    {"name": "log bp list", "help": "Blood pressure readings in a date range (one Garmin call).", "keyed": "date",
     "args": [], "flags": RANGE_FLAGS + SHAPE_FLAGS, "default_fields": ["measurementSummaries"], "series": [], "ttl_s": 600},
    {"name": "diary show", "help": 'Profile, active constraints/injuries, due entries and recent entries (newest first); truncated when --limit cuts entries.', "keyed": "none", "args": [], "flags": ["--since", "--kind", "--ref", "--limit"],
     "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "diary set", "help": 'Set a profile field (see the diary block for keys).', "keyed": "none", "args": ["KEY", "VALUE"], "flags": [],
     "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "diary unset", "help": 'Remove a profile field.', "keyed": "none", "args": ["KEY"], "flags": [],
     "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "diary add", "help": 'Append an entry. TEXT goes after `--` when it may start with `-`.', "keyed": "none", "args": ["TEXT..."], "flags": ["--kind", "--date", "--tag", "--ref", "--due"], "required_flags": ["--kind"],
     "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "diary remove", "help": 'Delete an entry; ids are never reused.', "keyed": "none", "args": ["ID"], "flags": [],
     "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "diary set-due", "help": "Set or clear an entry's due date. Give exactly one of --date or --clear.", "keyed": "none", "args": ["ID"], "flags": ["--date", "--clear"],
     "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "diary resolve", "help": 'Mark a constraint or injury resolved (--date, default today), or --reopen it. Not both.', "keyed": "none", "args": ["ID"], "flags": ["--date", "--reopen"],
     "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "plan new", "help": "Start a new plan; the current one is archived to $PACE_HOME/plans/.", "keyed": "none", "args": [], "flags": ["--name", "--goal", "--event-date"], "required_flags": ["--name"], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "plan show", "help": "Plan meta, all blocks and the sessions in the range (default: 7 days back, 28 ahead).", "keyed": "none", "args": [], "flags": ["--from", "--to", "--past", "--ahead"], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "plan meta", "help": "Set a plan field (name, goal, event_date).", "keyed": "none", "args": ["KEY", "VALUE"], "flags": [], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "plan block add", "help": "Add a block; ids are b1, b2, ...", "keyed": "none", "args": [], "flags": ["--name", "--phase", "--start", "--end", "--focus"], "required_flags": ["--name", "--phase", "--start", "--end"], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "plan block set", "help": "Change a block; only the given fields change.", "keyed": "none", "args": ["ID"], "flags": ["--name", "--phase", "--start", "--end", "--focus"], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "plan block remove", "help": "Delete a block no session uses.", "keyed": "none", "args": ["ID"], "flags": [], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "plan add", "help": "Add sessions from a JSON file or - (one session or a list; all or nothing).", "keyed": "none", "args": ["FILE"], "flags": [], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "plan set", "help": "Change a session; only the given fields change; `none` clears duration, distance, elevation, block, activity and workout.", "keyed": "none", "args": ["ID"], "flags": ["--date", "--title", "--purpose", "--intensity", "--sport", "--block", "--duration", "--distance", "--elevation", "--notes", "--status", "--activity", "--workout"], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "plan remove", "help": "Delete sessions; refused for any with a Garmin workout.", "keyed": "none", "args": ["ID..."], "flags": [], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
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
        "exit_codes": {"0": "ok", "1": "api or internal error",
                       **{str(v): k for k, v in EXIT_CODES.items()}, "130": "aborted by user"},
        "error_codes": list(HINTS),
        "limits": LIMITS,
        "global_flags": GLOBAL_FLAGS,
        "notes": NOTES,
        "groups": {**GROUPS, "activity": "Workouts and activity history; set effort (RPE/feel) on one activity.", "auth": "Authentication.",
                   "cache": "Local cache.", "config": "User configuration.",
                   "workout": "Structured workouts: list, show, push, schedule, delete.",
                   "log": "Health logs written to Garmin Connect.",
                   "diary": "Athlete diary: the coach's durable memory (local file).",
                   "plan": "Training plan: blocks and dated sessions (local file)."},
        "commands": commands,
        "workout_dsl": DSL_REFERENCE,
        "writes": WRITES,
        "diary": {"path": "$PACE_HOME/diary.json", "kinds": KINDS, "profile_keys": PROFILE_KEYS,
                  "ref_pattern": REF_PATTERN, "checkin_weight_days_default": WEIGHT_DAYS_DEFAULT},
        "plan": {"path": "$PACE_HOME/plan.json", "sports": SESSION_SPORTS, "pushable": PUSHABLE,
                 "untracked": UNTRACKED, "intensities": INTENSITIES, "statuses": STATUSES,
                 "settable_statuses": SETTABLE_STATUSES,
                 "derived_statuses": ["done", "modified", "missed", "unconfirmed", "rest"], "phases": PHASES,
                 "session_fields": SESSION_FIELDS, "modified_tolerance": MODIFIED_TOLERANCE,
                 "workout_name_prefix": WORKOUT_PREFIX},
    }


def schema(group: str | None = typer.Argument(None, help="Restrict to one group.")) -> None:
    """Machine-readable manifest of every command, flag, unit and error code."""
    known = set(GROUPS) | {"activity", "auth", "cache", "config", "workout", "log", "diary", "plan"}
    if group is not None and group not in known:
        fail("schema", PaceError("bad_args", f"Unknown group {group!r}. Groups: {', '.join(sorted(known))}."))

    async def work() -> Result:
        return Result(build_schema(group))

    run("schema", {"group": group}, work)
