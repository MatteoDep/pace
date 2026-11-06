# Pace 2.0 -- LLM-facing CLI design

Date: 2026-09-04
Status: approved in brainstorming, pending spec review

## Goal

Retarget `pace` from a human-facing Garmin Connect CLI into a CLI whose only consumer is an LLM (Claude Code via the Bash tool).
The LLM uses the user's Garmin data to coach.
A Claude Code skill teaches the LLM how to call `pace`, how to read its output, how to present data to the user uniformly, and (minimally, v0) how to coach.

Out of scope for this pass: derived training metrics (load ratios, baselines, CTL/ATL).
Those come later, once the coaching skill has been exercised.
Out of scope permanently: MCP server.
Mutations of Garmin data are in scope for a restricted set (structured workouts, health logs); see section 7.

## Decisions taken

| Topic | Decision |
|---|---|
| Interface | CLI only, plus one agent-facing skill. |
| Human UX | Stripped entirely (TUI, tables, sparklines, dashboard, watch). |
| Analytics | Raw data only for now. |
| Output shape | Pruned JSON, curated default fields, time series bucketed with configurable resolution. |
| Envelope | Always, on stdout, for success and error. |
| Units | Static, documented once in `pace schema` and the skill, not per response. |
| Ranges | Every date-keyed command takes `--from/--to/--days`. |
| Discovery | `pace schema` manifest + condensed copy in the skill. |
| Exports | Kept (`activity export`, `export-range`). |
| Mutations | Workouts (push/schedule/delete) and health logs (weight, hydration, blood pressure) now; activity edit/delete never. |
| Diary | CLI-managed athlete diary (`pace diary`) in `$PACE_HOME/diary.json`, read at session start, written after coaching decisions. |
| Skill scope | Two sections: data access (full) and coaching (minimal v0). |
| Skill home | Repo `.claude/skills/pace/`, symlinked into `~/.claude/skills/pace`. |
| Architecture | Spec registry drives simple commands and the schema; irregular commands hand-written. |

## 1. Layout

### Deleted

- `pace/tui/`, `pace/render/`, `pace/progress.py`.
- `pace/commands/today.py`, `watch.py`, `browse.py`, `_trend_metrics.py`; the `wellness trend` command.
- `pace/models/` (RawData/ListModel/TrendSeries no longer needed; commands return plain dicts/lists).
- `docs/man/`, `docs/STAGE1..4.md`, `PROPOSAL.md`, `main.py`, `garmin_api.py`.
- `tests/test_colors.py`, `test_chart.py`, `test_flatten.py`.
- Dependencies: `rich`, `textual`, `readchar`, `aionode`, `click-man`.
  `typer`, `garminconnect`, `pydantic` stay.

### Moved

- `demo.py` -> `scripts/garmin_api_reference.py` (reference for garminconnect method names and payload shapes).

### Config

`~/.pace/config.toml` keeps only `cache_ttl_overrides`.
`default_format` and `dashboard_panels` are removed.

### New / rewritten

```
pace/
  cli.py          root Typer app; wires groups; global --verbose
  spec.py         SeriesSpec, EndpointSpec, REGISTRY
  factory.py      EndpointSpec -> Typer command (range flags, fan-out, shaping, envelope)
  shape.py        prune, select_fields, bucket_series, auto bucket ladder
  envelope.py     ok()/error() builders, exit codes, emit()
  workout.py      compact workout DSL -> garminconnect typed workout model
  diary.py        diary.json load/save, entry ids, profile keys
  dates.py        parse_date, date_range, resolve_range(from, to, days)
  api.py          unchanged except error -> (code, http, message) classification
  cache.py        get_or_fetch returns (value, hit, age_s)
  commands/
    activity.py   list, show, splits, details, weather, last, export, export-range
    auth.py       status, login, logout
    cache.py      stats, clear, path
    config.py     show, path
    schema.py     schema [GROUP]
    workout.py    list, show, push, schedule, delete
    log.py        weight (add/list/delete), hydration, bp (add/list)
    diary.py      show, set, add, remove
    wellness.py, training.py, device.py, gear.py, goal.py, profile.py
                  each: build Typer group from REGISTRY entries for that group
.claude/skills/pace/SKILL.md
docs/CONTRACT.md
docs/PLUGINS.md   updated
tests/fixtures/*.json
```

## 2. Output contract

Source of truth: `docs/CONTRACT.md` (this section is duplicated there in full).

### Stream discipline

- stdout: exactly one compact JSON document (no indent) followed by a newline.
- stderr: empty unless `--verbose`, which logs fetch/cache events one per line.
- No progress bars, no colour, no ANSI.

### Success envelope

```json
{"ok": true,
 "command": "wellness sleep",
 "args": {"from": "2026-08-20", "to": "2026-09-02", "bucket": "auto", "fields": "default"},
 "cache": {"hit": "partial", "oldest_age_s": 812},
 "shape": {"bucket_used": "15m", "reduced_series": ["sleepHeartRate"], "fields": "default"},
 "truncated": false,
 "data": [{"date": "2026-08-20", "...": "..."}]}
```

- `args`: the effective arguments after defaults and date resolution.
  Dates are absolute ISO.
- `cache.hit`: `true` (all from cache), `false` (all fetched), `"partial"`.
  `oldest_age_s`: age of the oldest cache entry used; `0` when nothing came from cache.
- `shape.bucket_used`: the bucket actually applied (`"none"` when no series reduced).
  `reduced_series`: dotted paths that were bucketed.
  `fields`: `"default"`, `"all"`, or the explicit list.
- `truncated`: `true` when a `--limit` cut the result.
- `data`: command payload.
  Date-keyed commands always emit a list of `{"date": "YYYY-MM-DD", ...}` in ascending date order, even for a single day.

### Error envelope

```json
{"ok": false,
 "command": "wellness sleep",
 "error": {"code": "auth_required", "http": 401,
           "message": "Auth required (401) -- re-authenticate",
           "hint": "Ask the user to run `pace auth login` (interactive MFA)."}}
```

| code | exit | when |
|---|---|---|
| `bad_args` | 2 | Typer usage error, invalid date, unknown field/bucket |
| `auth_required` | 3 | no tokens, 401, garminconnect auth error |
| `rate_limited` | 4 | 429 or garminconnect too-many-requests |
| `not_found` | 1 | 404 |
| `endpoint_unavailable` | 1 | 400, 403 (feature not enabled for account) |
| `network` | 1 | connection failure, 5xx |
| `internal` | 1 | anything else |

Typer's own usage errors are intercepted and re-emitted as a `bad_args` envelope so stdout stays JSON.

### Partial failure in a range

If some days fail and some succeed, the failed days appear in `data` as `{"date": ..., "error": {"code": ..., "message": ...}}`, the envelope is `ok: true`, exit 0.
If every day fails, the envelope is `ok: false` with the first error.

### Shaping flags (every data command)

- `--fields default|all|<a,b.c,d>`.
  `default` applies the spec's curated dotted paths.
  `all` keeps every field.
  Explicit list: dotted paths, top-level or nested; a missing path is silently absent.
- `--bucket auto|none|1m|5m|15m|1h|3h|1d`.
  Applies to timestamped series declared in the spec.
  `auto` picks the smallest bucket on the ladder that yields at most 120 rows per series over the requested range (per day for date-keyed commands; per activity for `activity details`).
  `none` disables bucketing.
- `--raw`: Garmin JSON verbatim inside `data`, no pruning, no field selection, no bucketing.
  `shape` reports `{"raw": true}`.
- `--no-cache`: bypass the cache for this call (still writes the result).

Pruning (always on unless `--raw`): recursively drop `null`, `""`, `[]`, `{}`.

### Bucketed series format

```json
{"cols": ["t", "avg", "min", "max", "n"],
 "rows": [["2026-09-02T07:15", 58, 55, 63, 15], ["2026-09-02T07:30", 61, 57, 70, 15]]}
```

- `t`: bucket start, local time, ISO minute precision, no timezone suffix.
  Local offset derived from Garmin's paired `*Local` / `*GMT` fields when present, else system local.
- `avg` rounded to 1 decimal; `min`/`max` as source values; `n` = samples in bucket.
- Buckets with no samples are omitted.
- Segment series (e.g. `sleepLevels` with start/end/level) are not bucketed; they pass through pruning only.

### Units (static)

Garmin Connect returns SI regardless of user display preferences: distance `m`, duration `s`, speed `m/s`, elevation `m`, heart rate `bpm`, timestamps epoch ms GMT, `*Local` fields as ISO local strings.
Documented in `pace schema` and the skill; never repeated per response.

## 3. Spec registry

```python
@dataclass(frozen=True)
class SeriesSpec:
    path: str                                  # dotted path to the list
    kind: Literal["pairs", "records"]          # [[ts, v], ...] | [{t_key: ..., v_key: ...}, ...]
    t_key: str = "0"                           # index (pairs) or key (records)
    v_key: str = "1"

@dataclass(frozen=True)
class EndpointSpec:
    group: str
    name: str
    help: str
    method: str                                # garminconnect method name
    keyed: Literal["none", "date", "id"]
    ttl: int                                   # seconds, for "today" / un-keyed
    default_fields: tuple[str, ...] = ()       # () means all
    series: tuple[SeriesSpec, ...] = ()
    unwrap: str | None = None                  # "first" to take [0] of a list response
```

`REGISTRY: tuple[EndpointSpec, ...]` lives in `spec.py`.
Each group module calls `factory.build_group(group_name)` to get its Typer app.

### factory.build(spec)

- `keyed == "date"`: adds `--from/-F`, `--to/-T`, `--days/-n`.
  Defaults: today, one day.
  `--days N` means N days ending at `--to` (inclusive).
  `--from` overrides `--days`.
  Accepts `today`, `yesterday`, `-Nd`, `YYYY-MM-DD` (existing `parse_date`).
  Fan-out: one `cached_call` per day under `asyncio.gather`, bounded by the existing limiter and concurrency semaphore.
- `keyed == "id"`: positional `ID` argument (int).
- `keyed == "none"`: no positional.
- Always adds `--fields`, `--bucket`, `--raw`, `--no-cache`.
- Cache TTL per day: today -> `spec.ttl`; yesterday -> 86400; older -> 604800.
  `cache_ttl_overrides` in config keyed by `"group.name"` still override the today TTL.
- Assembles the envelope from per-call `(value, hit, age_s)` and the shaping report.

### Registered endpoints

| group | name | method | keyed | notes |
|---|---|---|---|---|
| wellness | summary | get_user_summary | date | renamed from `today` |
| wellness | sleep | get_sleep_data | date | series: sleepHeartRate, sleepMovement, respiration; segments: sleepLevels |
| wellness | hr | get_heart_rates | date | series: heartRateValues (pairs) |
| wellness | stress | get_stress_data | date | series: stressValuesArray, bodyBatteryValuesArray (pairs) |
| wellness | body-battery | get_body_battery | date | unwrap first; series: bodyBatteryValuesArray |
| wellness | hrv | get_hrv_data | date | series: hrvReadings (records) |
| wellness | hydration | get_hydration_data | date | |
| wellness | spo2 | get_spo2_data | date | series if present |
| training | status | get_training_status | date | |
| training | readiness | get_training_readiness | date | unwrap first |
| training | endurance | get_endurance_score | date | |
| training | fitness-age | get_fitness_age | date | |
| training | hill-score | get_hill_score | date | |
| training | race-predictions | get_race_predictions | none | |
| device | list | get_devices | none | |
| device | last-used | get_device_last_used | none | |
| device | settings | get_device_settings | id | |
| device | alarms | get_device_alarms | none | |
| device | solar | get_device_solar_data | id | may need date too; verify against reference script |
| gear | list | get_gear | none | needs userProfileNumber; keep existing hand-off via profile |
| gear | stats | get_gear_stats | id (uuid string) | |
| gear | defaults | get_gear_defaults | none | |
| goal | list | get_goals | none | |
| goal | personal-records | get_personal_record | none | |
| profile | show | get_full_name / get_user_profile | none | |
| profile | settings | get_userprofile_settings | none | `unit-system` folded in as a default field |

Exact method names, positional arguments, and `unwrap` needs are verified against `scripts/garmin_api_reference.py` and the recorded fixtures during implementation; the table is the intent, not the final code.
Where a spec needs a non-standard positional (e.g. a uuid string instead of int id) the factory accepts a `id_type` field.

### Hand-written commands

- `activity list --from --to --days --type --limit` (default limit 20).
  Uses `get_activities_by_date` when a range is given, else `get_activities` paginated.
  `truncated: true` when the limit cut results.
  Curated default fields: id, name, type, startTimeLocal, duration, distance, averageHR, maxHR, elevationGain, averageSpeed, aerobicTrainingEffect, anaerobicTrainingEffect, activityTrainingLoad, calories.
- `activity show ID`, `splits ID`, `weather ID`, `last`: as today, through shaping + envelope.
- `activity details ID` (new): `get_activity_details`.
  Per-second metrics (HR, speed, elevation, cadence, power when present) reshaped into one bucketed series table with one column per metric, auto bucket targeting 120 rows per activity.
- `activity export ID`, `export-range`: unchanged behaviour, `data: {"files": [{"activityId": ..., "path": ..., "bytes": ...}]}`.
- `auth status|login|logout`: `login` stays interactive and is meant to be run by the human.
  `status` returns `{"logged_in": bool, "token_store": path, "expires_at": ...}` where known.
- `cache stats|clear|path`, `config show|path`: envelope-wrapped.
- `schema [GROUP]`: see section 4.
- `workout *`, `log *`: see section 7.
- `diary *`: see section 8.

## 4. `pace schema`

Emits, inside the standard envelope:

```json
{"version": "2.0.0",
 "units": {"distance": "m", "duration": "s", "speed": "m/s", "elevation": "m",
           "heart_rate": "bpm", "timestamps": "epoch ms GMT; *Local fields are ISO local"},
 "exit_codes": {"0": "ok", "1": "api error", "2": "bad_args", "3": "auth_required", "4": "rate_limited"},
 "error_codes": ["bad_args", "auth_required", "rate_limited", "not_found", "endpoint_unavailable", "network", "internal"],
 "global_flags": {"--fields": "...", "--bucket": "...", "--raw": "...", "--no-cache": "...", "--verbose": "..."},
 "commands": [
   {"name": "wellness sleep", "help": "...", "keyed": "date",
    "args": [], "flags": ["--from", "--to", "--days"],
    "default_fields": ["dailySleepDTO.sleepTimeSeconds", "..."],
    "series": ["sleepHeartRate", "sleepMovement"], "ttl_s": 300}
 ]}
```

Generated from `REGISTRY` plus a static list describing the hand-written commands.
The static part also carries the workout DSL reference (section 7) and the diary entry kinds (section 8).
`pace schema wellness` filters to one group.
A test walks the Typer tree and asserts every command is present in the schema output, so the manifest cannot drift.

## 5. Skill: `.claude/skills/pace/SKILL.md`

Frontmatter: `name: pace`; description triggers on Garmin, training, running, cycling, sleep, HRV, recovery, readiness, coaching, workout analysis.

### A. Data access

1. Precheck with `pace auth status`.
   On `auth_required`: tell the user to run `pace auth login` themselves (interactive MFA).
   Never attempt login from the agent.
2. Command map: condensed table (group, command, keyed, what question it answers).
   Refer to `pace schema` for exact fields; call it once per session when unsure.
3. Envelope reading rules:
   - `ok: false` -> follow the `hint`, do not retry blindly.
   - `cache.oldest_age_s > 600` on today's data -> mention freshness ("as of N min ago"); if the user says they just synced, repeat with `--no-cache`.
   - `truncated: true` -> repeat with a larger `--limit` if the question needs it.
   - `shape.bucket_used` -> state the resolution when it matters ("15-minute averages").
4. Token budget ladder: defaults first; `--bucket 1h` or `15m` for intra-day questions; `--bucket none` only for one day or one activity; `--fields all` only when a needed field is missing from defaults; `--raw` never in normal flow.
5. Units: fixed SI.
   Convert for the user: km, min/km (or km/h for cycling), h:mm for durations, bpm, m.
   Use `*Local` timestamps.
6. Errors: `rate_limited` -> wait 60 s, retry once, then stop and say so; `endpoint_unavailable` -> feature not enabled on the account, do not retry.
7. Session start: run `pace auth status` and `pace diary show` together; use the diary profile before asking the user anything.
8. Writes (`workout push/schedule/delete`, `log *`): never unprompted, never during a check-in the user did not ask to act on.
   Show the planned write as a table, get an explicit yes, then call.
   Use `--dry-run` when unsure the DSL translation is right.
   After any write, `pace diary add --kind plan`.
9. Presentation contract (identical for every data answer):
   - Line 1: scope and freshness, e.g. `Sleep, 2026-08-20 to 2026-09-02 (data 12 min old)`.
   - One markdown table.
     Fixed column orders per domain, defined in the skill: activities, sleep, daily wellness, training status, workout detail.
   - Then at most 3 bullets under `Notes` -- observations only, unless coaching was asked.
   - ASCII only.
     No raw JSON.
     No camelCase field names shown to the user.

### B. Coaching (v0, minimal)

- Before advising, know the user's goal or event and date, weekly availability, injuries or constraints.
  Read them from `pace diary show`; ask only for empty profile fields or when the user mentions a change, then `pace diary set` / `pace diary add --kind constraint|injury`.
- After each coaching decision, `pace diary add --kind recommendation`; when the user reports how a session felt, `--kind feedback`.
  Next session starts by reading these back, so recommendations stay consistent across sessions.
- Weekly check-in recipe: `activity list --days 7`, `wellness sleep --days 7`, `wellness hrv --days 7`, `wellness summary --days 7`, `training status`, `training readiness`.
  Compare with the prior 7 days by requesting `--days 14`.
- Framing: observations -> one main recommendation -> what to watch next week.
- Guardrails: not medical advice.
  Persistent HRV drop, rising resting HR, or poor sleep -> suggest rest or easy days, never diagnose.
- Note for later: derived metrics (load ratios, baselines) will move into the CLI; until then compute inline from the table numbers and show the arithmetic.

### Install

`ln -s /home/matteo/projects/trainscout/.claude/skills/pace ~/.claude/skills/pace`.
Documented in README.

## 6. Testing and docs

### Tests (pytest, no network)

- `test_shape.py`: prune (nested null/empty removal), `select_fields` with dotted paths and missing paths, bucket ladder selection for `auto`, aggregation math, local ISO `t`, `pairs` vs `records`, segment series untouched, `--raw` bypass.
- `test_envelope.py`: success and error shapes, exit codes per error code, Typer usage error becomes `bad_args` envelope on stdout, stderr empty without `--verbose`.
- `test_factory.py` (Typer `CliRunner`, `safe_call` monkeypatched to fixtures): `--days` vs `--from/--to` resolution, single day yields list of one, ascending order, partial-failure day entry, all-fail -> `ok: false`, TTL by day age, `id`-keyed positional.
- `test_schema.py`: valid JSON, every Typer command present, group filter.
- `test_workout.py`: DSL -> Garmin payload for every step type, repeat groups, each end condition and target kind, pace range to m/s, hr zone vs bpm range, invalid DSL -> `bad_args` with the offending path.
- `test_log.py`: argument mapping to garminconnect calls (weigh_in vs body_composition selection), `--dry-run` payload, cache invalidation for the date.
- `test_diary.py`: empty file bootstrap, set dotted keys, add/remove with incrementing ids, `show` filters, active constraints listing, corrupt file -> `internal` error not crash.
- `test_cache.py`: `(value, hit, age_s)` accounting; existing TTL tests kept.
- `test_api_errors.py`, `test_ratelimit.py`: kept, adapted to the new error tuple.
- Fixtures: one recorded `--raw` payload per endpoint under `tests/fixtures/`, sanitized (ids, names, coordinates, device serials).

### Docs

- `README.md` rewritten: what it is, install, auth, `pace schema`, skill install, contract pointer.
  Short.
- `docs/CONTRACT.md`: section 2 of this spec, kept in sync with `envelope.py`/`shape.py`.
- `docs/PLUGINS.md`: `render` references replaced by `envelope`/`shape`/`factory` helpers.
- Version bumped to `2.0.0` in `pyproject.toml` and `pace/__init__.py`.

## 7. Mutations

### Safety contract

- Write commands never read or write the response cache; a successful write invalidates cache keys for the affected resource and date (e.g. `get_hydration_data:<date>`).
- Every write command accepts `--dry-run`: no network call, `ok: true`, `data: {"dry_run": true, "payload": <exact Garmin payload>}`.
- Success envelope: `data: {"action": "create" | "delete" | "update" | "link" | "unlink", "resource": "workout" | "weight" | "hydration" | "bp" | "activity_gear" | "activity_effort", "id": ..., "echo": <compact input as parsed>}`.
  `update`, `link`, `unlink` and the two `activity_*` resources were added by `docs/superpowers/specs/2026-11-06-gear-and-effort-design.md`.
- Deletes are allowed only for resources this CLI creates: workouts, weigh-ins, blood pressure readings.
- Permanently excluded: activity delete, activity edits other than gear links and RPE/feel (amended 2026-11-06, see `docs/superpowers/specs/2026-11-06-gear-and-effort-design.md`), manual activity creation, activity file upload.
- No `--yes` flag.
  Confirmation is the skill's job (show the planned write, get an explicit yes) because an agent would always pass a flag.

### `workout` group

| command | garminconnect | notes |
|---|---|---|
| `workout list [--limit]` | `get_workouts` | curated fields: id, name, sport, estimated duration, updated |
| `workout show ID` | `get_workout_by_id` | pruned full payload |
| `workout push FILE\|-` | `upload_workout` | input is the compact DSL below; `-` reads stdin |
| `workout schedule ID DATE` | `connectapi POST /workout-service/schedule/{id}` with `{"date": "YYYY-MM-DD"}` | endpoint not wrapped by the library; verify during implementation, drop the command if Garmin refuses |
| `workout delete ID` | `connectapi DELETE /workout-service/workout/{id}` | same caveat |

Compact workout DSL (input JSON):

```json
{"name": "Tempo 3x10", "sport": "running",
 "steps": [
   {"type": "warmup", "duration": "15m"},
   {"repeat": 3, "steps": [
     {"type": "interval", "duration": "10m", "target": {"pace": "4:15-4:25"}},
     {"type": "recovery", "duration": "3m"}]},
   {"type": "cooldown", "distance": "1km"}]}
```

- `sport`: `running | cycling | swimming | walking | hiking`.
- Step `type`: `warmup | interval | recovery | rest | cooldown`; `{"repeat": N, "steps": [...]}` nests.
- End condition, exactly one of: `duration` (`45s`, `10m`, `1h`, `1h30m`), `distance` (`400m`, `1km`, `5.5km`), `"open": true` (lap button).
- `target`, optional, exactly one of: `pace` (`"4:15-4:25"` min/km, converted to m/s speed range), `hr` (`"z2"` zone or `"140-150"` bpm), `power` (`"200-220"` W), `cadence` (`"170-180"`).
- Translation in `pace/workout.py` produces a `garminconnect.workout` typed model (`RunningWorkout` etc.) and lets the library serialise it.
  `estimatedDurationInSecs` is computed from durations; distance-only steps contribute an estimate from the target pace when present, otherwise 0.
- Invalid DSL -> `bad_args` with `error.message` naming the JSON path (`steps[1].steps[0].target`).
- The DSL reference is emitted by `pace schema` under `workout_dsl` and reproduced in the skill.

### `log` group

| command | garminconnect |
|---|---|
| `log weight KG [--date D] [--fat PCT] [--muscle KG] [--water PCT] [--bone KG] [--bmi X]` | `add_weigh_in` when only weight; `add_body_composition` when any extra field |
| `log weight list --from --to` | `get_weigh_ins` |
| `log weight delete PK --date D` | `delete_weigh_in` |
| `log hydration ML [--date D]` | `add_hydration_data` |
| `log bp SYS DIA PULSE [--notes T] [--date D]` | `set_blood_pressure` |
| `log bp list --from --to` | `get_blood_pressure` |

`--date` accepts the usual date forms; default today.
List commands are read commands and follow the normal shaping and caching rules.

## 8. Athlete diary

Purpose: persist what the coach must remember across sessions (goal, event, availability, constraints, past recommendations, athlete feedback) without depending on Claude Code memory.

Storage: `$PACE_HOME/diary.json`, no Garmin access, works offline.

```json
{"profile": {"goal": "sub-1:45 half", "event": {"name": "Verona HM", "date": "2026-11-15"},
             "availability": "5 sessions/wk, long run Sunday", "updated": "2026-09-04"},
 "entries": [
   {"id": 7, "date": "2026-09-04", "kind": "recommendation",
    "text": "Keep Tue quality session, swap Thu tempo for easy run: HRV down 3 days.", "tags": ["hrv"]}]}
```

- Entry `kind`: `note | plan | recommendation | feedback | constraint | injury`.
  Constraints and injuries are entries that stay active until removed; there is no separate list.
- `id` is an incrementing integer; never reused.

Commands (all envelope-wrapped, listed in `pace schema`):

| command | effect |
|---|---|
| `diary show [--since D] [--kind K] [--limit N]` | `data: {"profile": ..., "active": [constraint/injury entries], "entries": [recent, newest first]}`; default limit 20 |
| `diary set KEY VALUE` | dotted profile keys: `goal`, `event.name`, `event.date`, `availability`; sets `profile.updated` |
| `diary add --kind K [--date D] [--tag T ...] TEXT` | appends an entry, returns it |
| `diary remove ID` | removes one entry, returns it |

Corrupt or unreadable file -> `internal` error envelope with the path in `hint`; never overwrite a corrupt file silently.

## Implementation notes

- Fixture recording requires the user's logged-in tokens; run each command once with `--raw`, sanitize, commit.
  Default field curation is done against those fixtures.
- The garminconnect method signatures for `device solar`, `gear list`, `get_activity_details`, and `get_activities_by_date` must be checked in `scripts/garmin_api_reference.py` before wiring specs.
- Keep `pace.api.safe_call` returning a structured error `(code, http, message)` instead of a bare string so `envelope.error` does not re-parse text.
- `workout schedule` and `workout delete` use `api.connectapi` directly; confirm the endpoints against a real workout created by `workout push --dry-run` then pushed, before keeping them in the schema.
- Local dev: `uv run` needs the private index token; use `bash -ic 'update_aws_token && uv ...'` or run `.venv/bin/python` directly.

## Implementation notes (post-hoc)

Where the shipped core CLI departs from, or pins down, the design above:

- `auto` buckets are scored against the whole requested range (`days x 86400 s`), not one day, so `--days 7` returns a coarser grid than `--days 1` on the same endpoint.
  Section 2's "at most 120 rows" is per series per request, not per day.
- The registry's `method` names are authoritative in `pace schema`; the spec table in section 3 records intent, and a few method names were corrected against `garminconnect` while wiring the specs.
- `auth status` also reports `oauth2_expires_at` (ISO local, read from the token store) alongside `logged_in`.
  `logged_in` still means only "a token store exists": garth mints a fresh oauth2 token from the long-lived oauth1 one, so an expired oauth2 token is not a logged-out account.
- `stressValuesArray` and `bodyBatteryValuesArray` declare `min_value=0`: Garmin's `-1` / `-2` sentinels are dropped before bucketing.
- `workout schedule` and `workout delete` are implemented via `connectapi` directly: POST `/workout-service/schedule/{id}` and DELETE `/workout-service/workout/{id}`.
  Both request bodies are built as plain dicts rather than the `garminconnect` library's pydantic workout models, because Garmin's real field ids differ from `garminconnect.workout.ConditionType`.
- `workout push` builds its payload as a plain dict for the same reason.
  The DSL translator in `pace/workout.py` carries the step-type, end-condition and target ids read off a real workout on the user's account; the library's `ConditionType` / `TargetType` constants disagree with Garmin for `distance`, `lap.button` and the pace/target zone keys, so using its models would have shipped workouts Garmin rejects or silently mangles.
- `log weight add`, `log weight delete`, `log bp add` and `log bp delete` are Typer sub-groups (`log weight ...`, `log bp ...`), not the positional `log weight KG` / `log bp SYS DIA PULSE` forms of the table in section 3.
  A group is what makes room for the reads and the deletes under the same noun.
- `log bp delete VERSION --date D` also exists, which the table does not list: a blood-pressure reading is deleted by the `version` that `log bp list` reports, not by a samplePk.
- The blood-pressure bounds are garminconnect's own -- systolic `[70, 260]`, diastolic `[40, 150]`, pulse `[20, 250]`, all inclusive -- rather than a range of pace's choosing, so a `--dry-run` that passes locally also passes live.
  The library raises `ValueError` outside them, and `safe_call` maps `ValueError` / `TypeError` from the library to `bad_args` (exit 2), not `internal`.
- Writes are not idempotent, and pace does not try to make them so: there is no request id to deduplicate on.
  A retry after an ambiguous failure duplicates the workout, weigh-in or hydration entry, so `pace schema` -> `writes.idempotency` and the `network` / `rate_limited` hints tell the caller to verify with the matching read first.
