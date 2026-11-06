# Gear Links and Effort Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `pace` link/unlink gear on one activity and set RPE/feel on one activity, plus the reads needed to review activities one by one.

**Architecture:** Two new writes in the existing `gear` group (`link`, `unlink`) and one in `activity` (`effort`), all through `pace/writes.py` `run_write`.
A small pure module `pace/effort.py` owns the RPE/feel scale mapping, used by both the write and the `activity show` read.
Reads: `activity gear` (hand-written, via `_by_id`), `gear activities` (a `pace.spec.REGISTRY` entry), and derived `rpe`/`feel` keys on `activity show`.

**Tech Stack:** Python 3.14, typer, garminconnect 0.2.38 (`add_gear_to_activity`, `remove_gear_from_activity`, `get_activity_gear`, `get_gear_activities`, `connectapi`), pytest + typer `CliRunner`.

**Spec:** `docs/superpowers/specs/2026-11-06-gear-and-effort-design.md`, extending `docs/superpowers/specs/2026-09-04-llm-cli-design.md` section 7.

**Branch:** create `pace-2.2-gear-effort` from `pace-2.1-mutations` (the base is unmerged; do not branch from `master`).

## Global Constraints

- stdout = exactly one compact JSON envelope; error codes unchanged (`bad_args` exit 2).
- Every write: `--dry-run` (no network, `data: {"dry_run": true, "payload": ...}`), never reads the cache, invalidates affected keys on success, no `--yes`.
- New result values: `action` adds `update | link | unlink`; `resource` adds `activity_gear | activity_effort`.
  `gear link` -> `link`/`activity_gear`; `gear unlink` -> `unlink`/`activity_gear`; `activity effort` -> `update`/`activity_effort`; `id` is `null` for all three.
- RPE user scale integer 1..10, stored x10.
  Feel labels -> stored: `very-weak` 0, `weak` 25, `normal` 50, `strong` 75, `very-strong` 100.
- No other activity field is ever written; never send the whole `summaryDTO` back.
- No new dependencies. ASCII only; markdown newline after each period. No Claude attribution in commits. Tests `.venv/bin/python -m pytest -q`; `.venv/bin/ruff check pace tests scripts` clean at every commit.
- Live writes only in Task 6, only after the user's explicit go-ahead, on an activity the user picks.

## Review Focus

1. A gear uuid that is not 32 lowercase hex chars (pasted with dashes, a name instead of a uuid, `../x`) must be `bad_args` before any network call, since it is interpolated into a URL path (Task 4).
2. An activity id <= 0 must be `bad_args`, not a library `ValueError` surfacing as a generic error (Tasks 2 and 3).
3. `activity effort` with neither flag, `--rpe 0`, `--rpe 11`, or an unknown feel label must be `bad_args`; feel labels match case-insensitively and accept `_` for `-` (Task 2).
4. `activity show` on an activity with no `summaryDTO` or no RPE/feel keys (manual entries, multisport parents) must not crash; `rpe`/`feel` are then absent, because shaping prunes `None` (`pace/shape.py:30`) and absent already means unset everywhere in the CLI (Task 2).
5. Stored values off the grid (RPE 45, feel 60) must read as `4.5` and no `feel` key (mapper returns `None`, pruned by shaping), not raise (Task 1).

## File Map

| Path | Responsibility |
|---|---|
| `pace/effort.py` | scale mapping: `FEEL_LEVELS`, `parse_rpe`, `parse_feel`, `rpe_from_stored`, `feel_from_stored`, `with_effort` |
| `pace/commands/activity.py` | `_by_id` gains `default_fields`; `activity gear`; derived keys on `show`; `activity effort` |
| `pace/commands/gear.py` | `link`, `unlink` added to the generated app; `validate_uuid` |
| `pace/spec.py` | `gear activities` EndpointSpec |
| `pace/commands/schema.py`, `docs/CONTRACT.md`, `README.md`, parent spec section 7 | docs and manifest |
| `tests/test_effort.py`, `tests/test_activity.py`, `tests/test_gear_cmd.py`, `tests/test_schema.py` | tests |

---

### Task 1: Effort scale mapping `pace/effort.py`

**Files:**
- Create: `pace/effort.py`
- Test: `tests/test_effort.py`

**Interfaces:**
- Produces:
  - `FEEL_LEVELS: dict[str, int]` (the five labels above, in order).
  - `parse_rpe(value: int) -> int` -> stored value (x10); `PaceError("bad_args")` outside 1..10.
  - `parse_feel(label: str) -> tuple[str, int]` -> (canonical label, stored); case-insensitive, `_` == `-`; `PaceError("bad_args")` naming the valid labels.
  - `rpe_from_stored(v: Any) -> int | float | None`: `None`/non-number -> `None`; multiple of 10 -> int `v // 10`; else `v / 10`.
  - `feel_from_stored(v: Any) -> str | None`: exact stored value -> label, else `None`.
  - `with_effort(activity: Any) -> Any`: if `activity` is a dict, return a shallow copy with top-level `rpe` and `feel` from `summaryDTO.directWorkoutRpe` / `directWorkoutFeel` (missing `summaryDTO` -> both `None`); non-dict returned unchanged.

- [ ] **Step 1: Write failing tests** in `tests/test_effort.py`

```python
def test_parse_rpe_bounds():
    assert parse_rpe(1) == 10 and parse_rpe(10) == 100
    for bad in (0, 11, -3):
        with pytest.raises(PaceError) as e:
            parse_rpe(bad)
        assert e.value.code == "bad_args"

def test_parse_feel_labels_and_aliases():
    assert parse_feel("strong") == ("strong", 75)
    assert parse_feel("Very_Weak") == ("very-weak", 0)
    with pytest.raises(PaceError) as e:
        parse_feel("tired")
    assert e.value.code == "bad_args" and "very-strong" in str(e.value)

def test_from_stored_on_and_off_grid():
    assert rpe_from_stored(40) == 4 and isinstance(rpe_from_stored(40), int)
    assert rpe_from_stored(45) == 4.5
    assert rpe_from_stored(None) is None and rpe_from_stored("x") is None
    assert feel_from_stored(75) == "strong" and feel_from_stored(60) is None and feel_from_stored(None) is None

def test_with_effort():
    a = {"activityId": 1, "summaryDTO": {"directWorkoutRpe": 40, "directWorkoutFeel": 75}}
    out = with_effort(a)
    assert out["rpe"] == 4 and out["feel"] == "strong" and "rpe" not in a
    assert with_effort({"activityId": 2}) == {"activityId": 2, "rpe": None, "feel": None}
    assert with_effort([1]) == [1]
```


- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_effort.py -q`; expected: FAIL (module missing).
- [ ] **Step 3: Implement** `pace/effort.py` with the Interfaces above.
- [ ] **Step 4: Run** the same command; expected: PASS. Ruff clean.
- [ ] **Step 5: Commit** `feat: RPE/feel scale mapping`.

### Task 2: `activity gear`, derived effort on `activity show`, `activity effort`

**Files:**
- Modify: `pace/commands/activity.py` (`_by_id` at ~line 108, registrations at ~135)
- Test: `tests/test_activity.py`

**Interfaces:**
- Consumes: Task 1 (`parse_rpe`, `parse_feel`, `with_effort`); `WriteSpec`, `run_write`, `DRY_RUN_OPT` from `pace/writes.py`.
- Produces:
  - `_by_id(name, method, help_, default_fields: tuple[str, ...] = (), transform: Callable[[Any], Any] | None = None, ttl: int = _DETAIL_TTL)`; `transform` is applied to the fetched payload before shaping and skipped under `--raw`.
  - `ACTIVITY_GEAR_FIELDS = ("uuid", "customMakeModel", "gearTypeName", "gearStatusName")`.
  - `activity show` registered with `transform=with_effort`.
  - `activity gear ACTIVITY_ID` registered with `get_activity_gear`, `default_fields=ACTIVITY_GEAR_FIELDS`, `ttl=300`: gear changed in the Garmin app cannot be invalidated by pace, so the read must not serve day-old shoes during a review.
  - `activity effort ACTIVITY_ID [--rpe INT] [--feel LABEL] [--dry-run]`:
    path `/activity-service/activity/{id}`, body `{"activityId": id, "summaryDTO": {...only the given keys...}}` with keys `directWorkoutRpe` / `directWorkoutFeel`;
    `WriteSpec(resource="activity_effort", action="update", method="connectapi", args=(path,), kwargs={"method": "PUT", "json": body}, payload={"path": path, "json": body}, echo={"activity_id": id, "rpe": <1-10 or omitted>, "feel": <label or omitted>}, invalidate=(f"get_activity:{id}",))`.
    The prefix also hits ids that extend this one; over-invalidation is harmless, accept it.
    Envelope `args`: `{"activity_id", "rpe", "feel", "dry_run"}`, as `workout schedule` does.

- [ ] **Step 1: Write failing tests** (append to `tests/test_activity.py`; the existing `fake_api` patches `pace.commands._helpers.safe_call`, add `monkeypatch.setattr("pace.writes.safe_call", safe_call)` to it)

```python
def test_show_adds_effort_keys(fake_api):
    fake_api["payload"] = lambda m, a, kw: {"activityId": 5, "summaryDTO": {"directWorkoutRpe": 40, "directWorkoutFeel": 75}}
    doc = json.loads(runner.invoke(app, ["show", "5"]).stdout)
    assert doc["data"]["rpe"] == 4 and doc["data"]["feel"] == "strong"
    assert doc["data"]["summaryDTO"]["directWorkoutRpe"] == 40
    sel = json.loads(runner.invoke(app, ["show", "5", "--fields", "rpe,feel", "--no-cache"]).stdout)
    assert sel["data"] == {"rpe": 4, "feel": "strong"}
    raw = json.loads(runner.invoke(app, ["show", "5", "--raw", "--no-cache"]).stdout)
    assert "rpe" not in raw["data"]

def test_show_without_summary_omits_effort(fake_api):
    fake_api["payload"] = lambda m, a, kw: {"activityId": 6}
    doc = json.loads(runner.invoke(app, ["show", "6"]).stdout)
    assert doc["ok"] and "rpe" not in doc["data"] and "feel" not in doc["data"]

def test_activity_gear_curated(fake_api):
    fake_api["payload"] = lambda m, a, kw: [{"uuid": "a" * 32, "customMakeModel": "Altra", "gearTypeName": "Shoes",
                                             "gearStatusName": "active", "gearPk": 1, "imageNameLarge": None}]
    doc = json.loads(runner.invoke(app, ["gear", "7"]).stdout)
    assert doc["data"] == [{"uuid": "a" * 32, "customMakeModel": "Altra", "gearTypeName": "Shoes", "gearStatusName": "active"}]
    assert fake_api["calls"][0][:2] == ("get_activity_gear", (7,))

def test_effort_dry_run_sends_only_given_keys(fake_api):
    doc = json.loads(runner.invoke(app, ["effort", "9", "--rpe", "6", "--dry-run"]).stdout)
    assert doc["data"] == {"dry_run": True, "payload": {"path": "/activity-service/activity/9",
                           "json": {"activityId": 9, "summaryDTO": {"directWorkoutRpe": 60}}}}
    assert fake_api["calls"] == []

def test_effort_real_run_result_and_invalidation(fake_api, tmp_path):
    fake_api["payload"] = lambda m, a, kw: {"activityId": 9}
    runner.invoke(app, ["show", "9"])
    runner.invoke(app, ["show", "9"])
    assert [c[0] for c in fake_api["calls"]].count("get_activity") == 1  # second show was a cache hit
    doc = json.loads(runner.invoke(app, ["effort", "9", "--feel", "Very_Strong"]).stdout)
    assert doc["data"] == {"action": "update", "resource": "activity_effort", "id": None,
                           "echo": {"activity_id": 9, "feel": "very-strong"}}
    method, args, kw = fake_api["calls"][-1]
    assert method == "connectapi" and kw == {"method": "PUT", "json": {"activityId": 9, "summaryDTO": {"directWorkoutFeel": 100}}}
    runner.invoke(app, ["show", "9"])
    assert [c[0] for c in fake_api["calls"]].count("get_activity") == 2  # row was invalidated

@pytest.mark.parametrize("argv", [["effort", "9"], ["effort", "9", "--rpe", "0"], ["effort", "9", "--rpe", "11"],
                                  ["effort", "9", "--feel", "tired"], ["effort", "0", "--rpe", "5"]])
def test_effort_bad_args(fake_api, argv):
    res = runner.invoke(app, argv)
    assert res.exit_code == 2 and json.loads(res.stdout)["error"]["code"] == "bad_args" and fake_api["calls"] == []
```


- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_activity.py -q`; expected: the new tests FAIL.
- [ ] **Step 3: Implement** the Interfaces above in `pace/commands/activity.py`.
  `activity effort` validates (id > 0, at least one flag, via Task 1 parsers) inside the `try ... except PaceError: fail(cmd, err)` pattern used by `workout schedule`.
- [ ] **Step 4: Run** the full suite; expected: PASS, ruff clean.
- [ ] **Step 5: Commit** `feat: activity gear, effort read and write`.

### Task 3: Error codes for library calls that bypass `connectapi`

`add_gear_to_activity` / `remove_gear_from_activity` call `garth.put` directly (verified in garminconnect 0.2.38), so today:
a non-404 HTTP error arrives as `GarthHTTPError`, whose status lives at `err.error.response.status_code`, but `pace/api.py:88` reads `err.response` -> code `internal`, exit 1;
the library's 404 branch raises `GarminConnectConnectionError("Cannot add gear ...: gear not found ...")` with no status in the text -> code `network` ("retry once").

**Files:**
- Modify: `pace/api.py` (`safe_call`, ~line 87-98)
- Test: `tests/test_api_errors.py`

**Interfaces:**
- Produces: `safe_call` behaviour only, no new names.
  `GarthHTTPError`: status from `err.error.response.status_code`, falling back to `err.response` (keep current behaviour for any caller relying on it) and then `_status_from_text(str(err))`.
  `GarminConnectConnectionError` whose text contains `not found` and no status -> `classify_http(404, ...)`.

- [ ] **Step 1: Write failing tests** (follow the existing style in `tests/test_api_errors.py`, raising fake exceptions from a patched client method):
  a `GarthHTTPError(msg="x", error=requests.HTTPError(response=<obj with status_code=403>))` -> code `classify_http(403, ...)` gives (assert that code, not `internal`);
  the same with 404 -> `not_found`;
  `GarminConnectConnectionError("Cannot add gear u to activity 1: gear not found (likely retired/removed)")` -> `not_found`;
  `GarminConnectConnectionError("boom")` -> still `network`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_api_errors.py -q`; expected: the new tests FAIL.
- [ ] **Step 3: Implement** in `safe_call`.
- [ ] **Step 4: Run** the full suite; expected: PASS, ruff clean.
- [ ] **Step 5: Commit** `fix: map HTTP status from direct garth calls and library not-found errors`.

Known residual, settled in Task 6: both library calls end in `.json()`; an empty Garmin response body would raise `JSONDecodeError` (a `ValueError`) -> `bad_args` although the write landed.
If the live check shows an empty body, call the endpoints through `connectapi` (`PUT /gear-service/gear/link/{uuid}/activity/{id}`) instead of the library methods, in a follow-up task.

### Task 4: `gear link`, `gear unlink`, `gear activities`

**Files:**
- Modify: `pace/commands/gear.py`, `pace/spec.py` (gear section ~line 306)
- Create: `tests/test_gear_cmd.py` (copy the `_tmp_home` / `fake_api` fixtures from `tests/test_workout_cmd.py`)

**Interfaces:**
- Produces:
  - `validate_uuid(value: str) -> str` in `pace/commands/gear.py`: must match `^[0-9a-f]{32}$` after `.strip().lower()`; else `PaceError("bad_args", ...)`.
  - `gear link ACTIVITY_ID GEAR_UUID [--dry-run]` -> `WriteSpec(resource="activity_gear", action="link", method="add_gear_to_activity", args=(uuid, activity_id), payload={"method": "add_gear_to_activity", "gearUUID": uuid, "activity_id": activity_id}, echo={"activity_id": activity_id, "gear_uuid": uuid}, invalidate=(f"get_activity_gear:{activity_id}", "get_gear_activities:", "get_gear_stats:"))`.
    Whole prefixes for the gear-side keys: the factory keys reads by the uuid exactly as typed (any case), and a server-side replacement would touch another shoe's keys; dropping a handful of rows is cheap.
  - `gear unlink` identical with `remove_gear_from_activity` and `action="unlink"`.
  - `EndpointSpec("gear", "activities", "Activities a gear item was used on (newest first, up to 1000).", "get_gear_activities", TTL_LIVE, id_arg="GEAR_UUID", id_type=str, default_fields=(<the activity LIST_FIELDS subset that exists on these rows: "activityId", "activityName", "activityType.typeKey", "startTimeLocal", "distance", "duration">))`.
    No `--limit`: the library default (1000, capped) is enough for one person's gear; this drops the spec's `[--limit]` (builder call).
    Use `TTL_LIVE` (300 s), not `TTL_STATIC`: the list changes with every link from outside pace (the Garmin app), which pace cannot invalidate.

- [ ] **Step 1: Write failing tests** in `tests/test_gear_cmd.py`

```python
U = "0123456789abcdef0123456789abcdef"

def test_link_dry_run(fake_api):
    doc = _doc(runner.invoke(app, ["link", "9", U, "--dry-run"]))
    assert doc["data"]["payload"] == {"method": "add_gear_to_activity", "gearUUID": U, "activity_id": 9}
    assert fake_api["calls"] == []

def test_link_and_unlink_real(fake_api):
    for verb, method in (("link", "add_gear_to_activity"), ("unlink", "remove_gear_from_activity")):
        doc = _doc(runner.invoke(app, [verb, "9", U.upper()]))
        assert doc["data"] == {"action": verb, "resource": "activity_gear", "id": None,
                               "echo": {"activity_id": 9, "gear_uuid": U}}
        assert fake_api["calls"][-1] == (method, (U, 9), {})

def test_link_invalidates_gear_keys(fake_api):
    cache = Cache()
    for key in (f"get_activity_gear:9", f"get_gear_activities:{U.upper()}", f"get_gear_stats:{U}", "get_gear:1"):
        asyncio.run(cache.get_or_fetch(key, _ret_one, ttl=600))
    cache.close()
    runner.invoke(app, ["link", "9", U])
    cache = Cache()
    assert cache.invalidate("get_activity_gear:") == 0 and cache.invalidate("get_gear_activities:") == 0
    assert cache.invalidate("get_gear_stats:") == 0 and cache.invalidate("get_gear:1") == 1
    cache.close()

@pytest.mark.parametrize("argv", [["link", "9", "01234567-89ab-cdef-0123-456789abcdef"], ["link", "9", "../x"],
                                  ["unlink", "9", "Altra"], ["link", "0", U]])
def test_link_bad_args(fake_api, argv):
    res = runner.invoke(app, argv)
    assert res.exit_code == 2 and _doc(res)["error"]["code"] == "bad_args" and fake_api["calls"] == []

def test_gear_activities_registered(fake_api):
    fake_api["resp"] = [{"activityId": 1, "activityName": "r", "activityType": {"typeKey": "running", "typeId": 1},
                         "startTimeLocal": "2026-11-03 20:09:31", "distance": 1.0, "duration": 2.0, "calories": 3}]
    doc = _doc(runner.invoke(app, ["activities", U]))
    assert doc["data"] == [{"activityId": 1, "activityName": "r", "activityType": {"typeKey": "running"},
                            "startTimeLocal": "2026-11-03 20:09:31", "distance": 1.0, "duration": 2.0}]
```

`_ret_one` is `async def _ret_one(): return 1`; imports `asyncio`, `Cache`.

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_gear_cmd.py -q`; expected: FAIL.
- [ ] **Step 3: Implement** the Interfaces above.
- [ ] **Step 4: Run** the full suite; expected: PASS (the factory/spec tests may pin the registry size; update them), ruff clean.
- [ ] **Step 5: Commit** `feat: gear link/unlink and gear activities`.

### Task 5: Schema, contract, README, parent spec

**Files:**
- Modify: `pace/commands/schema.py`, `docs/CONTRACT.md` (Writes section ~line 134), `README.md`, `docs/superpowers/specs/2026-09-04-llm-cli-design.md` section 7, `tests/test_schema.py`

- [ ] **Step 1: Update `tests/test_schema.py::test_writes_and_dsl_in_schema`**: the write set gains `"gear link", "gear unlink", "activity effort"`; assert `"link"` in `data["writes"]["result"]["action"]` and `"activity_effort"` in `data["writes"]["result"]["resource"]`.
  `test_every_cli_command_is_in_schema` already forces `activity gear` and `activity effort` into `STATIC_COMMANDS` (gear commands come from the registry; `link`/`unlink` are hand-written, so they need static entries too).
- [ ] **Step 2: Run**; expected: FAIL.
- [ ] **Step 3: Update** `schema.py`: `STATIC_COMMANDS` entries (`activity gear` keyed `id`, `default_fields` from `ACTIVITY_GEAR_FIELDS`; three write entries with `"write": True`, `--dry-run`; `activity effort` flags `--rpe`, `--feel`), `WRITES["result"]` enums, `idempotency` text: keep the existing "not idempotent" sentence for the create writes (a test pins it) and add that link/unlink/effort set a state, so repeating them is harmless, groups help for `activity` and `gear` (the `activity` help lives in both `pace/commands/activity.py:20` and `schema.py` groups; update both).
  `activity show` entry: mention `rpe` (1-10) and `feel` (label) in its help.
- [ ] **Step 4: Update docs.**
  CONTRACT Writes: command count and list, result enums, the dry-run payload shapes for the new commands, the RPE/feel scale table, "deletes" paragraph unchanged, the activity-edit limit (only gear links and RPE/feel).
  README: the three writes and two reads.
  CONTRACT and README also carry the review recipe, so an LLM caller does not spend one `activity gear` call per activity (rate limit 10/min, `pace/ratelimit.py:18`):
  `gear list`, then `gear activities UUID` once per shoe, then `activity list` for the range; activities in no shoe's list have no gear.
  `activity gear ID` is for checking one activity after a write.
  The swap order (link-then-unlink vs unlink-then-link) is written after Task 6 settles whether Garmin keeps both shoes.
  Parent spec section 7: replace the "Permanently excluded" line with the amended text from the design doc's Constraints, and add the `action`/`resource` enum growth.
- [ ] **Step 5: Run** full suite and ruff; expected: PASS.
- [ ] **Step 6: Commit** `docs: gear links and effort in schema, contract, README, spec`.

### Task 6: Live verification (human-gated; controller runs it)

Ask the user for the go-ahead before any write.
The effort check (Step 2) runs first on a throwaway activity the user creates in the Garmin app (a short manual activity), never on a real run: if the partial PUT wipes other summary metrics, pace has no safe way to restore them (sending the whole `summaryDTO` is forbidden).
Before every write, save `pace activity show ID --raw --no-cache` to the scratchpad as the restore reference.
Record every command and result.

- [ ] **Step 1 (reads):** `pace activity show ID --fields rpe,feel,summaryDTO.directWorkoutRpe,summaryDTO.directWorkoutFeel`, `pace activity gear ID`, `pace gear activities UUID`.
- [ ] **Step 2 (effort, partial PUT), on the throwaway activity:** first write with both `--rpe` and `--feel` (closest to what the Garmin app sends), re-read with `--no-cache`, compare against the saved raw payload; then a single-field write `--rpe N` and compare again.
  Only after both pass, repeat one write on a real activity the user picks.
  PASS if RPE changed and feel plus the other metrics are unchanged; then ask the user to confirm the Garmin app shows N/10 and the expected feel label (confirms x10 and the label order).
  If feel or metrics were wiped: stop (the throwaway activity can simply be deleted in the app), and implement the read-modify-write fallback from the design doc ("Behaviours to settle live") as a new task with its own tests, then repeat this step.
- [ ] **Step 3 (gear):** on a real activity the user picks with one shoe A: `gear link ID B`, read `activity gear ID --no-cache`.
  Also note whether link/unlink returned a JSON body; an empty body surfaces as `bad_args` (see Task 3 residual) and needs the `connectapi` follow-up.
  If both A and B are present: as designed; `gear unlink ID B` and re-read.
  If A vanished: Garmin replaces same-type gear; restore A, stop, and implement the design doc's link-replacement fallback as a new task, then repeat.
- [ ] **Step 4:** write the swap order into CONTRACT/README: if both shoes stayed, link the new shoe first, then unlink the old one (a failed link never leaves the activity bare); if Garmin replaced A, `gear link` alone swaps.
  Append the live results (date, what was confirmed) to the CONTRACT Writes section, like the 2026-09-07 note; commit `docs: live verification of gear links and effort`.
- [ ] **Step 5:** restore every value changed during verification unless the user asked to keep it.

## Self-review notes

- Spec coverage: goals 1-3 -> Tasks 4, 2, 2+4; error mapping -> Task 3; result enum, validation, invalidation -> Tasks 2-5; live checks and fallbacks -> Task 6; docs -> Task 5.
- Deviation from spec, flagged: `gear activities` has no `--limit` (Task 4).
- Deviation from spec, flagged: `rpe`/`feel` are absent rather than `null` when unset (Task 2, pruning); the design doc is updated to match.
- Not done, noted: `gear stats` / `gear activities` (registry reads) do not validate the uuid; GET on the user's own data, low risk, would need a validator hook on `EndpointSpec`.
- `with_effort` is applied before shaping, which is what lets `--fields rpe,feel` select the keys; the design doc says the same.
