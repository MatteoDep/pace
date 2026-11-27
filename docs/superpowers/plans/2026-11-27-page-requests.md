# Page requests (PR B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `pace requests apply FILE [--dry-run]` applies the shoe, RPE/feel and weigh-in requests the athlete pressed on the dashboard, each checked in code, at most once, plus the coach-side procedure.

**Architecture:** The three write commands' `WriteSpec` builders move to one module, so the requests run the exact payloads and cache invalidation of `gear link/unlink`, `activity effort` and `log weight add`.
A new module validates each request (pure checks, then Garmin reads), skips what its applied log already holds, runs the writes through `run_write` inside a call and time budget (the export's `pace.ratelimit.budget`), and appends every outcome to `$PACE_HOME/requests-applied.jsonl` under a file lock.
The command is a thin wrapper that reads the file, checks login and runs inside `pace.auth.noninteractive()`.

**Tech Stack:** Python 3.14, Typer/click, pytest (pytest-asyncio), garminconnect 0.2.38.

**Spec:** `docs/superpowers/specs/2026-11-26-dashboard-recipes-design.md` (Part 2, the PR B items of Part 3, and the Requests line of Testing).

## Global Constraints

- Work in the worktree `~/projects/trainscout-wt/page-requests` (branch `page-requests`); never in `~/projects/trainscout`, which the installed `pace` and the coach skill use live.
- Run tests with `~/projects/trainscout/.venv/bin/python -m pytest` from the worktree root; never `uv sync`.
- Every pace call prints exactly one JSON envelope on stdout; nothing else goes to stdout.
- Code reads `pace.config.PACE_HOME` at call time (tests monkeypatch it), never a copy taken at import.
- No real Garmin ids in code, tests or docs: use synthetic ids (`12345678901`, uuid `0123456789abcdef0123456789abcdef`).
- Docs in markdown: one sentence per line; ASCII only.
- Keep `docs/CONTRACT.md`, `pace schema`, `README.md` and the skill files in step with the command in the same task; `tests/test_skill.py` requires every write command in SKILL.md's `## Writes` and every command and group in `reference.md`, and caps SKILL.md at 250 lines (225 today).
- Commits: conventional prefix, no Claude attribution or trailers.
- Values from the spec: ops `shoe.set` (`activity_id`, `gear_uuid`), `effort.set` (`activity_id`, `rpe` and/or `feel`), `weight.add` (`kg`, `date`); `kg` 25-250; `date` at most 365 days back; `rpe` integer 1-10; feel `very-weak`, `weak`, `normal`, `strong`, `very-strong`; weigh-in match within 100 g and a manual source; applied log `$PACE_HOME/requests-applied.jsonl`; refused codes `rejected`, `not_found`, `endpoint_unavailable` (plus validation, `bad_args`); failed codes `network`, `rate_limited`, `internal`; the third failed attempt becomes refused "gave up after 3 attempts".

## Decisions left to the builder by the spec, made here

- Store collection: `data/requests/pending` (three segments, a collection; named like `data/settings-pending`).
- Module names avoid `requests` (the HTTP library pace imports): logic in `pace/pagerequests.py`, command in `pace/commands/requests_cmd.py`, registered as group `requests`.
- Output `data` is the list of per-request results, each `{"id", "version", "op", "status", "code", "reason", "did"}`; `code` (the error code or null), `version` (echoed for the coach's pinned delete) and `op` are additions to the spec's four keys.
- `--dry-run` reads the applied log (no lock, no write), makes the validation reads (activity, gear list, activity gear, the day's weigh-ins) and writes nothing to Garmin or to the log; a request that would write gets status `planned` with the dry-run payloads in `did`; one that needs no write gets the same `applied` it would get live.
- Cost bound: the run sits in `pace.ratelimit.budget(MAX_CALLS=30, MAX_SECONDS=240)`, the export's budget, instead of a request cap.
  Cache hits are free; past the budget, and after a `rate_limited`, a `network` error with no HTTP status or a failed token login (the budget's stop rule in `pace.api`), calls fail `deferred` without reaching Garmin.
  A request that meets `deferred` is `failed` with reason "not attempted: ..." and is neither logged nor counted, so a page bug that floods the collection cannot spend the day's 500 calls and an outage does not burn attempts.
- Attempts: a logged `failed` line counts toward the three, except `rate_limited` and `network` with no HTTP status (Garmin throttled or did not answer, which says nothing about the request); the log line carries `http` for this.
- Same target after a failure: once a request fails in a run, later requests in that run for the same target are `failed` "not attempted: an earlier request for the same target failed in this run", not logged.
  Targets: `shoe.set` -> `("gear", activity_id)`; `effort.set` -> `("rpe", activity_id)` and/or `("feel", activity_id)`; `weight.add` -> `("weight", date)`.
  So a failed older press is never retried after a newer one landed, and the final state is the newest press once both apply.
- `weight.add` double press: a second `weight.add` with the same content hash in one run is `applied`, reason "duplicate of <id>", with no read or write (the day view may lag a write made seconds before).
  Only `weight.add` is deduplicated: for the state ops, an identical later press can follow a different one (rpe 5, 7, 5) and must still run.
- `at` only orders: requests whose `at` parses as an ISO datetime with a timezone sort by it (stable); the others keep their input order after them; a bad `at` is never a refusal (the spec does not validate it, and a refusal deletes the press).
- `date` of `weight.add`: not after `today + 1 day` (the page's local date may be ahead of the host's), not before `today - 365 days`.
- `auth_required` from any call ends the run: the command fails with `auth_required` (exit 3); outcomes already logged stay logged, so the next run skips them.
- Any other exception inside one request is `failed` with code `internal` (counted), and the run goes on, so the results of the writes already made are not lost.
- Unknown top-level keys of a request are ignored (the coach copies store documents); unknown `params` keys are refused.
- `id` must match `[A-Za-z0-9_-]{1,128}`; a request without a valid `id` is refused and echoed with `id: null`. Two requests with the same `id` in one file: the later one is refused "duplicate id in this file".
- Reasons quote untrusted values only through `repr()` cut to 80 characters (the coach reads them).
- Pure validation and the log check run before any Garmin read, so a request already applied costs no call.
- `effort.set` always writes (a cached `activity show` may be stale); `shoe.set` skips the link when the shoe is already linked and unlinks only other gear whose `gearTypeName` is `Shoes`.
- `shoe.set` reads the shoe list from the `gear list` cache; when the uuid is not an active shoe there, it reads the list once more with `refresh=True` before refusing (a shoe added in the app today).
- The weigh-in duplicate check reads the day view `get_daily_weigh_ins` uncached (a write whose response was lost never invalidated the cache), with the same rule `log weight add` uses to find its own entry.
- `_DETAIL_TTL` and `_GEAR_TTL` in `pace/commands/activity.py` become public (`DETAIL_TTL`, `GEAR_TTL`), since the requests read the same keys with the same TTLs.

## Review Focus

- A retry after a lost response for `weight.add`: the day already has the manual entry, so no second weigh-in is written (Task 2 `test_weight_add_existing_entry_writes_nothing`).
- `shoe.set` on an activity with a bike or other non-shoe gear linked: that gear stays linked (Task 2 `test_shoe_set_links_first_then_unlinks_other_shoes_only`).
- An older press that failed, then a newer press for the same activity field: the newer value is what Garmin ends with (Task 2 `test_failure_blocks_later_requests_for_the_same_target`).
- A torn last line in the applied log (a crash mid-append): it still loads, and the next append starts on its own line (Task 2 `test_log_torn_line_read_and_append`).
- A request value of the wrong JSON type or out of range (`true` for `rpe`, `"5"` for `activity_id`, `NaN` or `10**400` for `kg`, non-ASCII digits in `date`): refused before any Garmin call (Task 2 `test_validation_refuses`).

---

### Task 1: Shared write specs for gear link/unlink, activity effort and weigh-ins

**Files:**
- Create: `pace/writespecs.py`, `tests/test_writespecs.py`
- Modify: `pace/commands/gear.py`, `pace/commands/activity.py` (`effort`; rename `_DETAIL_TTL`, `_GEAR_TTL` to `DETAIL_TTL`, `GEAR_TTL`), `pace/commands/log.py` (`weight_add`, `_resolve_weight_id`)
- Test: existing `tests/test_gear_cmd.py`, `tests/test_log_cmd.py` and the `activity effort` tests must pass unchanged

**Interfaces:**
- Produces (in `pace/writespecs.py`):
  - `gear_link_spec(verb: str, activity_id: int, uuid: str) -> WriteSpec`: `verb` is `link` or `unlink` (methods `add_gear_to_activity`, `remove_gear_from_activity`); payload, echo and invalidation exactly as `gear.py` builds them today. `uuid` is already normalized.
  - `effort_spec(activity_id: int, rpe: int | None, feel: str | None) -> WriteSpec`: `rpe` 1-10 and `feel` a canonical label, both already checked; body `{"activityId", "summaryDTO"}` with `directWorkoutRpe = rpe * 10` and `directWorkoutFeel = FEEL_LEVELS[feel]`, only for the given fields; echo, path and invalidation as today.
  - `weigh_in_spec(kg: float, iso_date: str, extras: dict[str, float] | None = None, echo: dict[str, Any] | None = None) -> WriteSpec`: timestamp `f"{iso_date}T12:00:00"`; `add_weigh_in` with `{"weight", "unitKey": "kg", "timestamp"}` when `extras` is empty, else `add_body_composition`; echo defaults to `{"weight_kg": kg, "date": iso_date}`; invalidation and `id_of` as today.
  - `WEIGHT_MATCH_G = 100` and `manual_weigh_in_ids(day_view: Any, kg: float) -> list[int | float]`: the samplePks of the day view's `dateWeightList` entries within 100 g of `kg` whose `sourceType` is absent or `MANUAL` (the rule `_resolve_weight_id` applies today); never raises on odd shapes.
- `_resolve_weight_id` keeps its try/except and returns `max(manual_weigh_in_ids(...))` or None.

- [ ] **Step 1: Write `tests/test_writespecs.py`**: `test_manual_weigh_in_ids` (entries 66.30 kg MANUAL -> kept, 66.45 kg -> dropped as 150 g off, a `SCALE` source -> dropped, no `sourceType` -> kept, a non-dict entry and a missing `samplePk` -> skipped; `None` and `{}` day views -> `[]`), and `test_effort_spec_only_given_fields` (`effort_spec(9, 6, None)` body `summaryDTO == {"directWorkoutRpe": 60}`; `effort_spec(9, None, "strong")` -> `{"directWorkoutFeel": 75}`).
- [ ] **Step 2: Run them and see them fail** (`pace.writespecs` missing).
- [ ] **Step 3: Create `pace/writespecs.py`, switch the three commands to it, rename the two TTLs**; CLI parsing (`parse_rpe`, `parse_feel`, `validate_uuid`, `_check`, `_noon`) stays in the commands.
- [ ] **Step 4: Run the full suite**: all pass, with the gear, log and effort command tests unchanged.
- [ ] **Step 5: Commit** `refactor: share the gear, effort and weigh-in write specs`.

### Task 2: Request validation, applied log and the three operations

**Files:**
- Create: `pace/pagerequests.py`, `tests/test_pagerequests.py`

**Interfaces:**
- Consumes: Task 1 builders, `pace.writes.run_write`, `pace.commands._helpers.cached_call`, `pace.factory.resolve_upn` and `today_ttl` with the registry spec of `gear list`, `pace.commands.activity.DETAIL_TTL` and `GEAR_TTL`, `pace.ratelimit.budget`, `pace.store.locked`, `pace.ids.validate_uuid`, `pace.effort.FEEL_LEVELS`.
- Produces (in `pace/pagerequests.py`):
  - Constants: `OPS = ("shoe.set", "effort.set", "weight.add")`, `PARAMS: dict[str, tuple[str, ...]]` (allowed keys per op), `KG_RANGE = (25, 250)`, `MAX_AGE_DAYS = 365`, `MAX_ATTEMPTS = 3`, `MAX_CALLS = 30`, `MAX_SECONDS = 240`, `REFUSED_CODES = ("bad_args", "rejected", "not_found", "endpoint_unavailable")`, `STATUSES = ("applied", "refused", "failed", "planned")`, `LOG_NAME = "requests-applied.jsonl"`, `COLLECTION = "data/requests/pending"`, `ID_PATTERN = r"[A-Za-z0-9_-]{1,128}"`.
  - `log_path() -> Path`: `config.PACE_HOME / LOG_NAME`, read at call time.
  - `content_hash(op: Any, params: Any) -> str`: sha256 hex of `json.dumps({"op": op, "params": params}, sort_keys=True, separators=(",", ":"))`.
  - `check(req: Any, today: date) -> tuple[str, dict[str, Any]]`: pure validation, no network; returns `(op, params)` with `gear_uuid` normalized, or raises `PaceError("bad_args", reason)` naming the key (values quoted with `repr()` cut to 80 characters). Rules: `req` is an object; `id` matches `ID_PATTERN`; `op` in `OPS`; `params` an object whose keys are a subset of `PARAMS[op]` and include the required ones; `activity_id` an int (not bool), `0 < id < 2**63`; `gear_uuid` a string passing `validate_uuid`; `rpe` an int (not bool) 1-10; `feel` exactly one of the five labels; `effort.set` has `rpe` or `feel`; `kg` an int or float (not bool) within `KG_RANGE`, a float also finite (range check first: `math.isfinite` overflows on a huge int); `date` a string matching `[0-9]{4}-[0-9]{2}-[0-9]{2}` in full, then `date.fromisoformat`, between `today - 365 days` and `today + 1 day`.
  - `async def apply_requests(reqs: list[Any], *, dry_run: bool, today: date) -> list[dict[str, Any]]`.
- `apply_requests` flow:
  1. Order: requests whose `at` parses as an ISO datetime with a timezone sort by it (stable); the others keep their input order after them.
  2. Load the log: per `(id, hash)`, whether an `applied` line exists and how many counted `failed` lines there are. Unparsable lines are skipped (logged with `--verbose`). Without `dry_run`, hold `store.locked(log_path())` for the whole run; before the first append, when the file is not empty and does not end in `\n`, write `\n`.
  3. The rest of the run is inside `ratelimit.budget(MAX_CALLS, MAX_SECONDS)`, with one `Cache` for the run.
  4. Per request, in order: `check` (refused on `bad_args`); duplicate id in this file (refused); an `applied` log line with this `(id, hash)` -> `applied`, reason "already applied", no call, not logged again; `MAX_ATTEMPTS` counted failures already logged -> `refused`, reason "gave up after 3 attempts", no call; a target already failed in this run -> `failed` "not attempted", not logged; a `weight.add` whose hash an earlier request of this run had -> `applied` "duplicate of <id>", not logged; else run the op.
  5. Op reads: `get_activity` (cached, `DETAIL_TTL`, the `activity show` key) for both activity ops; for `shoe.set`, the active shoes from `get_gear` (cached, `resolve_upn` prefix, the `gear list` key and ttl), read once per run, refreshed once when the uuid is missing, and `get_activity_gear` with `refresh=True` (`GEAR_TTL`); for `weight.add`, `get_daily_weigh_ins` with no cache.
  6. A `PaceError` from a read or a write: `auth_required` is re-raised (ends the run); `deferred` -> `failed` "not attempted: <message>", not logged; a code in `REFUSED_CODES` -> `refused`; any other -> `failed`, and `refused` with reason "gave up after 3 attempts: <message>" when it is the third counted failure. Any other exception -> `failed`, code `internal`. `did` keeps the writes that landed before the error. A `failed` request adds its targets to the run's failed targets.
  7. Without `dry_run`, append one line per outcome that ran (`applied` from the op, `refused`, `failed`; not the skips): `{"id", "hash", "op", "status", "code", "http", "reason", "time"}` (`time` UTC ISO), with `O_APPEND`, mode 0600, flushed and fsynced per line.
  8. Result per request: `{"id", "version", "op", "status", "code", "reason", "did"}`; `reason` is the error message cut to 300 characters, or the skip reason, or null; `did` is the list of `run_write` results (dry-run payloads with `--dry-run`); with `--dry-run`, a request with a non-empty `did` is `planned`.
- Ops:
  - `shoe.set`: the activity exists; `gear_uuid` is in `gear list` with `gearTypeName == "Shoes"` and `gearStatusName == "active"` (uuids compared lowercased; else `bad_args` "not an active shoe"); read the activity's gear (a non-list answer is an empty list); link the shoe unless already linked; then unlink each other linked item whose `gearTypeName` is `Shoes`, in the order Garmin lists them; non-shoe gear is never touched.
  - `effort.set`: the activity exists; `effort_spec`.
  - `weight.add`: read the day view; `manual_weigh_in_ids` non-empty -> `applied`, reason "already recorded on <date>", no write; else `weigh_in_spec(kg, date)` (the created id is not resolved: no extra read).

- [ ] **Step 1: Write the failing tests** in `tests/test_pagerequests.py`, with a fake `safe_call` patched into `pace.writes`, `pace.commands._helpers` and `pace.pagerequests` that answers per method from a dict and records every call, and `PACE_HOME`/`CACHE_DB` on `tmp_path`:
  - `test_validation_refuses` (parametrized: each rule above, including `rpe: true`, `rpe: 6.0`, `activity_id: "5"`, `activity_id: 2**63`, `kg: float("nan")`, `kg: 10**400`, `kg: 24.9`, `kg: 250.5`, `feel: "Strong"`, `date: "today"`, `date` with Arabic-Indic digits, `date` two days ahead, `date` 366 days back, missing `id`, `id` with a space, a list instead of an object, an extra `params` key, `effort.set` with neither field): status `refused`, code `bad_args`, the key name in `reason`, and no Garmin call.
  - `test_valid_edges_pass`: `kg` 25 and 250, `date` tomorrow and 365 days back, `rpe` 1 and 10, extra top-level keys ignored, a missing or naive `at`.
  - `test_reads_use_the_cli_cache_keys`: after a `shoe.set` and an `effort.set`, the cache holds `get_activity:<id>`, `get_activity_gear:<id>` and `get_gear:<upn>`.
  - `test_activity_not_found_is_refused`, `test_shoe_not_active_is_refused` (a retired shoe, an active non-shoe item, an unknown uuid; the unknown uuid causes exactly one refreshed `get_gear` read), `test_new_shoe_found_on_refresh`.
  - `test_shoe_set_links_first_then_unlinks_other_shoes_only`: activity gear `[other shoe, bike]` -> calls in order `add_gear_to_activity(target)`, `remove_gear_from_activity(other shoe)`; the bike is never unlinked; `did` has two items.
  - `test_shoe_set_already_linked_writes_nothing`: gear `[target]` -> `applied`, `did == []`.
  - `test_shoe_set_unlink_failure_then_retry`: unlink answers `network` with HTTP 503 -> `failed`, `did` = the link; second run with gear `[target, other]` -> only the unlink is sent, `applied`.
  - `test_effort_set_writes_given_fields`: `rpe` 6 and `feel` `strong` -> one `connectapi` PUT with `summaryDTO == {"directWorkoutRpe": 60, "directWorkoutFeel": 75}`.
  - `test_weight_add_existing_entry_writes_nothing`: a MANUAL entry 50 g off -> `applied`, reason mentions the date, no `add_weigh_in`; `test_weight_add_writes_when_only_scale_or_far` (a SCALE entry, or 150 g off -> one `add_weigh_in` with timestamp `<date>T12:00:00`); `test_weight_add_day_view_failure_writes_nothing` (`network` on the day view -> `failed`, no write); `test_weight_add_double_press_in_one_run` (two ids, same content -> one day-view read, one write, the second `applied` "duplicate of <first id>").
  - `test_applied_log_skips_repeat`: the same file twice -> second run `applied`, reason "already applied", zero Garmin calls; `test_same_id_new_content_runs_again`.
  - `test_error_codes_map` (parametrized): `rejected`, `not_found`, `endpoint_unavailable` -> `refused`; `network` (with and without status), `rate_limited`, `internal` -> `failed`; `deferred` -> `failed`, not logged.
  - `test_third_failure_becomes_refused`: three runs with `network` HTTP 503 -> `failed`, `failed`, `refused` ("gave up after 3 attempts"); a fourth run -> `refused` with no call. `test_throttle_and_no_answer_do_not_count`: three runs with `rate_limited`, then with `network` and no status -> still `failed`, never `refused`.
  - `test_failure_blocks_later_requests_for_the_same_target`: `effort.set` rpe 5 (earlier `at`) fails with HTTP 503, `effort.set` rpe 7 for the same activity -> `failed` "not attempted", no call, not logged; an `effort.set` feel-only request for the same activity and one for another activity still run.
  - `test_unexpected_exception_is_internal`: the fake raises `KeyError` for one request -> `failed`, code `internal`; the next request still runs.
  - `test_dry_run_writes_nothing_and_logs_nothing`: `planned`, `did` holds `{"dry_run": true, "payload": ...}` items, no write method called, the log file does not exist; `test_dry_run_honours_the_log` (a logged request -> `applied` "already applied", no call).
  - `test_at_order`: requests with `at` out of order run in `at` order; one with a bad `at` runs last and is not refused for it.
  - `test_auth_required_ends_the_run`: the second request's read raises `auth_required` -> `apply_requests` raises it; the log holds only the first request's line.
  - `test_duplicate_id_in_file_refused`, `test_log_torn_line_read_and_append` (a log ending in a half line still skips an applied request, and the next append starts on a new line that parses).
- [ ] **Step 2: Run them and see them fail** (module missing).
- [ ] **Step 3: Implement `pace/pagerequests.py`.**
- [ ] **Step 4: Run the new tests, then the full suite.**
- [ ] **Step 5: Commit** `feat: validate and apply dashboard page requests`.

### Task 3: `pace requests apply`, schema, contract and coach procedure

**Files:**
- Create: `pace/commands/requests_cmd.py`, `tests/test_requests_cmd.py`
- Modify: `pace/cli.py`, `pace/commands/schema.py`, `pace/envelope.py` (the `deferred` hint names `requests apply` too), `docs/CONTRACT.md`, `README.md`, `.claude/skills/pace/SKILL.md`, `.claude/skills/pace/dashboard.md`, `.claude/skills/pace/reference.md`, `tests/test_skill.py`

**Interfaces:**
- Consumes: `apply_requests`, the Task 2 constants, `read_json`, `pace.auth.auth_status`, `pace.auth.noninteractive`.
- Produces: `pace requests apply FILE [--dry-run]` (FILE a path or `-` for stdin); envelope `args = {"file", "dry_run"}`; `data` = the result list.
  Not a JSON list -> `bad_args`; an empty list -> `ok` with `[]` and no login check; no tokens -> `auth_required` before any call; the run sits inside `noninteractive()`, so rejected tokens are `auth_required` and never a prompt; `today` is `date.today()`.
- Schema: a `requests apply` entry (`keyed: none`, `args: ["FILE"]`, `flags: ["--dry-run"]`, `write: true`, help naming the ops, statuses, log, budget and dry-run reads); group `requests` in `groups` and in `schema`'s known groups; a top-level `requests` block from the Task 2 constants (`ops` with their params, `collection`, `log_path`, `max_attempts`, `max_calls`, `max_seconds`, `kg_range`, `max_age_days`, `refused_codes`, `statuses`); `WRITES` gains the `requests apply` dry-run reads in `dry_run`, its result list in `result`, the log skip and weigh-in check in `idempotency`, and in `confirm` the page-request exception (a press on the private dashboard, which showed the planned write, is the yes).

- [ ] **Step 1: Write the failing tests** in `tests/test_requests_cmd.py`: invalid JSON and a non-list -> exit 2 `bad_args`; `[]` -> `ok`, `data == []`, `auth_status` not called; no tokens -> exit 3 `auth_required`, no Garmin call; one `effort.set` through `CliRunner` with a fake API -> `data[0]["status"] == "applied"`; stdin via `-`; `build_schema(None)` has `requests apply` with `write: true` and a `requests` block equal to the module constants; `pace schema requests` is accepted. In `tests/test_skill.py`, `test_requests_flow`: `dashboard.md` names `data/requests/pending`, `pace requests apply` and "pinned"; SKILL.md `## Writes` names `pace requests apply` and the press as the yes.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement the command and the schema; register the group in `pace/cli.py`.**
- [ ] **Step 4: Docs:**
  - `docs/CONTRACT.md`: `### Writes` (fifteen commands; the dry-run sentence gains the `requests apply` reads; "Write commands themselves never read the cache" gains the `requests apply` validation reads; the idempotency paragraph names the applied log and the weigh-in check; the confirmation exception); the `deferred` row of the error table names the requests budget; a new `## Page requests` section before "Keeping this file honest": input, ops, validation, order, targets, log, attempts, statuses, budget, dry-run, `auth_required`.
  - `README.md`: `pace requests apply requests.json --dry-run` in Use; the confirmation sentence names the page-request exception.
  - `reference.md`: the row `` | requests | `pace requests apply` | none | (G) Apply the athlete's dashboard requests: shoe, RPE/feel, weigh-in. | `` after `profile`.
  - `SKILL.md` (stay at or under 250 lines): Session start applies page requests when `dashboard.url` is set, before the run review (dashboard.md, Requests); `## Writes` lists `pace requests apply` with the press-is-the-yes exception.
  - `dashboard.md`: a `## Requests` section: collection `data/requests/pending`, the document shape per op for the page (`at` from `new Date().toISOString()`, SI numbers), the page shows the planned write before the press and the pending request after it; the coach flow (list, scratch file of `{"id", "version", "at", "op", "params"}`, `pace requests apply` with a 5-minute timeout, delete `applied` and `refused` pinned to the version read, keep `failed`, one line to the athlete, refresh); `internal` "busy" means another apply is running (leave the requests for the next session); after `auth_required` the next run reports the already applied ones as applied; the private-dashboard condition; and `## Unattended runs` gains the permissions for it.
- [ ] **Step 5: Run the full suite; ASCII and one-sentence-per-line check on the touched docs.**
- [ ] **Step 6: Commit** `feat: pace requests apply` and `docs: page requests in the contract and the coach skill`.
