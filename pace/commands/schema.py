"""`pace schema`: machine-readable manifest of every command."""

from typing import Any

import typer

from pace import __version__, fit
from pace import zones as zonesmod
from pace.commands.activity import ACTIVITY_GEAR_FIELDS, LIST_FIELDS
from pace.commands.zones import DEFAULT_MAX_CALLS, DEFAULT_WEEKS, F3_RECENT_DAYS, TESTS
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
    "keyed week: week-ranged (--weeks back, --weeks-ahead forward), no --from/--to; rows are whole ISO weeks",
]

SHAPE_FLAGS = ["--fields", "--bucket", "--raw", "--no-cache"]
RANGE_FLAGS = ["--from", "--to", "--days"]

#: The write safety contract, shared by every mutating command.
WRITES: dict[str, Any] = {
    "dry_run": "--dry-run on every write: no network, data.payload = exact Garmin body; exception: zones push --dry-run makes one live read of the watch zones, payload {before, after, body}",
    "result": {"action": "create | delete | update | link | unlink",
               "resource": "workout | weight | hydration | bp | activity_gear | activity_effort | hr_zones",
               "id": "Garmin-assigned id when the response carries one, else null",
               "echo": "the values you sent, for confirmation",
               "batch": "plan push/unpush return {results, done, failed}: results[] = {id, ok, action "
                        "(pushed | skipped | unpushed | noop; null when refused), steps, error, garmin, session}, "
                        "results[].steps[] = {step: delete | upload | schedule, payload, id}; "
                        "ok: false only when every session failed and no step completed"},
    "invalidation": "successful writes drop the affected cache keys",
    "idempotency": "Writes are not idempotent: a retry after an ambiguous failure duplicates the "
                   "workout/weigh-in/hydration. Verify with the matching read (workout list, "
                   "log weight list, log bp list) before retrying. gear link/unlink and activity effort set a "
                   "state, so repeating them is harmless. "
                   "plan push skips unchanged pushed sessions (unless --force) and records every landed id.",
    "confirm": "agents must show the planned Garmin write and get an explicit yes before calling; "
               "local_write commands (diary, plan edits, zones) are outside this rule; the coaching skill decides when to ask about them.",
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
    {"name": "workout calendar",
     "help": "Workouts scheduled on the Garmin calendar in a range (default: today to 14 days ahead); one call per month, at most 12.",
     "keyed": "date", "args": [], "flags": ["--from", "--to", "--past", "--ahead", "--no-cache"], "default_fields": [],
     "series": [], "ttl_s": 300},
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
    {"name": "wellness sleep-range", "help": "Nightly sleep (deep, light, REM, awake seconds), resting HR, average HR, HRV and sleep score for a date range, from one Garmin call per 28 days; fields are null when absent (HRV and score on the current watch). Rows: date, sleep_s, deep_s, light_s, rem_s, awake_s, rhr, avg_hr, hrv, hrv_7d, score.", "keyed": "date", "args": [], "flags": RANGE_FLAGS + ["--no-cache"], "default_fields": [], "series": [], "ttl_s": 3600},
    {"name": "load", "help": "Weekly and daily training load (km, elev_m, hours): actual, planned, projected and a rolling 7-day sum, from Garmin activities and the plan. --export PATH writes dashboard rows (both sports; --sport is bad_args) and returns data = {path, rows}. Default: 16 weeks back through the plan's last session (at most 26 weeks ahead).", "keyed": "week", "args": [], "flags": ["--weeks", "--weeks-ahead", "--sport", "--export", "--no-cache"], "default_fields": [], "series": [], "ttl_s": 600},
    {"name": "chart", "help": "Render a PNG chart (1200 x 675 px) and return data = {path, kind, range, summary}. KIND: volume (weekly bars and rolling 7-day line; week-ranged like `load`: --weeks, --weeks-ahead; --metric km|elev|hours, --sport running|all), plan (sessions by status and unplanned activities; day-ranged: --from, --to, --past, --ahead; default 14 days back, 14 ahead), recovery (resting HR, its 7-day mean, HRV when recorded; day-ranged, default 90 back through today), sleep (stacked sleep stages per night, score when recorded; day-ranged, default 90 back through today), weight (weigh-ins and their 7-day mean; day-ranged, default 90 back through today), intensity (HR-zone time against planned intensity per ISO week; day-ranged, default 56 back and 14 ahead snapped to whole ISO weeks, --sport running|all) or activity ID (pace or speed, heart rate and elevation over distance; positional activity ID, no range flags). Health kinds stop at today even when --to is later. A flag the kind does not use is bad_args. Default path $PACE_HOME/charts/<name>.png; --out must end in .png.", "keyed": "none", "args": ["KIND", "[ID]"], "flags": ["--weeks", "--weeks-ahead", "--from", "--to", "--past", "--ahead", "--metric", "--sport", "--out", "--no-cache"], "default_fields": [], "series": [], "ttl_s": 600},
    {"name": "plan new", "help": "Start a new plan; the current one is archived to $PACE_HOME/plans/.", "keyed": "none", "args": [], "flags": ["--name", "--goal", "--event-date"], "required_flags": ["--name"], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "plan show", "help": "Plan meta, all blocks and the sessions in the range (default: 7 days back, 28 ahead).", "keyed": "none", "args": [], "flags": ["--from", "--to", "--past", "--ahead"], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "plan status", "help": "Planned vs done for a range (default: 7 days back, 7 ahead): sessions matched to Garmin activities (derived status done, modified, missed, unconfirmed, rest), unplanned activities, totals. Effort (RPE, feel) costs one Garmin call per activity, cached one day; --no-effort skips it.", "keyed": "date", "args": [], "flags": ["--from", "--to", "--past", "--ahead", "--no-effort", "--no-cache"], "default_fields": [], "series": [], "ttl_s": 600},
    {"name": "plan meta", "help": "Set a plan field (name, goal, event_date).", "keyed": "none", "args": ["KEY", "VALUE"], "flags": [], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "plan block add", "help": "Add a block; ids are b1, b2, ...", "keyed": "none", "args": [], "flags": ["--name", "--phase", "--start", "--end", "--focus"], "required_flags": ["--name", "--phase", "--start", "--end"], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "plan block set", "help": "Change a block; only the given fields change.", "keyed": "none", "args": ["ID"], "flags": ["--name", "--phase", "--start", "--end", "--focus"], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "plan block remove", "help": "Delete a block no session uses.", "keyed": "none", "args": ["ID"], "flags": [], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "plan add", "help": "Add sessions from a JSON file or - (one session or a list; all or nothing).", "keyed": "none", "args": ["FILE"], "flags": [], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "plan set", "help": "Change a session; only the given fields change; `none` clears duration, distance, elevation, block, activity and workout.", "keyed": "none", "args": ["ID"], "flags": ["--date", "--title", "--purpose", "--intensity", "--sport", "--block", "--duration", "--distance", "--elevation", "--notes", "--status", "--activity", "--workout"], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "plan remove", "help": "Delete sessions; refused for any with a Garmin workout.", "keyed": "none", "args": ["ID..."], "flags": [], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "plan push", "help": "Upload and schedule sessions on Garmin (workout named 'pace: <name>'); unchanged pushed sessions are skipped unless --force; past sessions are refused.", "keyed": "none", "args": ["ID..."], "flags": ["--force", "--dry-run"], "default_fields": [], "series": [], "ttl_s": 0, "write": True},
    {"name": "plan unpush", "help": "Delete the sessions' workouts from Garmin (404 counts as done); the sessions stay, planned; refused when both the session date and the date its workout was pushed to are past.", "keyed": "none", "args": ["ID..."], "flags": ["--dry-run"], "default_fields": [], "series": [], "ttl_s": 0, "write": True},
    {"name": "zones show", "help": "Current LT1 and LT2 records with provenance, expired (per threshold), derived zones (rule lt-5z-v1), all records (current_for), candidates, due (estimate, field_test per threshold, reasons), hr_max, the alpha1 (h4) status, the watch's HR zones (garmin; null when unreadable) and in_sync. Two Garmin reads: the activity list for the due rule and the watch zones (cached).", "keyed": "none", "args": [], "flags": ["--no-cache"], "default_fields": [], "series": [], "ttl_s": 600},
    {"name": "zones estimate", "help": "Estimate LT1 and LT2 from recent runs (critical speed, HR-speed regression, alpha1, HR drift). Hard cap --max-calls on Garmin calls: the rest is deferred and the estimate logged as partial; a cold run takes up to about 4 minutes (use a 10-minute timeout), near-instant from cache. Always appends a summary to the local estimates log and raises hr_max; --save also stores a full record (source estimate); records never become current without `zones accept`. H4 runs report dropped_coupling and dropped_step (alpha1 windows dropped for coupling or step interference); an H4 marked unreliable by this same estimate keeps its values with flags [alpha1_unreliable] and is not combined into lt1.", "keyed": "none", "args": [], "flags": ["--weeks", "--max-calls", "--include-wrist", "--retry-h4", "--save", "--no-cache"], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "zones analyze", "help": "Analyze a field-test run: drift (F1, LT1 HR and pace), time_trial (F2, LT2) or ramp (F3, alpha1 steps; needs the FIT file, downloaded once). Needs laps for F2 and F3. Always raises hr_max (strap runs) and, for ramp, records the alpha1 gate outcome (ramp steps carry step_share; a step dominated by step interference is dropped, counted in dropped_step); --save also stores a field-test record, refused (bad_args, the verdict and next test in the message) for an F1 that is not 'at LT1' with even pace, a rejected F2 or an unusable or unbracketed F3.", "keyed": "id", "args": ["ACTIVITY_ID"], "flags": ["--test", "--treadmill", "--save", "--no-cache"], "required_flags": ["--test"], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "zones test-workout", "help": "Workout DSL for a field test (drift, time_trial, ramp), targets from the current LT1 record or the newest estimate; add it to the plan. No Garmin call.", "keyed": "none", "args": ["KIND"], "flags": [], "default_fields": [], "series": [], "ttl_s": 0},
    {"name": "zones add", "help": "Add a threshold record (lab, field_test or estimate); paces are m:ss per km. A lab record becomes current for each threshold it has unless that threshold's current record is newer. Refused when LT1 HR is not below LT2 HR.", "keyed": "none", "args": [], "flags": ["--source", "--date", "--lt1-hr", "--lt1-pace", "--lt2-hr", "--lt2-pace", "--max-hr", "--rest-hr", "--method", "--confidence", "--activity", "--notes"], "required_flags": ["--source"], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "zones accept", "help": "Make a record current for every threshold it has, or only --only lt1|lt2; refused when the resulting LT1 HR is not below LT2 HR.", "keyed": "none", "args": ["ID"], "flags": ["--only"], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "zones remove", "help": "Delete a record; refused while it is current for a threshold unless --force (then that threshold has no current record).", "keyed": "none", "args": ["ID"], "flags": ["--force"], "default_fields": [], "series": [], "ttl_s": 0, "local_write": True},
    {"name": "zones push", "help": "Write the current zones to the watch: the five floors (rule lt-5z-v1), and max HR when pace knows it (else Garmin's is kept and zone5Floor must be below it), into every DEFAULT and RUNNING entry of Garmin's HR zones; other entries and fields are kept. Built from one live read of the watch zones (never the cache), then one write; drops the cached watch zones. Refused (bad_args) without a current LT1 and LT2 with HR or with invalid floors; endpoint_unavailable without a DEFAULT or RUNNING entry. --dry-run makes the read, shows {before, after, body} and writes nothing.", "keyed": "none", "args": [], "flags": ["--dry-run"], "default_fields": [], "series": [], "ttl_s": 0, "write": True},
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
                   "plan": "Training plan: blocks and dated sessions (local file); push sessions to Garmin.",
                   "load": "Weekly and daily training load.",
                   "chart": "PNG charts: volume, plan, recovery, sleep, weight, intensity, activity.",
                   "zones": "Lactate thresholds and zones: estimate from history, analyze field tests, record, accept (local file); push to the watch."},
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
        "zones": {"path": "$PACE_HOME/zones.json", "rule": zonesmod.ZONE_RULE, "sources": list(zonesmod.SOURCES),
                  "confidences": list(zonesmod.CONFIDENCES), "tests": list(TESTS),
                  "lab_valid_days": zonesmod.LAB_VALID_DAYS, "weeks_default": DEFAULT_WEEKS,
                  "due": {"estimate_every_days": zonesmod.ESTIMATE_EVERY_DAYS,
                          "field_test_every_days": zonesmod.FIELD_TEST_EVERY_DAYS,
                          "break_days": zonesmod.BREAK_DAYS, "retrain_days": zonesmod.RETRAIN_DAYS,
                          "hr_drift_bpm": zonesmod.HR_DRIFT_BPM, "pace_drift_frac": zonesmod.PACE_DRIFT_FRAC,
                          "hard_training_effect": zonesmod.HARD_EFFORT_TE,
                          "f3_recent_days": F3_RECENT_DAYS},
                  "max_calls": {"default": DEFAULT_MAX_CALLS,
                                "rule": "hard cap on all Garmin calls of one estimate, extension included; each "
                                        "picked run costs get_activity + get_activity_details, a FIT download one "
                                        "call; cache hits are free; at the cap the rest is deferred and the "
                                        "estimate is logged partial (the due rule skips partial entries)"},
                  "no_rr_reasons": list(fit.NO_RR_REASONS)},
    }


def schema(group: str | None = typer.Argument(None, help="Restrict to one group.")) -> None:
    """Machine-readable manifest of every command, flag, unit and error code."""
    known = set(GROUPS) | {"activity", "auth", "cache", "config", "workout", "log", "diary", "plan", "load", "chart", "zones"}
    if group is not None and group not in known:
        fail("schema", PaceError("bad_args", f"Unknown group {group!r}. Groups: {', '.join(sorted(known))}."))

    async def work() -> Result:
        return Result(build_schema(group))

    run("schema", {"group": group}, work)
