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

Eleven commands mutate Garmin Connect: `workout push`, `workout schedule`, `workout delete`, `log weight add`, `log weight delete`, `log hydration`, `log bp add`, `log bp delete`, `gear link`, `gear unlink`, `activity effort`.
Every one of them takes `--dry-run`; `pace schema` marks each with `"write": true` and lists them under `data.writes`.

`--dry-run` makes no network call.
It returns `{"dry_run": true, "payload": ...}`: the exact call pace will make, so a caller can inspect precisely what would be sent before committing to it.
The payload shape depends on the command: `workout push` shows the full Garmin workout body; `workout schedule` and `workout delete` show `{"path": ..., "json": ...}` (delete has no body); the `log` commands and `gear link`/`gear unlink` show `{"method": ..., ...arguments}` for the garminconnect call; `activity effort` shows `{"path": ..., "json": ...}` like `workout schedule`.

`--date D` on `log weight add` and `log bp add` timestamps the entry at 12:00 local time of D, not midnight, so a day's entry cannot land in the neighbouring day once Garmin converts it to GMT.
`log hydration` and the two deletes take a plain date (`cdate`) instead.

Without `--dry-run`, a successful write returns `{"action": "create"|"delete"|"update"|"link"|"unlink", "resource": "workout"|"weight"|"hydration"|"bp"|"activity_gear"|"activity_effort", "id": <Garmin-assigned id, or null when the response carries none>, "echo": <the values you sent>}`.
`gear link` and `gear unlink` return `link`/`unlink` on `activity_gear`; `activity effort` returns `update` on `activity_effort`; all three have `id: null`.
A failed write raises the same error envelope as a read (`docs/CONTRACT.md` error table above); no partial-success shape exists for a single write.

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
Confirmation before a real write is the caller's responsibility -- the coaching skill must show the planned write (or its `--dry-run` payload) and get an explicit yes from the user before calling without `--dry-run`.

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

## Keeping this file honest

`pace/envelope.py` owns the envelope keys and exit codes.
`pace/shape.py` owns pruning, field selection and the bucket ladder.
`tests/test_envelope.py` and `tests/test_shape.py` assert the examples above.
Change the code, the test, and this file in the same commit.
