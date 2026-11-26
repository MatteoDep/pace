# Dashboard recipes and page requests: design

Proposed by the coach with the athlete on 2026-11-26 (`~/training/proposals/`), amended after a check against the code.
Delivered in two pull requests: A is Part 1 (recipe export) with its coach-side changes, B is Part 2 (page requests) with its coach-side changes.

## Why

The athlete wants the coach to shape each athlete's dashboard (which numbers, which charts, which quick actions) without a pace release for every new section.
Today the dashboard page and its data sources live in the artifact store, so the coach can already change charts and layout.
The gap is data: the page can only read files the coach uploads, and pace produces exactly one such file (`pace load --export`).
Every new section therefore needs new pace code.

The first sections the athlete asked for:
- Health over time: resting HR, predicted race times (5K, 10K, half, marathon), body weight, fitness age.
- A carousel of recent activities, each with a few stats, where the athlete can set the shoe, the RPE and the feel in a couple of clicks.
- A weigh-in from the dashboard.

## Goals

- A per-athlete recipe, a JSON file in `PACE_HOME`, lists the data sources the dashboard reads.
  Adding a source that an existing read command can return is a recipe line, with no pace code.
- `pace export` runs a recipe within a call and time budget and writes one bundle file with one section per source.
- `pace requests apply` runs actions the athlete requested from the page, after checking every value in code.
  First actions: set the shoe of an activity, set the RPE and feel of an activity, add a weigh-in.
- The coach (skill and `dashboard.md`) uses both: the export at every dashboard refresh, the requests at session start and in unattended runs.

## Non-goals

- New write actions without code.
  Every action from the page writes to Garmin from untrusted browser input, so each one stays a short, reviewed piece of pace code.
- Live writes from the page: requests wait for the next coach session (the athlete accepted this).
- Migrating the existing settings panel (`data/settings-pending`) onto requests.
  It can become a request type later.
- Unit conversion in pace: the bundle keeps what pace returns (SI, and Garmin's own units where pace passes them through, such as weight in grams); the page converts for display.
- Computed metrics that no read command returns: those stay pace code.
- Plugin commands in recipes.
  `pace schema` does not list plugins today (`pace/commands/schema.py`, `pace/plugins.py`), so v1 recipes name only built-in commands.
  Letting a plugin declare itself in the schema, and as exportable, is a follow-up; it would make plugins the escape hatch for computed metrics.

## Anti-goals

- A recipe that can write: to Garmin, to the diary, the plan or the zones store, or to files outside the export path.
- An export that fails as a whole, hangs past the coach's tool time limit, or leaves the dashboard empty because one source failed.
- A request from the page that writes twice (a double press, or a retry after a lost response or a lost delete).

## Approaches

1. Recipe export plus validated requests (picked).
   Reads become data (a recipe line); writes stay a fixed list in code.
   Once this is built, new views need no code, and the export works the same in unattended refreshes.
2. A pace plugin per athlete.
   The plugin mechanism already exists, and it is the most flexible.
   But every new section is Python code to write, test and install, which is what the athlete wants to avoid.
3. No pace changes: the coach builds the files with ad hoc commands and a script.
   It is fragile, costs coach tokens at every refresh, and cannot be reproduced in unattended runs.

The athlete picked 1; plugins stay the escape hatch once they can appear in the schema (Non-goals).

## Part 1: recipe export

### Recipe file

Location: `$PACE_HOME/dashboard/recipe.json` (`PACE_HOME` defaults to `~/.pace`); `--recipe PATH` reads another file.
When the file does not exist, `pace export` uses a default recipe shipped with pace (below), so a new athlete gets a working dashboard.
`pace export --init` writes the default recipe to that path, for the coach to edit; it refuses to overwrite an existing file.

A recipe is `{"version": 1, "datasets": [...]}`.
Each dataset:

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | Section id in the bundle: 1-64 letters, digits, `_`, `-` (the dashboard's dataset id rule). Unique. |
| `command` | yes | A built-in pace command, as in `pace schema` (`"wellness sleep-range"`). Must be exportable (below). |
| `argv` | no | Arguments exactly as typed after the command (`["--days", "112"]`). Positional arguments go here too. |
| `rows` | no | Dotted path to the list of rows inside the envelope's `data`. Default: `data` itself when it is a list; an object becomes one row. |
| `columns` | yes | `{"<column>": "<dotted path inside a row>"}`. Only these columns are written. A missing path gives `null`. |
| `foreach` | no | `{"dataset": "<earlier id>", "limit": N}`: run the command once per row of an earlier dataset (its first `limit` rows). An `argv` item that is exactly `{column}` is replaced by that row's value. Every output row gets the parent row's values under `parent.<column>` for the columns named in `argv`. |

Dotted paths use the same syntax as `--fields` (`latestWeight.weight`).
A `{column}` placeholder is a whole `argv` item, allowed only in a `foreach` dataset, and names a column of the parent dataset.
Unknown keys are refused.
`columns` is required because everyone who can open the dashboard can read the whole bundle: a recipe names what it exposes.

Sampling (one day every N days) is a command flag, not a recipe key: see `--every` under Endpoint changes.
A recipe passes it in `argv`, so `pace <command> <argv>` in a shell returns the same `data` the export sees.

### Exportable commands

The export runs only commands that pace marks as exportable, shown by `pace schema` as `"exportable": true`.
Exportable means: no Garmin write, no write to local stores or files, no auth, cache or config changes, and no private diary text.
Registry reads (`pace/spec.py`, every `wellness`, `training`, `gear`, `goal`, `device` and `profile` read) are exportable, since the registry holds only reads.
Among the hand-written commands, only an explicit list is: `activity list|show|gear|splits|details|weather|last`, `load`, `plan show`, `plan status`, `wellness sleep-range`, `log weight list`, `log bp list`, `workout list|show|calendar`, `zones show`.
It is an allowlist, so a new hand-written command is not exportable until someone marks it.
`gear link`, `activity effort`, `activity export`, `zones estimate` (it writes locally even without `--save`), `chart`, `diary *` and every write are not.
The check also covers arguments: `--export`, `--out`, `--out-dir`, `--save` (they write a file or a store), `--no-cache` (it spends calls on every refresh) and `--help` are refused in `argv` even on an exportable command.

### Running

- One process for the whole recipe, sharing pace's rate limiter (`pace.ratelimit.limiter`, one per process) and cache, so a recipe cannot exceed the limits that one command respects.
- Each dataset runs the command's own click command in-process, with its parsing, defaults, date resolution and shaping, and reads the envelope it prints.
  So `pace <command> <argv>` in a shell returns the same `data` the export sees.
- Each command runs its own event loop (`asyncio.run`), so the limiter's lock and the concurrency semaphore must work across loops: today they bind to the first loop that waits on them, and the second command fails with `RuntimeError: ... is bound to a different event loop`.
  The limiter keeps one call history for the process and makes a fresh lock and semaphore for each loop.
- Datasets run in recipe order; a `foreach` dataset must come after the dataset it reads.
- Budget: `--max-calls N` (Garmin calls, cache hits are free) and `--max-seconds N`, defaults 60 calls and 480 s, well under the coach's 10-minute tool limit.
  pace's limiter never fails on its own: it sleeps until a slot frees (up to about an hour once 100 calls in an hour are used), so without a budget an expensive recipe hangs and is killed with no bundle written.
  The budget is enforced where every Garmin call passes (`pace.api.safe_call`), inside the limiter's lock, so the calls a fan-out gathers cannot all pass the check at once: a call past `--max-calls`, or one whose limiter wait would end past the deadline, is not sent and fails with the new error code `deferred`.
  A Garmin `rate_limited` or `network` error also ends the budget, so every later call is deferred instead of hitting the same wall (as `zones estimate` does).
  Cache hits stay free after the budget ends, so a dataset served from the cache still refreshes.
  A deferred dataset is handled like any other failure (Failures below) with the reason `deferred`; a deferred day counts in `day_errors`; the next refresh continues from the cache.
  Without an active budget (every command outside `pace export`) nothing changes.
- Login is checked once before the first dataset: without Garmin tokens the export is `auth_required` and never prompts for credentials.
- `pace export --check` validates the recipe without network calls: JSON shape, ids, exportable commands and flags, `argv` accepted by the command's parser (placeholders parsed as `0`), placeholders, `foreach` order.

### Bundle

`pace export --out PATH` (default `$PACE_HOME/dashboard/bundle.json`) writes:

```json
{"meta": [{"label": "Generated", "value": "2026-11-26T10:16:15+01:00"},
          {"label": "Recipe", "value": "~/.pace/dashboard/recipe.json"}],
 "datasets": {
   "rhr": {"rows": [{"date": "2026-11-25", "rhr": 44}],
           "meta": {"command": "wellness sleep-range --days 112", "generated": "...",
                    "columns": ["date", "rhr"], "rows": 112, "calls": 0, "cache_oldest_age_s": 812,
                    "stale": false, "reason": null, "error": null, "day_errors": 0, "errors": []}}}}
```

The `Recipe` value is `default`, or the recipe path with the home directory written as `~`.

The envelope's `data` is a summary: per dataset `rows`, `calls`, `stale`, `reason`, `error`, `day_errors`, `errors`, plus the path, the bundle size in bytes, and the run's total calls and seconds.

### Failures

- A dataset that fails as a whole (a Garmin 429 `rate_limited`, `endpoint_unavailable`, `network`, `deferred`) does not fail the export.
  Its section keeps the rows of the previous bundle at the same path, with `stale: true`, the previous `generated` time, `reason` (`deferred` or `error`) and the error code.
  With no previous bundle, the section has no rows and the error code.
- Previous rows are reused only when the previous section has the same column names (kept in its `meta.columns`), so an edited recipe line never gets rows in the old shape.
- On a successful command, a missing `rows` path means no rows: pace drops empty lists from its output, so a range without weigh-ins has no `dailyWeightSummaries` key.
- The bundle, readable by everyone who can open the dashboard, carries error codes only; the messages go to the envelope summary for the coach.
- `auth_required` stops the export: no bundle is written, the previous one stays, and the envelope carries the error, so the coach asks the athlete to log in.
- Date-keyed commands that fetch one day per call return `{"date", "error"}` items for the days that failed and raise only when every day fails (`pace/factory.py`).
  The export replaces each failed day with the previous bundle's row for the same date (in the column the recipe maps from the path `date`), or drops it when there is none or no column maps `date`, and counts them in `day_errors`.
  So a brief Garmin error never wipes points the previous bundle had, and a missing day does not keep the whole section stale.
- A `foreach` child that fails is recorded in the section's `meta.errors` (parent values and error code); the previous bundle's rows with the same parent values are kept, and the other children still run.
  A placeholder value that is null or starts with `-` is not run (it could be read as a flag) and is recorded as an error the same way.
- An invalid recipe (`--check` would fail) is `bad_args` and writes nothing.
- The previous bundle is replaced atomically, so a crash never leaves half a file.
  An unreadable previous bundle counts as none.

### Cost

Rate limits are 10 calls per minute, 100 per hour, 500 per day.
Cache lifetimes set what a refresh costs: `activity list` 10 minutes, `activity gear` 5 minutes, `activity show` 1 day; for date-keyed commands, today follows the endpoint's TTL, yesterday 1 day, older days 7 days (`pace/cache.py`).
A refresh therefore still costs calls: a `foreach` over N activities with `activity show` costs N calls once a day, a `foreach` over shoes with `gear activities` costs one call per shoe on every refresh more than 5 minutes after the last (raise it with `cache_ttl_overrides` for `gear.activities` in `config.toml`), and a cold first export costs more.
The summary reports calls per dataset; `--every`, `foreach.limit` and the budget are the recipe's tools to keep the cost bounded.
The builder measures the default recipe's cold and warm cost and documents it next to the recipe.

### Endpoint changes needed

- `--every N` on the date-keyed registry commands that call Garmin once per day (`training fitness-age` and the other `dated` specs in `pace/factory.py`): fetch only the days of the range whose ordinal (`date.toordinal()`) is a multiple of N, plus the range's last day.
  The grid is fixed, so from one day to the next only the newest day changes and the cache stays warm; a grid that moved with the end date would cost every sampled day again each day.
  Default 1 (every day); 0 or less is `bad_args`.
- `training race-predictions`: accept `--from/--to/--days` and return one row per day from a single Garmin call (`get_race_predictions(startdate, enddate, "daily")`, ranges up to one year).
  With none of the three flags it keeps today's behaviour (latest prediction, one object).
  This needs a new kind of dated spec: one call for the whole range, not one per day.
- `load --rows`: return the dashboard rows (today written only by `--export`) as the envelope's `data`, so the current load dataset becomes a recipe line.
  `--rows` takes the same rules as `--export` (`--sport` refused, both sports), and the two cannot be combined.
  `pace load --export` keeps working.

## Part 2: page requests

### On the page

Each press on the page writes one new document, with a fresh id, to a store collection under `data/` where only editors write (an odd number of path segments, for example `data/requests/open`; the exact path is set in PR B):

```json
{"op": "shoe.set", "params": {"activity_id": 12345678901, "gear_uuid": "0123456789abcdef0123456789abcdef"},
 "at": "2026-11-26T09:30:00Z"}
```

Before the press, the page shows exactly what will be written (activity, shoe or values, date).
After it, the page shows the request as pending ("applied at the next session") until the document is gone.
Numbers are always SI: the page converts pounds to kg before writing.

### Operations (v1)

| op | params | What runs |
|---|---|---|
| `shoe.set` | `activity_id`, `gear_uuid` | Link that shoe first, then unlink every other shoe linked to the activity (the order CONTRACT.md gives, so a failed link never leaves the activity without a shoe). Non-shoe gear is untouched. |
| `effort.set` | `activity_id`, `rpe` and/or `feel` | `pace activity effort` with the given fields. |
| `weight.add` | `kg`, `date` | `pace log weight add` for that date, unless the day already has it (below). |

### `pace requests apply FILE [--dry-run]`

Input: a JSON list of `{"id", "version", "at", "op", "params"}`, as the coach read them from the store.
For each request, in `at` order:

1. Validate, refusing with a reason on the first failure:
   - `op` is in the list above; `params` has exactly the allowed keys, with the right types.
   - `activity_id`: positive integer, and `activity show` finds it.
   - `gear_uuid`: 32 hex characters, in `gear list` as an active shoe.
   - `rpe`: integer 1-10; `feel`: one of `very-weak`, `weak`, `normal`, `strong`, `very-strong`; at least one of them.
   - `kg`: number in a plausible range (for example 25-250); `date`: ISO date, not in the future, not older than one year.
2. Skip it as `applied` if the applied log already holds the same `id` with the same content hash (hash of `op` and `params`).
   This covers a retry after a lost store delete.
3. For `weight.add`, read `log weight list` for that date first: a manual entry within 100 g (the matching `pace/commands/log.py` already uses after a write) means the weigh-in is there, so the request is `applied` without a new write.
   This covers a double press and a retry after a lost response, since creating writes are not idempotent.
   `shoe.set` and `effort.set` set a state, so repeating them is harmless.
4. Run it through pace's existing write runner (same payloads, same cache invalidation as the CLI command), unless `--dry-run`.
5. Append `id`, content hash, op, outcome and time to the applied log, `$PACE_HOME/requests-applied.jsonl`; failed attempts are logged too, so attempts can be counted.

Output (`data`): one entry per request: `{"id", "status": "applied" | "refused" | "failed", "reason", "did"}`, where `did` lists the writes made (for `shoe.set`, the link and each unlink).
- `refused` is permanent: a validation failure, or a Garmin answer that a retry would not change (`rejected`, `not_found`, `endpoint_unavailable`).
- `failed` is worth retrying: `network`, `rate_limited`, `internal`.
  The third failed attempt of the same request becomes `refused` with the reason "gave up after 3 attempts".

A request is never built into a shell command: values reach the write functions as typed arguments.
`requests apply` is a Garmin write command (`"write": true` in `pace schema`).

### Coach flow

At session start, and in unattended runs (Confirmation below):
1. List the requests collection; when it is empty, stop.
2. Write the documents to a scratch file and run `pace requests apply`.
3. Delete each `applied` or `refused` document pinned to the version read, so a newer press survives; keep `failed` ones for the next session.
4. Tell the athlete in one line what was applied, and each refused request with its reason, once.
5. Refresh the dashboard, so the page shows the new shoe, effort or weight.

### Confirmation: the press is the yes

Today every Garmin write needs the athlete's explicit yes after seeing the planned write (CONTRACT.md, `pace schema` `writes.confirm`, SKILL.md "Writes").
A press on the page counts as that yes, since the page shows the planned write before the press.
The coach applies requests at session start and in unattended runs without asking again.
CONTRACT.md, `writes.confirm` and SKILL.md get one exception for page requests, next to the existing ones for RPE/feel, weight and shoe stated in a session.
This relies on the dashboard being private: only its editors can write to the requests collection.
If it is ever shared with edit rights, the rule needs another look.

Rejected alternative: the press only queues, and the coach asks for a yes in a live session.
It asks twice for the same write, and requests wait until the athlete opens a session.

## Part 3: coach side

Changes in the skill (`.claude/skills/pace/` in this repo) and docs, not in pace code.

With PR A:
- `dashboard.md` Create and Refresh: `pace export`, one upload of the bundle, one update per dashboard dataset pointing at the new upload (each dataset reads its section with a `dash.loader`), then the existing check and settings steps.
- `dashboard.md` new procedure "Add a view": add a recipe line, `pace export --check`, `pace export`, add the dataset and the page section, read the page check once, record a diary note.
  It replaces the rule that sections are never added to the dashboard ("never extra sections bolted on here"); deep dives on one run or one block stay separate artifacts or charts.
- `reference.md`: command map entries for `export`, `--every`, the race-predictions range and `load --rows`.
- `docs/CONTRACT.md`: the export, the `deferred` error code and the endpoint changes.
- Rollout, once, in the session that merges A and before the coach's next refresh: the live dashboard's `load` dataset moves from `load.json` to the bundle's `load` section (its source URL and a `dash.loader` in the page).
  Until then a refresh by the new procedure would leave the `load` dataset on the old file.

With PR B:
- `SKILL.md`: requests at session start and in unattended runs (Coach flow above), next to the pending settings, and `requests apply` in the "Writes" section with the page-request exception.
  `tests/test_skill.py` requires every write command to be named there, and caps SKILL.md at 250 lines (224 today): keep SKILL.md to a few lines and put the procedure in `dashboard.md`.
- `reference.md`: command map entry for `requests`.
- `docs/CONTRACT.md` and `pace schema` (`writes.confirm`): the page-request exception (Confirmation).

The first page sections, built by the coach after the pace work lands:
- Health: resting HR per night with a 7-day mean; weight with a weigh-in form; predicted race times (four lines); fitness age (weekly points).
- Activities: a carousel of the latest 15 activities (date, name, type, distance, time, pace, average HR, elevation gain, RPE and feel, shoe), each with a shoe picker (active shoes only) and RPE and feel controls.
  An activity's shoe is the shoe whose activity list contains it (CONTRACT.md: not `activity gear` once per activity).
Display units follow `data/settings-current`.
The pickers and the weigh-in form come with PR B; with A alone the carousel is read-only.

## Default recipe (v1)

```json
{"version": 1, "datasets": [
  {"id": "load", "command": "load", "argv": ["--rows"], "columns": {"table": "table", "...": "every column of the load rows"}},
  {"id": "rhr", "command": "wellness sleep-range", "argv": ["--days", "112"],
   "columns": {"date": "date", "rhr": "rhr"}},
  {"id": "weight", "command": "log weight list", "argv": ["--days", "112"], "rows": "dailyWeightSummaries",
   "columns": {"date": "summaryDate", "weight_g": "latestWeight.weight"}},
  {"id": "race_predictions", "command": "training race-predictions", "argv": ["--days", "112"],
   "columns": {"date": "calendarDate", "t5k_s": "time5K", "t10k_s": "time10K",
               "half_s": "timeHalfMarathon", "marathon_s": "timeMarathon"}},
  {"id": "fitness_age", "command": "training fitness-age", "argv": ["--days", "112", "--every", "7"],
   "columns": {"date": "date", "fitness_age": "fitnessAge", "achievable": "achievableFitnessAge",
               "age": "chronologicalAge"}},
  {"id": "shoes", "command": "gear list",
   "columns": {"uuid": "uuid", "name": "customMakeModel", "type": "gearTypeName", "status": "gearStatusName"}},
  {"id": "shoe_activities", "command": "gear activities", "argv": ["{uuid}"],
   "foreach": {"dataset": "shoes", "limit": 20},
   "columns": {"activity_id": "activityId", "start": "startTimeLocal"}},
  {"id": "activities", "command": "activity list", "argv": ["--limit", "15"],
   "columns": {"id": "activityId", "name": "activityName", "type": "activityType",
               "start": "startTimeLocal", "duration_s": "duration", "distance_m": "distance",
               "avg_hr": "averageHR", "elev_gain_m": "elevationGain", "avg_speed_mps": "averageSpeed"}},
  {"id": "activity_effort", "command": "activity show", "argv": ["{id}"],
   "foreach": {"dataset": "activities", "limit": 15}, "columns": {"rpe": "rpe", "feel": "feel"}}
]}
```

The paths above match what the commands return today (`activity list` already flattens `activityType` to its key; race predictions over a range return `calendarDate` rows), except the `load` rows mode (Endpoint changes).
`shoe_activities` runs over every shoe in `gear list`, retired ones included, so an old activity still shows its shoe; `gear activities` returns up to 1000 rows per shoe, which is why only two columns are kept.
The builder checks each path against real output and fixes the default recipe accordingly.

## Testing

Cover these cases; plain wiring needs no tests:
- Recipe validation: each rule of the table, duplicate ids, `foreach` before its parent, placeholders outside `foreach` or naming a column the parent lacks, unknown keys.
- The allowlist: a write command, a local-writing command (`zones estimate`), an unmarked command, `diary show`, and a refused flag on an exportable command (`load --export`, `--no-cache`) are refused.
- Extraction: `rows` and `columns` paths on lists, objects, nested lists and missing keys.
- `--every` sampling (grid on the day ordinal, range edges, last day always included, stable from one day to the next) and `foreach` (placeholder fill, `limit`, `parent.*` columns, one child failing).
- The limiter across event loops: two commands in one process, the first one contending for the semaphore.
- Budget: a call past `--max-calls`, or a limiter wait past the deadline, fails `deferred` without calling Garmin; a deferred dataset keeps its previous section; no budget outside the export.
- Stale fallback: a failing dataset keeps the previous section; failed days are filled from the previous bundle by date; no previous bundle; `auth_required` writes nothing; atomic replace.
- Requests: each validation rule refuses with a reason; the applied log skips a repeat; `weight.add` finds an existing entry and does not write; Garmin error codes map to `refused` or `failed`; the third failure becomes `refused`; `shoe.set` links first and unlinks only other shoes; `--dry-run` writes nothing and logs nothing.

## Left to the builder

- How exportable commands and refused flags are declared in the code.
- The exact names of the budget objects and of the race-predictions dated kind.
- Bundle `meta` details beyond the fields above.
