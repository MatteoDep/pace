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
| `internal` | 130 | aborted by user (Ctrl-C) |

Typer's own usage errors are intercepted and re-emitted as a `bad_args` envelope so stdout stays JSON.

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
  `auto` picks the smallest bucket on the ladder that yields at most 120 rows per series within the window it is scored against: one activity for `activity details`, one day for a single-day payload.
  For a multi-day (list) payload, `auto` scores each day independently but then applies to every day the single coarsest bucket any day would have picked, so the whole series shares one homogeneous row grid across days; the result is `none` only when every day independently would have chosen `none`.
  `none` disables bucketing.
- `--raw`: Garmin JSON verbatim inside `data`, no pruning, no field selection, no bucketing.
  `shape` reports `{"raw": true}`.
- `--no-cache`: bypass the cache read for this call; the fresh result is still written to the cache.

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

#### `activity details`

`activity details` emits its own multi-column table instead of the five-column format above: `{"cols": ["t", "hr", "speed_mps", "dist_m", "elev_m", "cadence", "power_w"], "rows": [...]}`.
Only the columns present in the activity's recorded metrics appear (for example, no `power_w` without a power meter).
`dist_m` is the last sample value in each bucket (it is a running total); every other column is the bucket average.
`t` is local time: ISO minute precision when a bucket is applied; `--bucket none` keeps ISO second precision instead.

### Units (static)

Garmin Connect returns SI regardless of user display preferences: distance `m`, duration `s`, speed `m/s`, elevation `m`, heart rate `bpm`, timestamps epoch ms GMT, `*Local` fields as ISO local strings.
Documented in `pace schema` and the skill; never repeated per response.

## Keeping this file honest

`pace/envelope.py` owns the envelope keys and exit codes.
`pace/shape.py` owns pruning, field selection and the bucket ladder.
`tests/test_envelope.py` and `tests/test_shape.py` assert the examples above.
Change the code, the test, and this file in the same commit.
