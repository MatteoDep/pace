# Coach 1: Athlete Diary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the coach durable memory: `pace diary` over a local JSON file, with the intake profile, decision follow-ups, refs to sessions/activities, and stored check-in state.

**Architecture:** `pace/store.py` is a small generic JSON-file store (load with corrupt detection, atomic save, exclusive lock for read-modify-write) that the diary uses now and the plan store (coach plan 2) reuses.
`pace/diary.py` holds the diary model and its operations on a plain dict; `pace/commands/diary.py` wraps them in the envelope; `pace schema` lists them.

**Tech Stack:** Python 3.14, typer, stdlib json/os/fcntl, pytest + typer `CliRunner`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-17-coach-plan-design.md` section 1 (diary), Constraints; base design in `docs/superpowers/specs/2026-09-04-llm-cli-design.md` section 8.
The old plan `docs/superpowers/plans/2026-09-06-diary-and-skill.md` (called "the old diary plan" below) was never executed; its diary tasks are superseded by this plan, its skill tasks by coach plan 4.
Read its Task 1 and Task 2 code: they show the house style for a local store and its commands, and most of it carries over.

This is plan 1 of 4: (1) diary, (2) plan store, push, status, `workout calendar`, (3) `pace load` and charts, (4) skill and dashboard.

## Global Constraints

- stdout = exactly one compact JSON envelope per invocation; stderr empty unless `--verbose`; `bad_args` exit 2, `not_found` and `internal` exit 1.
- Diary file `$PACE_HOME/diary.json`, path resolved at call time from `pace.config.PACE_HOME` (tests monkeypatch it).
- Shape `{"profile": {...}, "entries": [...], "next_id": int}`; entry ids increment and are never reused.
- Entry kinds exactly `note | plan | recommendation | feedback | constraint | injury | decision`.
- Profile keys exactly `goal`, `event.name`, `event.date`, `availability`, `background`, `other_sports`, `strength`, `preferences`, `checkin.weight_days`, `checkin.last`, `dashboard.url`; dotted keys nest (`{"event": {"name": ..}}`); `profile.updated` = today's ISO date on every `set`.
- `checkin.weight_days` default 7 when unset (the skill reads the default from `pace schema`, the store does not write it).
- Corrupt or unreadable file -> `PaceError("internal", ..., hint=<path>)`; never overwrite a corrupt file.
- Writes atomic (`<file>.tmp` + `os.replace`); read-modify-write holds an exclusive lock so two sessions cannot lose each other's write.
- Local writes have no `--dry-run` and are not Garmin writes: schema entries mark them `"local_write": True` (never `"write": True`, which means a Garmin write with `--dry-run` and the confirmation rule).
  `WRITES["confirm"]` in `pace/commands/schema.py` and the Writes section of `docs/CONTRACT.md` say explicitly that the confirmation rule covers Garmin writes only; local diary writes are the coach's own memory and need no per-call yes (the skill in plan 4 decides when to ask).
- ASCII only in code, docs, help text; markdown one sentence per line; no Claude attribution in commits.
- `.venv/bin/python -m pytest -q` and `.venv/bin/ruff check pace tests scripts` clean at every commit (`uv run` needs the private index token; the venv binaries do not).

## Review Focus

1. Two sessions writing at once (Telegram run + terminal): both entries survive, ids stay unique. Test in Task 1.
2. Values starting with `-`: option values (`--date -1d`, `--due +7d`) already parse (verified with click 8.3.3); a positional `-1d` and diary TEXT words starting with `-` ("-5 kg") do not, so no positional takes a date, and TEXT after `--` must work. Test in Task 2.
3. Non-ASCII text from the user (Italian accents in a feedback comment): stored and returned unchanged. Test in Task 1.
4. A diary file written before a field existed (entries without `refs`/`due`): loads, and `show` returns those fields with defaults. Test in Task 1.
5. Bad `ref` values (`activity:abc`, `run:12`, empty, trailing newline) and bad profile values (`checkin.weight_days 0`, `dashboard.url http://example.com`) are `bad_args`, not stored. Test in Task 1.
6. First write on a fresh machine (`$PACE_HOME` does not exist yet): succeeds and creates the directory. Test in Task 1.

---

### Task 1: JSON store and diary model

**Files:**
- Create: `pace/store.py`, `pace/diary.py`
- Test: `tests/test_store.py`, `tests/test_diary.py`

**Interfaces:**
- Consumes: `pace.config.PACE_HOME`, `pace.config.ensure_home()`, `pace.envelope.PaceError`, `pace.dates.parse_date`.
- Produces (`pace.store`):

```python
def load_json(path: Path, empty: dict, what: str) -> dict
    # missing -> deepcopy(empty); unreadable or not a dict -> PaceError("internal", f"{what} unreadable: ...", hint=f"Inspect or move {path}; pace never overwrites it.")
def save_json(path: Path, data: dict) -> None
    # path.parent.mkdir(parents=True, exist_ok=True) (NOT config.ensure_home: plan 2 saves under $PACE_HOME/plans/);
    # write <path>.tmp (json, indent=1, sort_keys, ensure_ascii=False, utf-8); os.replace
@contextmanager
def locked(path: Path, timeout_s: float = 10.0) -> Iterator[None]
    # path.parent.mkdir(parents=True, exist_ok=True); fcntl.flock(LOCK_EX) on "<path>.lock" (created if missing,
    # never deleted), polling LOCK_NB every 50 ms until timeout;
    # timeout -> PaceError("internal", f"{path.name} is busy", hint="Another pace process is writing it; retry.")
    # flock is per open file description: a nested locked() on the same path in one process blocks until timeout,
    # so callers never nest transactions.
```

- Produces (`pace.diary`):

```python
KINDS = ("note", "plan", "recommendation", "feedback", "constraint", "injury", "decision")
ACTIVE_KINDS = ("constraint", "injury")
PROFILE_KEYS = ("goal", "event.name", "event.date", "availability", "background", "other_sports",
                "strength", "preferences", "checkin.weight_days", "checkin.last", "dashboard.url")
WEIGHT_DAYS_DEFAULT = 7
REF_PATTERN = r"(session|activity|entry):[1-9][0-9]*"   # always used with re.fullmatch
EMPTY = {"profile": {}, "entries": [], "next_id": 1}

def diary_path() -> Path
def load() -> dict                    # store.load_json; shape check (profile dict, entries list of dicts with int id) else internal + path hint;
                                      # backfill: entries missing "refs" get [], missing "due"/"resolved" get None; next_id = max(next_id or 1, max id + 1)
@contextmanager
def transaction() -> Iterator[dict]   # store.locked(diary_path()); data = load(); yield data; save on clean exit only
def set_profile(data: dict, key: str, value: str) -> dict          # returns profile
def unset_profile(data: dict, key: str) -> dict                    # removes the key (and an emptied parent dict); unknown key -> bad_args
def add_entry(data: dict, *, kind: str, text: str, on: str | None, tags: list[str],
              refs: list[str], due: str | None) -> dict               # returns entry
def remove_entry(data: dict, entry_id: int) -> dict                   # PaceError("not_found")
def set_due(data: dict, entry_id: int, due: str | None) -> dict       # None clears; PaceError("not_found")
def resolve(data: dict, entry_id: int, on: str | None) -> dict        # constraint/injury only (else bad_args); sets resolved (default today, not before the entry date); on=None with --reopen semantics handled by reopen()
def reopen(data: dict, entry_id: int) -> dict                         # clears resolved
def show(data: dict, *, since: str | None, kind: str | None, ref: str | None, limit: int,
         today: date | None = None) -> tuple[dict, bool]   # (data, truncated)
```

Entry shape, always all keys: `{"id": int, "date": "YYYY-MM-DD", "kind": str, "text": str, "tags": [str], "refs": [str], "due": "YYYY-MM-DD" | None, "resolved": "YYYY-MM-DD" | None}`.

`set_profile` value rules: strip, non-empty, else `bad_args`.
`event.date` -> `parse_date(...).isoformat()`.
`checkin.weight_days` -> ASCII digits only (`[0-9]+`), 1..90, stored as int.
`checkin.last` -> `"now"` or any `datetime.fromisoformat` value; a naive value gets the local zone (`.astimezone()`); stored normalized as `isoformat(timespec="seconds")` with offset.
`profile.updated` is set by changes to every key except `checkin.*` and `dashboard.*` (bookkeeping, not athlete data).
`dashboard.url` -> must start with `https://claude.ai/`.

`show` returns `{"profile", "active", "due", "entries"}`:
- `active`: constraint/injury entries with `resolved` None, oldest first, unfiltered (resolved ones stay in `entries` as history).
- `due`: entries with `due` not None and `due <= today`, ordered by (`due`, `id`), unfiltered.
- `entries`: sorted (date desc, id desc), filtered by `since` (date >= since), `kind`, `ref` (ref in refs), cut to `limit` (>= 1, else `bad_args`); `truncated` is True when the cut dropped entries.
- An `entry:<id>` ref lets a follow-up point back at the decision it closes.

- [ ] **Step 1: Write failing store tests** in `tests/test_store.py`:
  - `test_missing_returns_copy_of_empty`: result equals `empty`, mutating it leaves `empty` unchanged, no file created.
  - `test_save_round_trip_unicode`: save `{"t": "perché città"}`, file contains the literal accented text (not `\u` escapes), `load_json` returns it, no `.tmp` left.
  - `test_corrupt_is_internal_and_untouched`: file `{oops` -> `PaceError.code == "internal"`, path in hint, file content unchanged; same for a JSON list `[]`.
  - `test_lock_serializes_writers`: two processes from `multiprocessing.get_context("spawn")`, worker a module-level function taking the explicit path (store functions take paths, so no PACE_HOME is involved), each do 20 x (`with locked(p): d = load_json(..); d["n"] += 1; save_json(..)`) -> final `n == 40`.
  - `test_lock_timeout_is_internal`: in-process: open `<p>.lock` and `flock(LOCK_EX)` it directly, then `locked(p, timeout_s=0.2)` raises `PaceError` code `internal` with "busy" in message.
  - `test_fresh_dir_is_created`: `p = tmp_path / "new" / "x.json"`; `with locked(p): save_json(p, {...})` works and creates `new/`.

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_store.py -q` -> fails, `pace.store` missing.

- [ ] **Step 3: Implement `pace/store.py`** per the interface above.

- [ ] **Step 4: Run** -> 5 passed.

- [ ] **Step 5: Write failing diary tests** in `tests/test_diary.py` (autouse fixture monkeypatches `pace.config.PACE_HOME` to `tmp_path`). Start from the old diary plan's Task 1 tests, adapted to `on=`, `refs=`, `due=`, and to the new `show` keys; add:
  - `test_new_profile_keys`: `background`, `other_sports`, `strength`, `preferences` stored as strings; `checkin.weight_days "10"` -> `10` (int) under `profile["checkin"]["weight_days"]`; `"0"` and `"x"` -> `bad_args`; `dashboard.url "https://claude.ai/artifact/abc"` stored under `profile["dashboard"]["url"]`, `"http://example.com"` -> `bad_args`; `checkin.last "now"` parses with `datetime.fromisoformat` and has a UTC offset; `checkin.last "yesterday"` -> `bad_args`.
  - `test_decision_kind_refs_and_due`: `add_entry(kind="decision", text="advised: 2 quality / chose: 3 / because: race in May / check: HRV after 3 weeks", refs=["session:12"], due="2026-12-08", on="2026-11-17", tags=[])` returns all seven keys with those values.
  - `test_bad_refs_rejected`: each of `"activity:abc"`, `"run:12"`, `""`, `"session:0"`, `"activity:5\n"` -> `bad_args`, entry not appended, `next_id` unchanged; `"entry:3"` accepted.
  - `test_unset_and_updated`: `unset_profile(data, "event.name")` then `"event.date"` leaves no `event` key; unknown key -> `bad_args`; setting `checkin.last` does not change `profile.updated`, setting `goal` does (freeze today by passing values, or compare to a pre-set sentinel).
  - `test_checkin_last_normalized`: `"2026-11-17T08:00"` is stored with a UTC offset; `"yesterday"` -> `bad_args`; `checkin.weight_days` `"1_0"` and `"91"` -> `bad_args`.
  - `test_show_truncated_flag`: 3 entries, `limit=2` -> `truncated is True`; `limit=3` -> False.
  - `test_show_due_and_ref_filter`: entries with due `2026-11-10`, `2026-11-17`, `2026-11-24`, and one without; `show(..., today=date(2026, 11, 17))["due"]` ids are the first two in due order; `set_due(data, id_first, None)` removes it from `due`; `show(ref="activity:5")` returns only entries carrying that ref.
  - `test_old_file_backfills_fields`: write a file whose single entry (id 4) lacks `refs` and `due` and whose `next_id` is 2; `load()` gives `refs == []`, `due is None`, `next_id == 5`; a file whose `entries` is a dict -> `internal` with path hint.
  - `test_resolve_and_reopen`: injury dated 2026-03-01, `resolve(..., on="2026-04-05")` -> gone from `active`, still in `entries` with `resolved == "2026-04-05"`; `on` before the entry date -> `bad_args`; resolving a `note` -> `bad_args`; `reopen` puts it back in `active`.
  - `test_fresh_pace_home`: `PACE_HOME = tmp_path / "new"`; `with diary.transaction() as d: add_entry(...)` creates the directory and file.
  - `test_transaction_saves_only_on_success`: an exception inside `with diary.transaction()` leaves the file as it was; a clean exit writes it.
  - `test_unicode_text_round_trip`: feedback text `"gambe pesanti, perché ho dormito poco"` survives `transaction` + `load`.

- [ ] **Step 6: Run** `.venv/bin/python -m pytest tests/test_diary.py -q` -> fails, `pace.diary` missing.

- [ ] **Step 7: Implement `pace/diary.py`** per the interface; name the date parameter `on` so the stdlib `date` import is not shadowed.

- [ ] **Step 8: Run** full suite and ruff -> green.

- [ ] **Step 9: Commit**

```bash
git add pace/store.py pace/diary.py tests/test_store.py tests/test_diary.py
git commit -m "feat: locked JSON store and athlete diary model"
```

---

### Task 2: `diary` commands, schema, docs

**Files:**
- Create: `pace/commands/diary.py`
- Modify: `pace/cli.py` (register group `diary`), `pace/commands/schema.py` (STATIC_COMMANDS, groups, `known` set in `schema()`, manifest block), `docs/CONTRACT.md` (new "Diary" section), `README.md` (Use + a "Local stores" note)
- Test: `tests/test_diary_cmd.py`, `tests/test_schema.py`

**Interfaces:**
- Consumes: Task 1 `pace.diary.*`; `pace.envelope.Result`, `run`, `fail`, `PaceError`.
- Produces `pace.commands.diary.app`; command strings `"diary show"`, `"diary set"`, `"diary unset"`, `"diary add"`, `"diary remove"`, `"diary set-due"`, `"diary resolve"`:

| command | args / flags | `data` |
|---|---|---|
| `diary show` | `--since D`, `--kind K`, `--ref R`, `--limit N` (default 20) | `diary.show(...)` |
| `diary set KEY VALUE` | | `{"profile": ...}` |
| `diary unset KEY` | | `{"profile": ...}` |
| `diary add TEXT...` | `--kind K` (required), `--date D`, `--tag T` (repeat), `--ref R` (repeat), `--due D` | `{"entry": ...}` |
| `diary remove ID` | | `{"removed": ...}` |
| `diary set-due ID` | `--date D` or `--clear` (exactly one) | `{"entry": ...}` |
| `diary resolve ID` | `--date D` (default today) or `--reopen` | `{"entry": ...}` |

  `cache` and `shape` are `None`; `diary show` sets `Result.truncated` from `diary.show`. `show` uses `diary.load()` (no lock); the others use `diary.transaction()`.
  No positional takes a date (a positional `-1d` is misread as a flag). `diary add` help text and CONTRACT say TEXT goes after `--` when it may start with `-`.
- Schema: one STATIC_COMMANDS entry per command (`keyed: "none"`, exact `args`/`flags`, `default_fields: []`, `series: []`, `ttl_s: 0`; on set/unset/add/remove/set-due/resolve also `"local_write": True`; `diary add` and `diary set-due` declare `required_flags` (`["--kind"]`; set-due none, since it is one-of, so state it in `help`)); group `"diary": "Athlete diary: the coach's durable memory (local file)."`; top-level manifest key `"diary": {"path": "$PACE_HOME/diary.json", "kinds": KINDS, "profile_keys": PROFILE_KEYS, "ref_pattern": REF_PATTERN, "checkin_weight_days_default": WEIGHT_DAYS_DEFAULT}`.

- [ ] **Step 1: Write failing command tests** in `tests/test_diary_cmd.py`. Start from the old diary plan's Task 2 tests (show empty, round trip, bad kind/key, corrupt file), adapted to the four-key `show` data (`profile`, `active`, `due`, `entries`); add:
  - `test_add_with_refs_due_and_relative_dates`: `diary add --kind feedback --date -1d --ref activity:5 --ref session:12 --due +7d "felt", "heavy"` -> exit 0, entry `date` = yesterday ISO, `due` = today + 7 ISO, `refs == ["activity:5", "session:12"]`, `text == "felt heavy"`.
  - `test_set_due_sets_and_clears`: add an entry, `diary set-due 1 --date 2026-12-01` -> `entry.due == "2026-12-01"`; `diary set-due 1 --clear` -> `entry.due is None`; both or neither flag -> exit 2; `diary set-due 99 --clear` -> exit 1, `not_found`.
  - `test_resolve_command`: `diary resolve 1 --date 2026-04-05` then `diary show` -> not in `active`; `diary resolve 1 --reopen` -> back.
  - `test_text_starting_with_dash`: `diary add --kind feedback -- -5 kg on the scale` -> `text == "-5 kg on the scale"`.
  - `test_show_truncated`: 3 entries, `diary show --limit 2` -> envelope `truncated: true`.
  - `test_show_ref_filter`: two entries, one with `--ref activity:5`; `diary show --ref activity:5` returns just that one; `--ref bogus` -> exit 2.

- [ ] **Step 2: Append to `tests/test_schema.py`**: `test_diary_in_schema` asserting the seven command names are present, `local_write` is true on the six local writes and `write` absent on all diary commands (so `test_writes_and_dsl_in_schema`'s exact write set and dry-run loop stay valid unchanged), `diary add` has `required_flags == ["--kind"]`, `WRITES["confirm"]` mentions Garmin, `data["diary"]["kinds"][-1] == "decision"`, `"dashboard.url" in data["diary"]["profile_keys"]`, `"diary" in data["groups"]`; and `runner.invoke(app, ["schema", "diary"])` returns only `diary ...` commands.
  The existing `test_every_cli_command_is_in_schema` must stay green.

- [ ] **Step 3: Run** both files -> fail (unknown command `diary`).

- [ ] **Step 4: Implement** `pace/commands/diary.py` (pattern: the old diary plan's Task 2 Step 3), wire it in `pace/cli.py` and `pace/commands/schema.py`.

- [ ] **Step 5: Run** full suite and ruff -> green.

- [ ] **Step 6: Docs.**
  `docs/CONTRACT.md`: a "Diary" section: file location, entry shape, kinds, profile keys with value rules, `due` semantics, refs pattern, TEXT after `--`, locking (a second writer waits up to 10 s, then `internal` "busy"; the `.lock` file stays), corrupt-file rule; amend the Writes section: confirmation rule = Garmin writes only.
  `README.md`: diary lines under "Use" (`pace diary show`, `pace diary add --kind feedback --ref activity:<id> ...`), and one line saying diary and plan are local files under `$PACE_HOME`.
  One sentence per line.

  Spec post-hoc note (section 1): `refs` is a list (feedback concerns an activity and a session), `entry:<id>` refs, `diary set-due`, `diary unset`, `diary resolve` (user's call 2026-11-17), `local_write` marker.

- [ ] **Step 7: Live smoke check** (no Garmin access needed): `PACE_HOME=$(mktemp -d) .venv/bin/pace diary set goal "test"` then `diary show` -> `ok: true`, profile goal `test`.

- [ ] **Step 8: Commit**

```bash
git add pace/commands/diary.py pace/cli.py pace/commands/schema.py tests/test_diary_cmd.py tests/test_schema.py docs/CONTRACT.md README.md docs/superpowers/specs/2026-11-17-coach-plan-design.md
git commit -m "feat: diary commands, schema and contract"
```
