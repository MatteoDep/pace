# Pace 2.0 Athlete Diary and Coaching Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the LLM coach durable memory (`pace diary`, a local JSON file behind the envelope) and the Claude Code skill that teaches it how to use `pace` for data access and (minimally) for coaching.

**Architecture:** `pace/diary.py` is a pure file-backed store (`$PACE_HOME/diary.json`, atomic writes, corrupt-file detection) with four operations; `pace/commands/diary.py` wraps them in the envelope like every other command; `pace schema` lists them.
The skill lives in the repo at `.claude/skills/pace/SKILL.md` (plus a condensed `reference.md`), is drift-tested against `pace schema`, and is installed by symlink into `~/.claude/skills/pace`.

**Tech Stack:** Python 3.14, typer, stdlib json/os, pytest + typer `CliRunner`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-04-llm-cli-design.md` sections 5 (skill) and 8 (diary), plus section 2 (envelope contract) and section 7 (writes need confirmation).

## Global Constraints

- stdout = exactly one compact JSON envelope per invocation; stderr empty unless `--verbose`; error codes/exit codes unchanged (`bad_args` 2, `internal` 1).
- Diary file: `$PACE_HOME/diary.json` (late-bound via `pace.config.PACE_HOME`), shape `{"profile": {...}, "entries": [...]}`; entry ids are incrementing integers never reused (store `"next_id"`); entry kinds exactly `note | plan | recommendation | feedback | constraint | injury`; profile keys exactly `goal`, `event.name`, `event.date` (must parse as a date), `availability`; `profile.updated` = today's ISO date on every `set`.
- Corrupt or unreadable file -> `PaceError("internal", ..., hint=<path>)`; never overwrite a corrupt file.
- `diary show` data = `{"profile": {...}, "active": [constraint/injury entries], "entries": [recent, newest first, default limit 20]}`; `--since D` and `--kind K` filter `entries` only.
- Writes are atomic (write `diary.json.tmp`, `os.replace`).
- Skill: frontmatter `name: pace`; sections "Data access" and "Coaching (v0)"; ASCII only; every command group in `pace schema` must be mentioned in the skill (drift test); every write command must appear under the confirmation rule.
- ASCII only; markdown newline after each period; no Claude attribution; `.venv/bin/python -m pytest -q` and `ruff check pace tests scripts` clean at every commit.

## File Map

| Path | Responsibility |
|---|---|
| `pace/diary.py` | `Diary` store: load/save, `set_profile`, `add`, `remove`, `show`, `KINDS`, `PROFILE_KEYS` |
| `pace/commands/diary.py` | `show`, `set`, `add`, `remove` commands |
| `pace/commands/schema.py`, `pace/cli.py` | manifest entries + `diary` group wiring |
| `.claude/skills/pace/SKILL.md` | the skill (sections A and B) |
| `.claude/skills/pace/reference.md` | condensed command map + presentation tables (loaded by the skill on demand) |
| `tests/test_diary.py`, `tests/test_diary_cmd.py`, `tests/test_skill.py`, `tests/test_schema.py` | tests |
| `docs/CONTRACT.md`, `README.md` | diary section, skill install |

---

### Task 1: Diary store `pace/diary.py`

**Files:**
- Create: `pace/diary.py`
- Test: `tests/test_diary.py`

**Interfaces:**
- Consumes: `pace.config.PACE_HOME`, `pace.envelope.PaceError`, `pace.dates.parse_date`.
- Produces (`pace.diary`):

```python
KINDS = ("note", "plan", "recommendation", "feedback", "constraint", "injury")
PROFILE_KEYS = ("goal", "event.name", "event.date", "availability")
EMPTY = {"profile": {}, "entries": [], "next_id": 1}

def diary_path() -> Path                       # config.PACE_HOME / "diary.json", evaluated at call time
def load(path: Path | None = None) -> dict     # missing file -> deep copy of EMPTY; unreadable/corrupt -> PaceError("internal", msg, hint=str(path))
def save(data: dict, path: Path | None = None) -> None   # ensure_home, write <path>.tmp, os.replace
def set_profile(data: dict, key: str, value: str) -> dict   # validates key in PROFILE_KEYS; event.date parsed via parse_date -> ISO; sets profile.updated; returns profile
def add_entry(data: dict, *, kind: str, text: str, date: str | None, tags: list[str]) -> dict   # validates kind, non-empty text; date default today (parse_date); assigns id = next_id, increments; returns entry
def remove_entry(data: dict, entry_id: int) -> dict        # PaceError("not_found") when absent; returns removed entry
def show(data: dict, *, since: str | None, kind: str | None, limit: int) -> dict   # {"profile", "active", "entries"}
```

  - `active` = all entries with kind in (`constraint`, `injury`), oldest first, unfiltered.
  - `entries` = all entries sorted by (date desc, id desc), filtered by `since` (date >= since) and `kind`, cut to `limit`.
  - Entry shape: `{"id": int, "date": "YYYY-MM-DD", "kind": str, "text": str, "tags": [str]}` (`tags` omitted when empty is NOT allowed -- always a list, pruning happens in the envelope layer only if empty).

- [ ] **Step 1: Write failing tests**

`tests/test_diary.py`:

```python
"""Diary store: load/save, profile, entries, show filters, corruption."""

from datetime import date

import pytest

from pace import diary
from pace.envelope import PaceError


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    return tmp_path


def test_missing_file_bootstraps_empty(tmp_path) -> None:
    data = diary.load()
    assert data == {"profile": {}, "entries": [], "next_id": 1}
    assert not (tmp_path / "diary.json").exists()          # load never writes


def test_save_is_atomic_and_round_trips(tmp_path) -> None:
    data = diary.load()
    diary.add_entry(data, kind="note", text="hello", date="2026-09-06", tags=["x"])
    diary.save(data)
    assert (tmp_path / "diary.json").exists() and not (tmp_path / "diary.json.tmp").exists()
    assert diary.load() == data


def test_corrupt_file_is_internal_error_and_untouched(tmp_path) -> None:
    p = tmp_path / "diary.json"
    p.write_text("{not json")
    with pytest.raises(PaceError) as exc:
        diary.load()
    assert exc.value.code == "internal" and str(p) in (exc.value.hint or "")
    assert p.read_text() == "{not json"


def test_set_profile_keys_and_updated() -> None:
    data = diary.load()
    prof = diary.set_profile(data, "goal", "sub-1:45 half")
    diary.set_profile(data, "event.name", "Verona HM")
    diary.set_profile(data, "event.date", "2026-11-15")
    assert prof["goal"] == "sub-1:45 half" and prof["event"] == {"name": "Verona HM", "date": "2026-11-15"}
    assert prof["updated"] == date.today().isoformat()
    with pytest.raises(PaceError) as exc:
        diary.set_profile(data, "weight", "70")
    assert exc.value.code == "bad_args"
    with pytest.raises(PaceError):
        diary.set_profile(data, "event.date", "soon")


def test_add_assigns_incrementing_ids_never_reused() -> None:
    data = diary.load()
    a = diary.add_entry(data, kind="plan", text="Tue intervals", date=None, tags=[])
    b = diary.add_entry(data, kind="feedback", text="felt heavy", date="2026-09-05", tags=["legs"])
    assert (a["id"], b["id"]) == (1, 2) and a["date"] == date.today().isoformat()
    assert b == {"id": 2, "date": "2026-09-05", "kind": "feedback", "text": "felt heavy", "tags": ["legs"]}
    removed = diary.remove_entry(data, 2)
    assert removed["id"] == 2 and [e["id"] for e in data["entries"]] == [1]
    c = diary.add_entry(data, kind="note", text="n", date=None, tags=[])
    assert c["id"] == 3                                     # 2 is never reused
    with pytest.raises(PaceError) as exc:
        diary.remove_entry(data, 2)
    assert exc.value.code == "not_found"


def test_add_validation() -> None:
    data = diary.load()
    with pytest.raises(PaceError) as exc:
        diary.add_entry(data, kind="rant", text="x", date=None, tags=[])
    assert exc.value.code == "bad_args" and "kind" in exc.value.message
    with pytest.raises(PaceError):
        diary.add_entry(data, kind="note", text="   ", date=None, tags=[])


def test_show_filters_and_active() -> None:
    data = diary.load()
    diary.set_profile(data, "goal", "g")
    diary.add_entry(data, kind="constraint", text="no Mondays", date="2026-08-01", tags=[])
    diary.add_entry(data, kind="injury", text="left achilles", date="2026-08-20", tags=[])
    for i in range(25):
        diary.add_entry(data, kind="recommendation", text=f"r{i}", date=f"2026-09-{(i % 6) + 1:02d}", tags=[])
    out = diary.show(data, since=None, kind=None, limit=20)
    assert out["profile"]["goal"] == "g"
    assert [e["text"] for e in out["active"]] == ["no Mondays", "left achilles"]
    assert len(out["entries"]) == 20
    dates = [e["date"] for e in out["entries"]]
    assert dates == sorted(dates, reverse=True)
    ids_same_day = [e["id"] for e in out["entries"] if e["date"] == dates[0]]
    assert ids_same_day == sorted(ids_same_day, reverse=True)
    only = diary.show(data, since="2026-09-06", kind="recommendation", limit=50)["entries"]
    assert only and all(e["date"] >= "2026-09-06" and e["kind"] == "recommendation" for e in only)
    assert diary.show(data, since=None, kind="injury", limit=5)["entries"][0]["text"] == "left achilles"
```

- [ ] **Step 2: Run to verify failure** -> ModuleNotFoundError `pace.diary`.

- [ ] **Step 3: Implement `pace/diary.py`**

```python
"""Athlete diary: the coach's durable memory, stored at $PACE_HOME/diary.json."""

import copy
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from pace import config
from pace.dates import parse_date
from pace.envelope import PaceError

KINDS = ("note", "plan", "recommendation", "feedback", "constraint", "injury")
ACTIVE_KINDS = ("constraint", "injury")
PROFILE_KEYS = ("goal", "event.name", "event.date", "availability")
EMPTY: dict[str, Any] = {"profile": {}, "entries": [], "next_id": 1}


def diary_path() -> Path:
    return config.PACE_HOME / "diary.json"


def load(path: Path | None = None) -> dict[str, Any]:
    path = path or diary_path()
    if not path.exists():
        return copy.deepcopy(EMPTY)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaceError("internal", f"diary unreadable: {exc}", hint=f"Inspect or move {path}; pace never overwrites it.") from None
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list) or not isinstance(data.get("profile"), dict):
        raise PaceError("internal", "diary has an unexpected shape", hint=f"Inspect or move {path}; pace never overwrites it.")
    data.setdefault("next_id", max([e.get("id", 0) for e in data["entries"]] + [0]) + 1)
    return data


def save(data: dict[str, Any], path: Path | None = None) -> None:
    path = path or diary_path()
    config.ensure_home()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def set_profile(data: dict[str, Any], key: str, value: str) -> dict[str, Any]:
    if key not in PROFILE_KEYS:
        raise PaceError("bad_args", f"unknown profile key {key!r}; one of {', '.join(PROFILE_KEYS)}")
    value = value.strip()
    if not value:
        raise PaceError("bad_args", "value must be non-empty")
    if key == "event.date":
        value = parse_date(value).isoformat()
    prof = data["profile"]
    if key.startswith("event."):
        prof.setdefault("event", {})[key.split(".", 1)[1]] = value
    else:
        prof[key] = value
    prof["updated"] = date.today().isoformat()
    return prof


def add_entry(data: dict[str, Any], *, kind: str, text: str, date_str: str | None = None,
              date: str | None = None, tags: list[str]) -> dict[str, Any]:
    when = date if date is not None else date_str
    if kind not in KINDS:
        raise PaceError("bad_args", f"unknown kind {kind!r}; one of {', '.join(KINDS)}")
    if not isinstance(text, str) or not text.strip():
        raise PaceError("bad_args", "text must be non-empty")
    entry = {"id": data["next_id"], "date": parse_date(when).isoformat(), "kind": kind,
             "text": text.strip(), "tags": [t for t in tags if t]}
    data["next_id"] += 1
    data["entries"].append(entry)
    return entry


def remove_entry(data: dict[str, Any], entry_id: int) -> dict[str, Any]:
    for i, e in enumerate(data["entries"]):
        if e.get("id") == entry_id:
            return data["entries"].pop(i)
    raise PaceError("not_found", f"no diary entry with id {entry_id}")


def show(data: dict[str, Any], *, since: str | None, kind: str | None, limit: int) -> dict[str, Any]:
    if kind is not None and kind not in KINDS:
        raise PaceError("bad_args", f"unknown kind {kind!r}; one of {', '.join(KINDS)}")
    if limit < 1:
        raise PaceError("bad_args", "--limit must be >= 1")
    since_iso = parse_date(since).isoformat() if since else None
    entries = sorted(data["entries"], key=lambda e: (e["date"], e["id"]), reverse=True)
    if since_iso:
        entries = [e for e in entries if e["date"] >= since_iso]
    if kind:
        entries = [e for e in entries if e["kind"] == kind]
    active = sorted((e for e in data["entries"] if e["kind"] in ACTIVE_KINDS), key=lambda e: (e["date"], e["id"]))
    return {"profile": data["profile"], "active": active, "entries": entries[:limit]}
```

Note: `add_entry` takes `date=` (the tests call it that way); the `date_str` alias exists only because `date` shadows the stdlib import inside the function -- keep the `from datetime import date` import and use `date.today()` only in `set_profile`. If ruff flags the shadowing, rename the import to `from datetime import date as _date` and use `_date.today()`.

- [ ] **Step 4: Run tests** -> 7 passed; full suite green; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add pace/diary.py tests/test_diary.py
git commit -m "feat: athlete diary store"
```

---

### Task 2: `diary` commands, schema, docs

**Files:**
- Create: `pace/commands/diary.py`
- Modify: `pace/cli.py`, `pace/commands/schema.py`, `docs/CONTRACT.md`, `README.md`
- Test: `tests/test_diary_cmd.py`, `tests/test_schema.py`

**Interfaces:**
- Consumes: `pace.diary.*`, `pace.envelope.run/fail/Result/PaceError`.
- Produces `pace.commands.diary.app` with commands (command strings `"diary show"`, `"diary set"`, `"diary add"`, `"diary remove"`):
  - `show [--since D] [--kind K] [--limit N=20]` -> `Result(diary.show(...))`.
  - `set KEY VALUE` -> `Result({"profile": <profile>})`.
  - `add --kind K [--date D] [--tag T]... TEXT...` (TEXT is one or more positional words joined by spaces) -> `Result({"entry": <entry>})`.
  - `remove ID` -> `Result({"removed": <entry>})`.
  - `cache`/`shape` stay `None` (no Garmin access). Each mutating command loads, mutates, saves.
- Schema: STATIC_COMMANDS entries for the four commands (`keyed: "none"`, `args`/`flags` exact, `"write": true` on `set/add/remove` with a `"local": true` marker meaning "writes the local diary, not Garmin"), group `"diary": "Athlete diary: the coach's durable memory (local file)."`, and a `diary` block in the manifest: `{"path": "$PACE_HOME/diary.json", "kinds": KINDS, "profile_keys": PROFILE_KEYS}`.

- [ ] **Step 1: Write failing tests**

`tests/test_diary_cmd.py`:

```python
import json

import pytest
from typer.testing import CliRunner

from pace.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")
    return tmp_path


def _doc(res):
    assert res.stdout.count("\n") == 1, res.stdout
    return json.loads(res.stdout)


def test_show_empty(tmp_path) -> None:
    doc = _doc(runner.invoke(app, ["diary", "show"]))
    assert doc["ok"] and doc["command"] == "diary show"
    assert doc["data"] == {"profile": {}, "active": [], "entries": []}
    assert doc["cache"] is None and doc["shape"] is None
    assert not (tmp_path / "diary.json").exists()


def test_set_add_remove_round_trip(tmp_path) -> None:
    doc = _doc(runner.invoke(app, ["diary", "set", "goal", "sub-1:45 half"]))
    assert doc["data"]["profile"]["goal"] == "sub-1:45 half"
    doc = _doc(runner.invoke(app, ["diary", "add", "--kind", "constraint", "--date", "2026-09-01", "--tag", "schedule",
                                   "no", "long", "runs", "on", "Mondays"]))
    entry = doc["data"]["entry"]
    assert entry == {"id": 1, "date": "2026-09-01", "kind": "constraint", "text": "no long runs on Mondays", "tags": ["schedule"]}
    doc = _doc(runner.invoke(app, ["diary", "add", "--kind", "recommendation", "Easy", "week"]))
    assert doc["data"]["entry"]["id"] == 2
    doc = _doc(runner.invoke(app, ["diary", "show", "--kind", "recommendation"]))
    assert [e["id"] for e in doc["data"]["entries"]] == [2] and [e["id"] for e in doc["data"]["active"]] == [1]
    doc = _doc(runner.invoke(app, ["diary", "remove", "2"]))
    assert doc["data"]["removed"]["id"] == 2
    res = runner.invoke(app, ["diary", "remove", "2"])
    assert res.exit_code == 1 and _doc(res)["error"]["code"] == "not_found"
    assert json.loads((tmp_path / "diary.json").read_text())["next_id"] == 3


def test_bad_kind_and_key_are_bad_args() -> None:
    res = runner.invoke(app, ["diary", "add", "--kind", "rant", "x"])
    assert res.exit_code == 2 and _doc(res)["error"]["code"] == "bad_args"
    res = runner.invoke(app, ["diary", "set", "weight", "70"])
    assert res.exit_code == 2


def test_corrupt_diary_is_internal_with_hint(tmp_path) -> None:
    (tmp_path / "diary.json").write_text("{oops")
    res = runner.invoke(app, ["diary", "show"])
    doc = _doc(res)
    assert res.exit_code == 1 and doc["error"]["code"] == "internal" and "diary.json" in doc["error"]["hint"]
    assert (tmp_path / "diary.json").read_text() == "{oops"
```

Append to `tests/test_schema.py`:

```python
def test_diary_in_schema() -> None:
    data = build_schema(None)
    names = {c["name"] for c in data["commands"]}
    assert {"diary show", "diary set", "diary add", "diary remove"} <= names
    assert data["diary"]["kinds"][0] == "note" and "diary" in data["groups"]
    assert all(c.get("local") for c in data["commands"] if c["name"] in {"diary set", "diary add", "diary remove"})
```

- [ ] **Step 2: Run to verify failure** -> unknown command `diary` (exit 2), schema test fails.

- [ ] **Step 3: Implement `pace/commands/diary.py`**

```python
"""Diary commands: the coach's durable memory (local file, no Garmin access)."""

import typer

from pace import diary
from pace.envelope import PaceError, Result, fail, run

app = typer.Typer(no_args_is_help=True, help="Athlete diary: goals, constraints, recommendations, feedback (local file).")


@app.command("show")
def show(since: str | None = typer.Option(None, "--since", help="Only entries on/after this date."),
         kind: str | None = typer.Option(None, "--kind", help=f"One of {', '.join(diary.KINDS)}."),
         limit: int = typer.Option(20, "--limit", "-l")) -> None:
    """Profile, active constraints/injuries, and recent entries (newest first)."""
    async def work() -> Result:
        return Result(diary.show(diary.load(), since=since, kind=kind, limit=limit))

    run("diary show", {"since": since, "kind": kind, "limit": limit}, work)


@app.command("set")
def set_(key: str = typer.Argument(..., metavar="KEY", help=f"One of {', '.join(diary.PROFILE_KEYS)}."),
         value: str = typer.Argument(..., metavar="VALUE")) -> None:
    """Set a profile field (goal, event.name, event.date, availability)."""
    async def work() -> Result:
        data = diary.load()
        prof = diary.set_profile(data, key, value)
        diary.save(data)
        return Result({"profile": prof})

    run("diary set", {"key": key, "value": value}, work)


@app.command("add")
def add(text: list[str] = typer.Argument(..., metavar="TEXT...", help="Entry text (words are joined)."),
        kind: str = typer.Option(..., "--kind", "-k", help=f"One of {', '.join(diary.KINDS)}."),
        date: str | None = typer.Option(None, "--date", "-d", help="Default today."),
        tag: list[str] = typer.Option([], "--tag", "-t", help="Repeatable.")) -> None:
    """Append an entry."""
    joined = " ".join(text)

    async def work() -> Result:
        data = diary.load()
        entry = diary.add_entry(data, kind=kind, text=joined, date=date, tags=list(tag))
        diary.save(data)
        return Result({"entry": entry})

    run("diary add", {"kind": kind, "date": date, "tags": list(tag), "text": joined}, work)


@app.command("remove")
def remove(entry_id: int = typer.Argument(..., metavar="ID")) -> None:
    """Remove one entry by id."""
    async def work() -> Result:
        data = diary.load()
        entry = diary.remove_entry(data, entry_id)
        diary.save(data)
        return Result({"removed": entry})

    run("diary remove", {"id": entry_id}, work)
```

`PaceError`/`fail` imports are unused if all validation happens inside `work()` -- drop them to keep ruff clean.
Wire `diary` in `pace/cli.py` (import + `add_typer` tuple).
Schema: add the four STATIC_COMMANDS entries (`"write": True, "local": True` on set/add/remove), the `diary` group, the `known` set entry in `schema()`, and `"diary": {"path": "$PACE_HOME/diary.json", "kinds": list(diary.KINDS), "profile_keys": list(diary.PROFILE_KEYS)}` in `build_schema`.
`docs/CONTRACT.md`: section "Diary" (file location, shape, kinds, `cache`/`shape` null, corrupt-file behaviour). `README.md`: `pace diary show`, `pace diary set goal "..."`, `pace diary add --kind feedback "..."` under Use.

- [ ] **Step 4: Run tests** -> full suite green including the drift test; ruff clean; `.venv/bin/python -m pace diary show` works.

- [ ] **Step 5: Commit**

```bash
git add pace/commands/diary.py pace/cli.py pace/commands/schema.py tests/test_diary_cmd.py tests/test_schema.py docs/CONTRACT.md README.md
git commit -m "feat: diary commands with schema and docs"
```

---

### Task 3: The skill

**Files:**
- Create: `.claude/skills/pace/SKILL.md`, `.claude/skills/pace/reference.md`
- Modify: `README.md` (install section)
- Test: `tests/test_skill.py`

**Interfaces:**
- `SKILL.md` frontmatter:

```yaml
---
name: pace
description: Use when the user asks about their Garmin data or training - running, cycling, sleep, HRV, recovery, readiness, workouts, weight - or wants coaching, a weekly check-in, or a workout pushed to the watch. Drives the `pace` CLI (JSON envelopes) and the athlete diary.
---
```

- Body sections, in order: `# Pace coach`, `## Session start`, `## Data access` (envelope rules, token ladder, units, errors, command map pointer), `## Presenting data` (the homogeneous contract with the five column-order tables), `## Writes` (confirmation rule, dry-run, diary logging), `## Coaching (v0)`, `## Reference` (pointer to `reference.md` and `pace schema`).
- `reference.md`: condensed command map table (group, command, keyed, question it answers) for every group in `pace schema` (`activity auth cache config device diary gear goal log profile schema training wellness workout`), the workout DSL example, and the five presentation tables with fixed column orders:
  - Activities: `Date | Type | Name | Dist (km) | Time (h:mm) | Pace (min/km) or Speed (km/h) | Avg HR | Load | TE aer/anaer`
  - Sleep: `Night | Total (h:mm) | Deep | Light | REM | Awake | Score | Avg HR | Resp`
  - Daily wellness: `Date | Steps | Resting HR | HRV (ms) | Stress avg | Body battery hi/lo | Readiness`
  - Training status: `Date | Status | Load (7d) | Load focus | VO2max | Recovery (h)`
  - Workout detail: `Time | HR | Pace (min/km) | Dist (km) | Elev (m) | Cadence | Power`
- `tests/test_skill.py` drift guard: every group name from `build_schema(None)["groups"]` appears in `reference.md`; every command with `"write": true` in the manifest appears in `SKILL.md` under the `## Writes` section text; SKILL.md is ASCII; frontmatter has `name: pace`.

- [ ] **Step 1: Write failing test**

`tests/test_skill.py`:

```python
from pathlib import Path

from pace.commands.schema import build_schema

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude" / "skills" / "pace" / "SKILL.md"
REF = ROOT / ".claude" / "skills" / "pace" / "reference.md"


def test_skill_files_exist_and_ascii() -> None:
    for p in (SKILL, REF):
        text = p.read_text(encoding="utf-8")
        assert text.isascii(), f"{p} has non-ASCII"
    assert SKILL.read_text().startswith("---\nname: pace\n")


def test_every_group_in_reference() -> None:
    ref = REF.read_text()
    for group in build_schema(None)["groups"]:
        assert f"`pace {group}" in ref or f"| {group} " in ref, group


def test_every_write_listed_under_writes_section() -> None:
    skill = SKILL.read_text()
    writes_section = skill.split("## Writes", 1)[1].split("\n## ", 1)[0]
    for cmd in build_schema(None)["commands"]:
        if cmd.get("write"):
            assert f"`pace {cmd['name']}" in writes_section, cmd["name"]
```

- [ ] **Step 2: Run to verify failure** -> FileNotFoundError.

- [ ] **Step 3: Write `.claude/skills/pace/SKILL.md`**

Write the full skill text following the spec's section 5 A/B items verbatim in substance (each numbered rule becomes a bullet), with these exact command-bearing lines so the drift test passes and the agent has copy-paste commands:

```markdown
## Session start

Run these two together, before anything else:

    pace auth status
    pace diary show

If `auth status` reports `logged_in: false` or any command returns `auth_required`, tell the user to run `pace auth login` in their own terminal (interactive MFA). Never run it yourself.
Read the diary profile (goal, event, availability) and active constraints/injuries before asking the user anything.
```

```markdown
## Writes

Writes change the user's Garmin account or the local diary. Rules:

- Never write unprompted, and never during a check-in the user did not ask to act on.
- Before any Garmin write, show the planned write as a table and get an explicit "yes". Then call the command.
- Use `--dry-run` first when you are unsure the DSL translation is right; show the payload summary, not raw JSON.
- After any Garmin write, record it: `pace diary add --kind plan "..."`.

Garmin writes: `pace workout push FILE|-`, `pace workout schedule ID DATE`, `pace workout delete ID`, `pace log weight add KG`, `pace log weight delete SAMPLE_PK --date D`, `pace log hydration ML`, `pace log bp add SYS DIA PULSE`.
Local writes (no confirmation needed, but say what you recorded): `pace diary set KEY VALUE`, `pace diary add --kind K TEXT`, `pace diary remove ID`.
```

(Keep every write command from `pace schema` in that paragraph; the test enforces it.)
Presenting-data section: the five tables (copy from `reference.md`), plus the rules: line 1 scope+freshness; one table; <= 3 bullets under `Notes`; ASCII only; no raw JSON; no camelCase names; units km / min:ss per km (or km/h cycling) / h:mm / bpm / m; use `*Local` timestamps.
Coaching section: the spec's B bullets (profile first from diary; recommendation/feedback logging; weekly check-in recipe with the exact six commands; framing; guardrails; inline arithmetic note).

- [ ] **Step 4: Write `.claude/skills/pace/reference.md`**

Generate the command map from `pace schema` output at authoring time (`.venv/bin/python -m pace schema | .venv/bin/python -c "import json,sys; [print(c['name'], c['keyed'], c['help']) for c in json.load(sys.stdin)['data']['commands']]"`), one row per command, grouped, plus the DSL example from `workout_dsl` and the five presentation tables.

- [ ] **Step 5: README install section**

Replace the "planned (plan 3)" line with:

```markdown
## Claude Code skill

```bash
ln -s "$PWD/.claude/skills/pace" ~/.claude/skills/pace
```

The skill loads automatically in any Claude Code session when you talk about your Garmin data or training.
```

- [ ] **Step 6: Run tests** -> green; ruff clean. **Step 7: Commit**

```bash
git add .claude/skills/pace tests/test_skill.py README.md
git commit -m "feat: pace coaching skill (data access + coaching v0)"
```

---

### Task 4: Install and smoke the skill (controller step)

- [ ] `ln -sfn /home/matteo/projects/trainscout/.claude/skills/pace ~/.claude/skills/pace` (ask the user first if `~/.claude/skills/pace` already exists and is not ours).
- [ ] `ls -la ~/.claude/skills/pace/` shows `SKILL.md`, `reference.md`.
- [ ] Ask the user to open a fresh Claude Code session in any directory and say "how did I sleep this week?" -- the skill should trigger, run `pace auth status` + `pace diary show`, then `pace wellness sleep --days 7`, and present the Sleep table.

## Self-review notes

- Spec 8 diary storage/shape/kinds/ids/commands/corruption: Tasks 1-2.
- Spec 5 skill sections A (1-9) and B, install: Task 3-4; drift test ties the skill to `pace schema`.
- Spec 7 confirmation rule lives in the skill (`## Writes`), enforced by `test_every_write_listed_under_writes_section`.
- Type consistency: `diary.add_entry(data, kind=, text=, date=, tags=)` used identically in Tasks 1 and 2; `build_schema(None)["groups"]` / `["commands"][i]["write"]` shapes come from plan 2 Task 6.
- Depends on plan 2 being merged (write commands present in the manifest); execute after it.
