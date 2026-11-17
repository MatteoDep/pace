# Pace output contract

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
  Date-keyed commands (the registry-driven groups) always echo both `from` and `to`.
  `activity list` echoes `to` always, and adds `from` only when the call is ranged (an explicit `--from`, `--days`, or a non-default `--to` was given).
- `cache.hit`: `true` (all from cache), `false` (all fetched), `"partial"`.
  `oldest_age_s`: age of the oldest cache entry used; `0` when nothing came from cache.
  Both `cache` and `shape` are `null` for commands that never fetch or never shape: `schema`, `config show|path`, `cache stats|clear|path`, `auth status|logout`, `activity export`, `activity export-range`, and every write command.
- `shape.bucket_used`: the bucket actually applied (`"none"` when no series reduced).
  `reduced_series`: dotted paths that were bucketed.
  `fields`: `"default"`, `"all"`, or the explicit list.
- `truncated`: `true` when a `--limit` cut the result.
- `data`: command payload.
  Date-keyed commands always emit a list of `{"date": "YYYY-MM-DD", ...}` in ascending date order, even for a single day.
  This holds under `--raw` too: the payload is verbatim but each item is still stamped with the requested `date`, and a payload that is not a dict (Garmin often returns `null` or a bare list) is wrapped as `{"value": ...}` so the `date` key has somewhere to live.

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
| `bad_args` | 2 | Typer usage error, invalid date, unknown field/bucket, argument rejected by garminconnect's own validation |
| `auth_required` | 3 | no tokens, 401, garminconnect auth error |
| `rate_limited` | 4 | 429 or garminconnect too-many-requests |
| `not_found` | 1 | 404 |
| `endpoint_unavailable` | 1 | 400 on a read, 403 (feature not enabled for account) |
| `rejected` | 1 | 4xx on a write other than 401/403/404/429: Garmin refused the payload, and `message` carries its reason (e.g. "Gear's start date must be before activity's start date") |
| `network` | 1 | connection failure, 5xx |
| `internal` | 1 | anything else |
| `internal` | 130 | aborted by user (Ctrl-C) |

Typer's own usage errors are intercepted and re-emitted as a `bad_args` envelope so stdout stays JSON.

`--help` is the one deliberate exception to the JSON-only rule: `pace --help`, `pace <group> --help` and `pace <group> <command> --help` print Typer's plain-text usage and exit 0.
Bare `pace` and bare `pace <group>` are usage errors, so they still produce a `bad_args` envelope with exit 2.

A Ctrl-C during a command is caught at the top level and emitted as `{"ok": false, "command": "...", "error": {"code": "internal", "http": null, "message": "Aborted by user.", "hint": "..."}}` with exit code `130`, independent of the `internal` -> 1 mapping above.

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
  `auto` picks the smallest bucket on the ladder that yields at most 120 rows per series **across the whole requested range**, not per day: the window scored is the activity for `activity details`, and `days x 86400 s` for a date-keyed command.
  So `--days 7` gets coarser buckets than `--days 1` on the same endpoint (7 days of 1 Hz data lands on `3h`, one day on `15m`); ask for a short range or an explicit `--bucket` when intra-day detail matters.
  Within a range every day then shares the one coarsest bucket any day would have picked on its own, so the series has one homogeneous row grid across days; the result is `none` only when every day independently would have chosen `none`.
  A day with no samples of its own omits the series key entirely rather than emitting a headers-only table.
  `none` disables bucketing.
- `--raw`: Garmin JSON verbatim inside `data`, no pruning, no field selection, no bucketing.
  `shape` reports `{"raw": true}`.
- `--no-cache`: bypass the cache read for this call; the fresh result is still written to the cache.
- `--from`/`--to`: an inverted range is not an error -- the two dates are swapped, so `--from today --to -7d` and `--from -7d --to today` mean the same seven days.
- `shape.reduced_series` lists only the series that survive field selection: a bucketed series that `--fields` drops is not reported as reduced.

Pruning (always on unless `--raw`): recursively drop `null`, `""`, `[]`, `{}`.

### Bucketed series format

```json
{"cols": ["t", "avg", "min", "max", "n"],
 "rows": [["2026-09-02T07:15", 58, 55, 63, 15], ["2026-09-02T07:30", 61, 57, 70, 15]]}
```

- `t`: bucket start, local time, ISO minute precision, no timezone suffix.
  Local offset derived from Garmin's paired `*Local` / `*GMT` fields when present (searched up to three levels deep, since sleep only carries its pair inside `dailySleepDTO`), else the host zone as of the series' own first sample -- so a payload from the other side of a DST change is not shifted by an hour.
- `avg` is rounded to 1 decimal and emitted as an integer when it is integral (`65`, not `65.0`); `min`/`max` are the source values unchanged (an integer series stays integer); `n` = samples in bucket.
- Buckets with no samples are omitted.
- Sentinel readings are dropped before bucketing: Garmin writes `-1` (no reading) and `-2` (off wrist) into `stressValuesArray` and `bodyBatteryValuesArray`, and those never enter `avg`, `min`, `max` or `n`.
- Segment series (e.g. `sleepLevels` with start/end/level) are not bucketed; they pass through pruning only.

#### `activity details`

`activity details` emits its own multi-column table instead of the five-column format above: `{"cols": ["t", "hr", "speed_mps", "dist_m", "elev_m", "cadence", "power_w"], "rows": [...]}`.
Only the columns present in the activity's recorded metrics appear (for example, no `power_w` without a power meter).
`dist_m` is the last sample value in each bucket (it is a running total); every other column is the bucket average.
Every column, carried-through or averaged, is rounded to 1 decimal.
`t` is local time: ISO minute precision when a bucket is applied; `--bucket none` keeps ISO second precision instead.
The details payload carries no `*GMT` / `*Local` pair, so that local time is the *host's* zone as of the activity's first sample, not the zone the activity was recorded in -- an activity abroad reads in the host's clock.

### Rate limits and cost

Garmin is called through one process-wide limiter, and `pace schema` publishes its numbers under `data.limits`:

| limit | value | meaning |
|---|---|---|
| `calls_per_minute` | 10 | rolling one-minute cap (also 100/h, 500/day) |
| `min_interval_s` | 2 | minimum gap between two calls |
| `concurrency` | 3 | calls in flight at once |
| `max_days` | 366 | longer `--from`/`--to`/`--days` ranges are `bad_args` |
| `cold_cache_seconds_per_day` | 6 | wall time per uncached day (`60 / calls_per_minute`) |

A date-keyed command issues one Garmin call per day, so a cold range costs roughly `days x 6 s`: `--days 7` about 40 s, `--days 30` about 3 minutes, the 366-day maximum about 37 minutes.
A warm cache costs nothing; TTLs are today `spec.ttl` (5 min wellness, 1 h training, 1 day static), yesterday 1 day, older 7 days.
Prefer a narrow range, and expect `--days 30` to sit there for minutes rather than to fail.

### Units (static)

Garmin Connect returns SI regardless of user display preferences: distance `m`, duration `s`, speed `m/s`, elevation `m`, heart rate `bpm`, timestamps epoch ms GMT, `*Local` fields as ISO local strings.
Documented in `pace schema` and the skill; never repeated per response.

### Writes

Thirteen commands mutate Garmin Connect: `workout push`, `workout schedule`, `workout delete`, `log weight add`, `log weight delete`, `log hydration`, `log bp add`, `log bp delete`, `gear link`, `gear unlink`, `activity effort`, `plan push`, `plan unpush`.
Every one of them takes `--dry-run`; `pace schema` marks each with `"write": true` and lists them under `data.writes`.

`--dry-run` makes no network call.
It returns `{"dry_run": true, "payload": ...}`: the exact call pace will make, so a caller can inspect precisely what would be sent before committing to it.
The payload shape depends on the command: `workout push` shows the full Garmin workout body; `workout schedule` and `workout delete` show `{"path": ..., "json": ...}` (delete has no body); the `log` commands and `gear link`/`gear unlink` show `{"method": ..., ...arguments}` for the garminconnect call; `activity effort` shows `{"path": ..., "json": ...}` like `workout schedule`.

`--date D` on `log weight add` and `log bp add` timestamps the entry at 12:00 local time of D, not midnight, so a day's entry cannot land in the neighbouring day once Garmin converts it to GMT.
`log hydration` and the two deletes take a plain date (`cdate`) instead.

Without `--dry-run`, a successful write returns `{"action": "create"|"delete"|"update"|"link"|"unlink", "resource": "workout"|"weight"|"hydration"|"bp"|"activity_gear"|"activity_effort", "id": <Garmin-assigned id, or null when the response carries none>, "echo": <the values you sent>}`.
`gear link` and `gear unlink` return `link`/`unlink` on `activity_gear`; `activity effort` returns `update` on `activity_effort`; all three have `id: null`.
A failed write raises the same error envelope as a read (`docs/CONTRACT.md` error table above); no partial-success shape exists for a single write.
`plan push` and `plan unpush` are batches with their own result shape (see Plan below).

Deletes are limited to workouts, weigh-ins and blood-pressure readings; activities and other resources are never deleted through `pace`.
The only activity edits are gear links and perceived effort; no other activity field is ever written.

`activity effort ID [--rpe 1-10] [--feel LABEL]` sends only the given field(s) inside `summaryDTO`.
Garmin stores RPE x10 and feel in steps of 25:

| user value | stored |
|---|---|
| `--rpe N` (1-10) | `directWorkoutRpe` = N x 10 |
| `--feel very-weak` / `weak` / `normal` / `strong` / `very-strong` | `directWorkoutFeel` = 0 / 25 / 50 / 75 / 100 |

Feel labels match case-insensitively and accept `_` for `-`.
`activity show` reports the same values back as top-level `rpe` (1-10; a stored value that is not a multiple of 10 reads as e.g. `4.5`) and `feel` (label); either key is absent when unset or when the stored feel is not one of the five steps.

`gear link ACTIVITY_ID GEAR_UUID` adds one gear item to an activity and `gear unlink` removes one; `GEAR_UUID` is the 32-hex `uuid` from `gear list` (case-insensitive).
To change shoes, link the new item first, then unlink the old one: Garmin keeps every linked item, so a failed link never leaves the activity without gear.
`activity gear ID` reads an activity's gear (cached 5 minutes, since gear changed in the Garmin app cannot be invalidated by `pace`).
To review gear across many activities, do not call `activity gear` once per activity (10 calls a minute): `gear list`, then `gear activities UUID` once per item, then `activity list` for the range; an activity in no item's list has no gear.
There is no `--yes` flag anywhere in the CLI: a write either runs (given no `--dry-run`) or it doesn't.
Confirmation before a real Garmin write is the caller's responsibility -- the coaching skill must show the planned write (or its `--dry-run` payload) and get an explicit yes from the user before calling without `--dry-run`.
The confirmation rule covers Garmin writes only: `diary` commands write a local file (`"local_write": true` in `pace schema`, never `"write"`) and are outside this rule; the coaching skill decides when to ask about them.

A successful write drops the cache keys it affects (`data.writes.invalidation` in `pace schema`), so a following read is never served stale data.
Write commands themselves never read the cache: every write always calls Garmin directly.
A cache failure during that invalidation is logged (`--verbose`) and ignored: the write already landed on Garmin, so it is still reported as `ok: true`.

Creating writes are not idempotent: a retry after an ambiguous failure duplicates the workout/weigh-in/hydration.
`gear link`, `gear unlink` and `activity effort` set a state, so repeating them is harmless.
Verify with the matching read (`workout list`, `log weight list`, `log bp list`) before retrying.
The `network` and `rate_limited` hints say so too, since both otherwise read as "retry once".

`log weight delete`, `log bp delete` and `activity export-range` have a mandatory option; `pace schema` lists it per command under `required_flags`, because a flat `flags` list cannot tell a required option from an optional one.

DSL reference for `workout push`: `pace schema` -> `workout_dsl`.

Live-verified 2026-09-07: `workout push` returns `workoutId`; `workout schedule` returns `workoutScheduleId`; deleting a workout also removes its calendar entries.
`log weight add` resolves the created `samplePk` with one extra day-view read because Garmin's add response carries no id.

Live-verified 2026-11-06 on one run: `activity effort` with both flags, `--rpe` only, and `--feel` only each changed just the given `summaryDTO` key(s); every other summary field was unchanged, so Garmin merges the partial update.
Garmin marks an edited activity with a new `metadataDTO.lastUpdateDate` and `isOriginal: false`, as it does for edits made in the app.
`gear link` added a second shoe without removing the first; `gear unlink` removed only the one named; both return a JSON body.
Only a day-view entry matching the weight just written (within 100 g) and attributed to a manual source is accepted, so a scale sync on the same day cannot lend its id; when nothing matches, `id` is `null` and `log weight list` still has the answer.

## Diary

`pace diary` is the coach's durable memory.
It reads and writes one local file, `$PACE_HOME/diary.json`, and never calls Garmin.
Commands: `diary show`, `set`, `unset`, `add`, `remove`, `set-due`, `resolve`.
`pace schema` marks the six mutating ones `"local_write": true` and describes the store under the top-level `diary` key.

File shape: `{"profile": {...}, "entries": [...], "next_id": N}`.
Ids increment and are never reused, even after `remove`.

An entry is `{"id", "date", "kind", "text", "tags", "refs", "due", "resolved"}`:
- `date`: ISO date, `--date` (default today); accepts the usual date words and `-Nd`/`+Nd`.
- `kind`: one of `note`, `plan`, `recommendation`, `feedback`, `constraint`, `injury`, `decision`.
- `tags`: list of strings from repeated `--tag`, stripped, empties dropped, duplicates removed in order.
- `refs`: list of `session:<id>`, `activity:<id>` or `entry:<id>` (positive integer id; pattern in `diary.ref_pattern`).
  Repeat `--ref`; one feedback entry can name an activity and a session.
- `due`: ISO date or null; the date to look at the entry again.
- `resolved`: ISO date or null; only `constraint` and `injury` entries can be resolved.

Profile keys: `goal`, `event.name`, `event.date`, `availability`, `background`, `other_sports`, `strength`, `preferences`, `checkin.weight_days`, `checkin.last`, `dashboard.url`.
Any other key is `bad_args`.
Values are non-empty text, with these rules:
- `event.date`: a date, stored as ISO.
- `checkin.weight_days`: integer 1 to 90; the default is 7 when unset (`diary.checkin_weight_days_default`).
- `checkin.last`: `now` or an ISO datetime, stored as a local-offset ISO datetime to the second.
- `dashboard.url`: must start with `https://claude.ai/`.
Dotted keys are stored nested (`event.name` under `event`).
Setting or unsetting any key except `checkin.*` and `dashboard.*` stamps `profile.updated` with today's date.
`diary unset KEY` removes the key and is a no-op when it is not set.

`--since` also accepts an ISO datetime (for example `2026-11-17T08:00:00+01:00`); its local date is used.
`diary show [--since D] [--kind K] [--ref R] [--limit N]` returns `data = {"profile", "active", "due", "entries"}`.
`entries` are newest first, filtered, and cut to `--limit` (default 20); `truncated` is true when the cut dropped some.
`active` is every unresolved `constraint` and `injury`, oldest first, regardless of the filters.
`due` is every entry whose `due` is today or earlier, oldest due first, regardless of the filters.
Clear a due entry with `diary set-due ID --clear`, or move it with `--date D`; give exactly one of the two.
`diary resolve ID` sets `resolved` to `--date` (default today, not before the entry date and not in the future); `--reopen` clears it.
`--reopen` applies only to `constraint` and `injury` entries, like resolving; other kinds are `bad_args`.
Neither `--date` nor `--reopen` means today; both is `bad_args`.

TEXT for `diary add` is one or more words, joined with spaces.
Put it after `--` when it may start with `-`: `pace diary add --kind feedback -- -5 kg on the scale`.
No positional argument takes a date, so `-1d` is never misread as a flag; dates go in `--date`, `--due` and `--since`.

An unknown entry id is `not_found` with the hint to list ids with `pace diary show`.

Locking: every mutating command takes an exclusive lock on `diary.json.lock` for its read-modify-write.
A second writer waits up to 10 s, then fails with `internal` ("busy").
The `.lock` file stays in `$PACE_HOME`; that is normal.
`diary show` takes no lock and reads a complete file, since saves are atomic.
Saves write a temp file, `fsync` it, then rename it over `diary.json`; on failure the temp file is removed and the old file stays.
The file is created with mode 0600, and `$PACE_HOME` with 0700 when pace creates it.

Corrupt file: unreadable JSON or an unexpected shape is `internal` with a hint naming the path.
Unexpected shape includes an entry missing `date`, `kind` or `text`, a non-list `tags` or `refs` (null included), a non-string `due` or `resolved`, and a non-object `event`, `checkin` or `dashboard` in the profile.
pace never overwrites or repairs it; the user inspects or moves it.

## Plan

`pace plan` holds the training plan: blocks and dated sessions.
It reads and writes one local file, `$PACE_HOME/plan.json`.
Commands: `plan new`, `show`, `status`, `meta`, `block add`, `block set`, `block remove`, `add`, `set`, `remove`, `push`, `unpush`.
Only `plan push` and `plan unpush` write to Garmin (`plan status` reads it); `pace schema` marks them `"write": true` and the other mutating ones `"local_write": true`, and describes the store under the top-level `plan` key.

A session has `id` (integer, never reused), `date`, `sport`, `title`, `intensity`, and optionally `purpose`, `block`, `target`, `workout`, `notes`, `status`, `activity_id`.
`sport` is one of `running`, `cycling`, `swimming`, `walking`, `hiking`, `strength`, `mobility`, `other`; only the first five can carry a `workout` (the `workout push` DSL) and be pushed.
`intensity` is one of `rest`, `easy`, `moderate`, `hard`.
`target` holds `duration_s`, `distance_m` and `elevation_m`, each a number above 0 or null.
`block` is a block id (`b1`, `b2`, ...); a block has `name`, `phase`, `start`, `end` and `focus`.
Phases: `base`, `build`, `peak`, `taper`, `recovery`, `transition`.

Statuses.
Stored: `planned`, `pushed`, `skipped`, `done`.
`plan set --status` accepts `planned`, `skipped` and `done`; `pushed` is set only by pushing.
`done` is stored by hand, for sessions Garmin does not record, and never for a future date.
Derived only (reported by `plan status`, never stored): `done`, `modified`, `missed`, `unconfirmed`, `rest`.
Actual duration or distance differing from the target by more than 20 % (`modified_tolerance`) makes a matched session `modified`.

Garmin invariant: a session has a Garmin workout if and only if its `garmin` field is not null, and `status` is `pushed` only when `garmin.schedule_id` is set.
Every guard keys on `garmin`, not on `status`.
`plan remove` is refused while a session has a Garmin workout, with a hint to unpush first.
`plan new` is refused while a session has a Garmin workout dated today or later: the later of the session `date` and the date it was pushed to.
The status of a session with a Garmin workout cannot be set, except to `skipped` or `done` once its date is before today: such a workout can no longer be unpushed, so the session keeps its Garmin ids (and a `schedule_id` with a status other than `pushed`).
`plan remove` and `plan new` take the push lock before the `plan.json` lock, as a push does, so they never run between the steps of a push.
A pushed session is `stale` when its `date` or `workout` no longer matches what was pushed; `plan set` recomputes the flag.

Validation: any bad session, block or option is `bad_args` (exit 2) and nothing is written.
`plan add` is all or nothing; its errors name the item index.
An unknown session or block id is `not_found`; a block used by a session cannot be removed.

`plan set` changes only the given fields and needs at least one.
`--duration` and `--distance` take the `workout push` forms (`50m`, `1h10m`, `12km`); `--elevation` is meters.
The literal `none` clears `--duration`, `--distance`, `--elevation`, `--block`, `--activity` and `--workout`.
`--workout FILE` reads a workout DSL (a file, or `-` for stdin).

`plan show [--from D] [--to D] [--past N] [--ahead N]` returns `data = {"plan", "blocks", "sessions"}`.
`--from` and `--to` win when given; a missing end falls back to today minus `--past` (default 7) or today plus `--ahead` (default 28); `--from` after `--to` is `bad_args`.
Without a plan file it returns the empty plan and creates nothing.

`plan new --name N [--goal G] [--event-date D]` starts a fresh plan and archives the current one, when there is one, to `$PACE_HOME/plans/<created>-<name>.json`; `data.archived` is that path or null.
`<created>` is the old plan's `created` date when it is a valid `YYYY-MM-DD`, else today.
Session and block ids keep counting across plans.

Locking, atomic saves and the corrupt-file rule are those of the diary: pace never overwrites an unreadable `plan.json`.

### Plan status

`plan status [--from D] [--to D] [--past N] [--ahead N] [--no-effort] [--no-cache]` is a read command; it changes no file.
The range rule is the one of `plan show`, with `--past` 7 and `--ahead` 7 by default.
Activities are read with `get_activities_by_date` for `from` to `min(to, today)` (cache 10 min), and not at all when `from` is after today.

`data = {"days", "totals", "intensity_planned_s", "intensity_missing"}`.
Each day `{"date", "sessions", "unplanned"}`; days with neither are omitted.
A session entry is `{"id", "title", "sport", "intensity", "status", "target", "stale", "actual"}`.
`actual` is null or `{"activity_id", "type", "duration_s", "distance_m", "elevation_m", "avg_pace_s_per_km", "avg_hr", "hr_zones_s", "rpe", "feel"}`.
`avg_pace_s_per_km` is `1000 / averageSpeed`, null when the speed is 0.
`hr_zones_s` holds the seconds in Garmin's five watch zones (null entries kept); `duration_s` is the moving time when above 0, else the elapsed time.
An `unplanned` entry is an activity no session took: `{"activity_id", "type", "sport", "duration_s", "distance_m", "avg_hr", "hr_zones_s", "rpe", "feel"}`.

Each activity is used at most once, matched in this order:
1. A session's manual `activity_id` wins and never falls back; if the activity is not in the fetched rows, or an earlier session (by date, then id) already linked the same activity, the session is `done` with only `activity_id` set in `actual`.
2. A session with a pushed workout takes the activity whose `workoutId` equals its `garmin.workout_id`, on any date in the range.
3. Remaining pairs on the same local date and the same sport family (`pace/sports.py`: treadmill and trail runs count as running, e-bikes as cycling) are taken closest first, by the relative difference to the target (`|actual - target| / target`) of the duration, else of the distance, else 0, so duration and distance targets sort together; ties broken by session id, start time and activity id.

Steps 2 and 3 skip sessions stored `skipped` and sessions of intensity `rest`: a run on the day of a skipped session stays `unplanned`; only a manual `activity_id` links one.

A matched session is `modified` when a duration or distance target differs from the actual by more than 20 % (`MODIFIED_TOLERANCE`; a missing actual value does not count), else `done`.
An unmatched session keeps a stored `done` or `skipped`; otherwise `rest` for intensity `rest`, `unconfirmed` (strength, mobility, other) or `missed` (the rest) when its date is before today, else the stored `planned` or `pushed`.

`totals` maps each sport to `{"planned": {"duration_s", "distance_m"}, "actual": {...}}`.
Planned sums every session in the range except `skipped` and intensity `rest`; actual sums every activity in the range, matched or not, by sport family.
`intensity_planned_s` is the planned seconds per intensity over the same sessions, and `intensity_missing` counts those without a duration target.

RPE and feel are read with `get_activity` for every activity in the range: one Garmin call per activity, cached one day under the same key as `activity show`, so a long range is slow and counts against the rate limit.
`--no-effort` skips them and leaves `rpe` and `feel` null.
An activity whose `get_activity` call fails also gets `rpe` and `feel` null; the command still succeeds.

### Push and unpush

`plan push ID... [--force] [--dry-run]` uploads each session's workout and schedules it on the session date.
The Garmin workout is named `pace: <workout name>` (the prefix is not doubled).
Per session the steps are: delete the old workout when `garmin` is set, upload, schedule.
Each step is saved to `plan.json` as soon as it lands: after the delete `garmin` is null and `status` is `planned`; after the upload `garmin = {"workout_id", "schedule_id": null}` and `pushed_as` holds the date and workout as read before the upload; after the schedule `garmin.schedule_id` is set and `status` is `pushed`.
So the invariant holds after every step, and a later failure never loses an id.
A pushed session that is not stale is skipped (`action: "skipped"`, no call) unless `--force`, so re-running a partly failed batch never duplicates the sessions that went through.
A session with `garmin` set but no schedule (its schedule call failed) is not `pushed`, so the next push deletes that workout first.
`stale` is recomputed from `pushed_as` against the file at every save, so a `plan set --date` or `--workout` made while the push is in flight leaves the session `stale`, and the next push moves it.

Refused per session, with no Garmin call: an unknown id (`not_found`), and as `bad_args` a `skipped` or `done` session, a sport that cannot be pushed, a session without `workout`, and a date before today.
Past sessions are never pushed or re-pushed: deleting their workout would erase that day from the Garmin calendar.
`plan unpush` is refused (`bad_args`) only when both the session `date` and the date its workout was pushed to (`pushed_as.date`) are before today; an unknown id is `not_found`.
So a session moved into the past after its push can still be unpushed, which deletes the workout from its old, future day.
A session moved from the past to a date today or later may be re-pushed; the delete step removes its old workout and with it that workout's schedule on the past day, so that day loses it from the Garmin calendar.

`plan unpush ID... [--dry-run]` deletes each session's workout and saves `garmin = null`, `status = planned`, `pushed_as = null`, `stale = false`; a session without `garmin` is a `noop`.
Deleting a workout also removes its calendar schedules, and a delete answered with 404 `not_found` counts as done (the workout is already gone) and still drops the same cache keys.
Pushed workouts stay in the Garmin workout library after their day; pace deletes one only on re-push or unpush, never as a cleanup.

The whole call holds a push lock (`$PACE_HOME/plan-push.json.lock`, separate from the `plan.json` lock taken for each save), so a second `plan push` or `plan unpush`, from a terminal or the bot, waits up to 10 s, then re-reads each session and skips what is already pushed; past the wait it fails with `internal` "busy".
`plan remove` and `plan new` wait for the same lock and fail the same way.

`data = {"results": [...], "done": n, "failed": n}`, one result per distinct id in order: `{"id", "ok", "action", "steps", "error", "garmin", "session"}`.
`action` is `pushed`, `skipped`, `unpushed` or `noop`, and null for a refused session.
`steps` lists the steps that completed, each `{"step": "delete" | "upload" | "schedule", "payload", "id"}` with the Garmin-assigned id (null for a delete).
`error` is the error object of the error envelope, or null.
`garmin` holds the Garmin ids that exist for the session as far as pace knows, and `session` the session as saved.
The envelope is `ok: false` with the first error only when every session failed and no step completed; otherwise it is `ok: true` with exit 0, even when `failed` is above 0, and failures are reported per session.
If the session vanished from the file during the push (`not_found`, hint `pace plan show`), or saving after a landed upload failed, the result is an error whose `garmin` carries the landed ids and whose hint names the `pace workout delete` call that cleans up.
If only the save after the schedule failed, the workout id is already recorded and the hint says to push again, which replaces it.
Any error outside the Garmin calls, such as an unreadable `plan.json`, fails that session only; the results already collected are kept.
A `network` or `internal` error on the upload is ambiguous, so its hint says to look for the `pace: <name>` workout in `pace workout list` before retrying; an upload response without `workoutId`, or a schedule response without `workoutScheduleId`, is an `internal` error.

`--dry-run` takes no lock, makes no network call and changes no file; `data.dry_run` is true and each result lists the steps it would run with their exact payloads (the schedule path shows `{workout_id}` for the id the upload would return).

Upload drops the cached workout list; schedule and delete also drop every cached calendar read (`connectapi:/calendar-service/`), as do `workout schedule` and `workout delete`.

### Workout calendar

`workout calendar [--from D] [--to D] [--past N] [--ahead N] [--no-cache]` is a read command listing the workouts scheduled on the Garmin calendar; the default range is today to 14 days ahead.
`data` is `[{"date", "schedule_id", "workout_id", "title"}]` sorted by date and schedule id.
It makes one cached call (5 min) to `GET /calendar-service/year/{Y}/month/{M}` per calendar month touching the range, with `M` counted from 0; more than 12 months is `bad_args`.
The month view also returns days of the neighbouring months, so items are kept only when `itemType` is `workout`, `id` is set and the date is in the range, and are deduplicated by `id`.
A 400 from Garmin surfaces as `rejected`.

## Load

`pace load [--weeks N] [--weeks-ahead N] [--sport running|all] [--no-cache]` returns weekly and daily training load.
It is keyed by weeks (`keyed: "week"` in `pace schema`): there is no `--from` or `--to`.
`--weeks` counts whole weeks back, this week included (default 16, at least 1).
`--weeks-ahead` counts whole weeks after this one; by default the range runs through the week of the plan's last session, never before this week.
The end is capped at 26 weeks ahead, also when `--weeks-ahead` asks for more.
`--sport` is `running` (running family activities and running sessions, the default) or `all`; anything else, `--weeks 0` or a negative `--weeks-ahead` is `bad_args`.
The range always covers whole ISO weeks (Monday to Sunday).

`data = {"weeks", "days", "range": {"from", "to"}, "sport"}`.
Metrics are `km`, `elev_m` and `hours`, rounded to 2 decimals at output only.
An activity counts `distance / 1000`, `elevationGain` and `movingDuration` (else `duration`) over 3600; a missing value counts 0.
Its day is the local date from `startTimeLocal`, never the GMT one.
A session counts its `target` (`distance_m / 1000`, `elevation_m`, `duration_s / 3600`); a null target value adds 0 and is counted in `planned.missing`.
Sessions with status `skipped` or intensity `rest` are left out; a stored `done` session counts.

Week row: `{"week": "2026-W47", "start", "actual", "planned", "projected"}`.
The week key is the ISO year and week.
`actual` is null for a week starting after today, `planned` is null for a week that ended before today.
`planned` carries `missing`, a count per metric of sessions with a null target value.
`projected` is the sum of the days' `effective` values.
For a past week `projected` equals `actual`.

Day row: `{"date", "actual", "planned", "effective", "rolling7"}`.
`actual` is null after today; `planned` is null on a day without a session, and past days keep it so a missed session shows.
`effective` is `actual` before today and `planned` after today.
Today it is `actual` when any activity of the selected sport is recorded today, else `planned`; the choice covers all three metrics together.
`rolling7` on a day is the sum of `effective` over that day and the six before it, so on a week's Sunday it equals that week's `projected`.

The fetch reads 6 days before the first row so the first `rolling7` values are complete.
It makes one `get_activities_by_date` call from that day to today (skipped when the whole range is in the future).
That method pages internally in requests the rate limiter does not count.

## Charts

`pace chart KIND [ID] [--weeks N] [--weeks-ahead N] [--from D] [--to D] [--past N] [--ahead N] [--metric km|elev|hours] [--sport running|all] [--out PATH] [--no-cache]` renders one PNG chart and returns where it is.
`data = {"path", "kind", "range": {"from", "to"}, "summary"}`; `summary` always has `empty`, true when the range holds no data, and the PNG then shows a "No data in range" note instead of marks.
Every chart is a PNG of exactly 1200 x 675 px with one shared light style.
Without `--out` the file is `$PACE_HOME/charts/<name>.png`, where name is `volume-<metric>[-all]-<from>-<to>` for `volume`, `activity-<id>` for `activity` and `<kind>-<from>-<to>` for the others, so two volume charts with different metrics or sports never overwrite each other.
`--out` must end in `.png`; its parent directories are created and `path` is returned absolute.
An unknown `KIND`, an `ID` given to a kind that takes none (or missing for `activity`), or a flag the kind does not use is `bad_args`.

Kinds so far:

- `volume`: weekly bars and the rolling 7-day sum, from the same data as `pace load`.
  Flags: `--weeks`, `--weeks-ahead` and `--sport` as in `pace load` (same defaults and checks), and `--metric` `km` (default), `elev` (elevation gain, m) or `hours`.
  Past weeks show `actual`; the current week shows `actual` with the rest of `projected` stacked lighter and labeled `actual / projected` (no label when both are 0); future weeks show `projected`, lighter.
  The rolling 7-day line runs at daily resolution, solid up to today and dashed after, so on each Sunday it meets the top of that week's bar.
  `summary = {"metric", "sport", "weeks": [{"week", "actual", "projected"}], "rolling7_today", "plan_ahead", "empty"}`: `actual` is null for future weeks, `rolling7_today` is null when today is outside the range, `plan_ahead` is false when no session is planned from today on (the line then stops at today instead of dropping to zero).
- `plan`: one mark per plan session on a date axis, one row per sport, colored and shaped by its `plan status` status; unplanned activities are hollow circles.
  Flags: `--from`, `--to`, `--past`, `--ahead` as in `plan status`; the default range is 14 days back and 14 ahead.
  It makes the same single activities call as `plan status` and never fetches effort.
  `summary = {"counts": {status: n}, "unplanned", "empty"}`, counting every session in the range by its derived status.

- `recovery`: daily resting heart rate (dots) and its 7-day trailing calendar mean (line), from `pace wellness sleep-range`.
  Flags: `--from`, `--to`, `--past`, `--ahead`; the default range is 90 days back through today (the 28-day Garmin cap is handled by chunking).
  The mean covers the 7 calendar days ending on each day and ignores days without a value; the fetch reads 6 days before the range so the first means are complete.
  HRV (`hrv` dots and `hrv_7d` dashed line) goes on a second axis only when any value in the range is non-null.
  `summary = {"days": [{"date", "rhr", "rhr_7d"} (plus "hrv", "hrv_7d" when available)], "rhr_7d_latest", "hrv_available", "empty"}`.
- `sleep`: stacked deep, light, REM and awake hours per night; the sleep score is a line on a second axis only when present.
  Flags and default range as `recovery`.
  `summary = {"nights": [{"date", "sleep_h", "deep_h", "light_h", "rem_h", "awake_h", "score"}], "sleep_7d_mean_h", "score_available", "empty"}`; `sleep_7d_mean_h` is the mean of the nights in the 7 calendar days ending at the last night, awake time excluded.

- `weight`: one dot per weigh-in (the latest of each day) and its 7-day trailing calendar mean, from one `get_weigh_ins` call.
  Flags and default range as `recovery`.
  The mean covers the weigh-ins within D-6 .. D and is drawn on days that have a weigh-in; the fetch reads 6 days before the range.
  `summary = {"points": [{"date", "kg", "kg_7d"}], "first", "last", "mean", "count", "empty"}`; `first`, `last` and `mean` are null when there are no weigh-ins.
- `intensity`: per ISO week, actual time in HR zones 1-5 (stacked, left bar) next to planned time by session intensity `easy`, `moderate`, `hard` (stacked, right bar), in hours.
  Flags: `--from`, `--to`, `--past`, `--ahead` (default: 56 days back and 14 ahead, snapped out to whole ISO weeks, Monday to Sunday; an end given with `--from`, `--to`, `--past` or `--ahead` is not snapped) and `--sport running|all` (default running).
  Zone time is the sum of `hrTimeInZone_1..5` (the watch's zones) over the activities of the sport family; planned time is the `duration_s` of the sessions of that sport, skipped and rest sessions excluded.
  A range cut by explicit flags shows partial edge weeks, only the days inside it; sessions without a `duration_s` are counted per week and noted on the bar.
  `summary = {"sport", "weeks": [{"week", "start", "zones_s": [z1..z5], "planned_s": {easy, moderate, hard}, "planned_missing", "low_share"}], "empty"}`; `low_share` is the fraction of zone time in zones 1-2 (null when no zone time).
- `activity ID`: pace (running family, min/km, faster is up, samples under 0.5 m/s dropped) or speed (other sports, km/h), heart rate on a second axis and elevation below, all over distance.
  Takes no flags; it reads `get_activity_details` (bucketed as `pace activity details` does) and `get_activity` (type, summary), both cached for a day.
  `range` is the day of the activity, `from` = `to`.
  `summary = {"activity_id", "family", "distance_km", "moving_time_s", "avg_pace_min_km" (running) or "avg_speed_kmh" (others), "avg_hr", "elevation_gain_m", "empty"}`.

## Sleep range

`pace wellness sleep-range [--from D] [--to D] [--days N] [--no-cache]` returns one row per night, keyed `date`, in ascending order.
Row: `{"date", "sleep_s", "deep_s", "light_s", "rem_s", "awake_s", "rhr", "avg_hr", "hrv", "hrv_7d", "score"}`; seconds throughout, `sleep_s` is deep + light + REM (awake excluded), and any field is null when Garmin has none.
`hrv`, `hrv_7d` and `score` are null on the current watch (Forerunner 55).
It reads `/sleep-service/stats/sleep/daily/{from}/{to}` through `connectapi`, which accepts at most 28 days inclusive per call (29 is HTTP 400).
The range is cut into fixed 28-day windows anchored on the calendar (day ordinal divided by 28), each clipped to the range, so a repeated read asks for the same windows and hits the cache.
Days after today are never requested; a window ending in the past is cached for 7 days, one ending yesterday for 1 day, one holding today for 1 hour.
There is no `--fields`, `--bucket` or `--raw`; the charts read the same rows.

## Keeping this file honest

`pace/envelope.py` owns the envelope keys and exit codes.
`pace/shape.py` owns pruning, field selection and the bucket ladder.
`tests/test_envelope.py` and `tests/test_shape.py` assert the examples above.
Change the code, the test, and this file in the same commit.
