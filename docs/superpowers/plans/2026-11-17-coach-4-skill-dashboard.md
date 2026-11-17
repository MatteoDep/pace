# Coach 4: Coaching Skill and Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Claude Code skill that coaches the user through `pace` (run reviews, check-ins, intake, plan building with evidence and pushback, zones, charts), and the persistent training load dashboard it keeps current.

**Architecture:** `.claude/skills/pace/` holds `SKILL.md` (always loaded, kept short) and three reference files loaded at named moments: `reference.md` (command map, presentation tables), `coaching.md` (principles, threshold methods, evidence grades, verified sources), `dashboard.md` (refresh and change procedure).
Strict drift tests tie every `pace ...` span in the skill to `pace schema`.
The dashboard is a private artifact made from the account's "Data" type; its data is one JSON file exported by `pace load --export`, uploaded as an asset and read by three datasets through loaders; its page is d3 code stored in the artifact.

**Tech Stack:** Markdown skill files, pytest, the Artifact / ArtifactData tools (controller tasks only), d3 v7 inside the Data type.

**Spec:** `docs/superpowers/specs/2026-11-17-coach-plan-design.md` sections 4, 5 (5B as revised 2026-11-17: run review + full check-in), 7 (zones), "What the coach is", Anti-goals, "Decisions after plan review".
Plan 4; runs after plans 1, 2, 3 and 5.
Supersedes Tasks 3-4 of `docs/superpowers/plans/2026-09-06-diary-and-skill.md`.

## Global Constraints

- Skill files ASCII only; frontmatter `name: pace`; `SKILL.md` at most about 250 lines (details go to reference files).
- Confirmation rules (the `## Writes` section states them exactly):
  - Garmin writes (`"write": true` in `pace schema`) need an explicit yes after the user sees the planned write, except: the user's own RPE/feel statement is the yes for that one `activity effort`; a weight number the user gives is the yes for that one `log weight add`.
  - Plan changes (`plan new/meta/add/set/remove`, `plan block ...`) and `zones accept` are proposed and applied on the user's yes.
  - Diary writes need no per-call yes and the skill says what it recorded, except a reported pain becomes an `injury` entry only after the user confirms the wording.
  - `zones add` records what the user reported (lab) or what pace computed (`--save` on estimate/analyze); the skill says what it recorded; a lab entry changes the zones, so the skill states the new zones right after.
- Which zones judge intensity (spec 7): if `pace zones show` reports `in_sync: true` (pace's current floors equal the watch's), the run review uses `hr_zones_s` from `plan status`; otherwise it judges from average HR and time against the current LT1/LT2 HR, and workout DSL targets use bpm ranges instead of `z1..z5`; with no current record, the watch's zones and `hr_zones_s` are used as they are.
- Unattended runs: `auth_required` ends the run with one message asking the user to run `pace auth login`; nothing waits for a login.
- Run review and check-in exactly as spec 5B (2026-11-17): openers built from the data, one line per pending run, "no comment" recorded when the user adds nothing, full check-in only when asked or `checkin.last` older than 20 hours.
- Runtime citation rule in `SKILL.md`: cite only sources in `coaching.md` or sources opened in this session; otherwise state the principle without a citation.
- The user's watch (Forerunner 55) records no HRV and no sleep score; the skill uses resting HR, sleep, RPE/feel and load, and HRV only when present.
- Charts: terminal sessions write `--out "$(git rev-parse --show-toplevel 2>/dev/null || pwd)/chart-<kind>.png"`; `.gitignore` gains `/chart-*.png`.
- Dashboard: one artifact, URL in `diary` profile `dashboard.url`; if unset, the coach offers to create one and never creates one unattended; refreshed at the end of a full check-in and once after a batch of plan edits (not after each edit); replaced assets are deleted after the new ones are in use.
- Markdown in docs: one sentence per line; no Claude attribution.

## Review Focus

1. Brand-new user (empty diary, no plan, no zones): session start runs intake, one open question at a time, and does not fail on empty data. Smoke test, Task 4.
2. An unattended run (project 2) can follow every step: no step needs a human-opened file, `checkin.last` is written, the dashboard is refreshed or skipped without looping on `check`. Checked by reading SKILL.md and dashboard.md as an unattended run would (Task 1 Step 6, Task 3 Step 6).
3. A run with RPE and feel already set and on target: one short opener, no question about RPE; the user says nothing -> "no comment" feedback, not raised again next session. Smoke test, Task 4.
4. A command or flag renamed in the CLI: the strict drift test fails. Task 1 tests.
5. A source in `coaching.md` that does not say what it is cited for. Task 1 Step 3 and the controller's review of the file.

---

### Task 1: Skill files and strict drift tests

**Files:**
- Create: `.claude/skills/pace/SKILL.md`, `.claude/skills/pace/reference.md`, `.claude/skills/pace/coaching.md`, `.claude/skills/pace/dashboard.md` (headings only; Task 3 fills it)
- Modify: `README.md` (replace the "planned (plan 3; not yet in the repo)" skill line with an install section), `.gitignore` (`/chart-*.png`)
- Test: `tests/test_skill.py`

**Interfaces:**
- Consumes: `pace.commands.schema.build_schema` (commands with `flags`, `write`, `local_write`; `global_flags`; `diary.profile_keys`, `diary.checkin_weight_days_default`; `plan.modified_tolerance`), `pace.diary.PROFILE_KEYS`, `pace.diary.WEIGHT_DAYS_DEFAULT`.
- Frontmatter:

```yaml
---
name: pace
description: Use when the user talks about their running or other training, their Garmin data (runs, sleep, resting HR, weight, recovery), a training plan, thresholds or zones, how a workout went, or wants coaching, a check-in, a chart, or a workout on their watch. Drives the `pace` CLI and keeps the athlete diary, training plan and zones.
---
```

- `SKILL.md` sections, in order:
  1. `# Pace coach`: who the coach is (spec "What the coach is", five bullets, the user's register).
  2. `## Session start`: run together `pace auth status`, `pace diary show`, `pace diary show --kind feedback --since <D> --limit 200`, `pace plan status --from <D> --ahead 7`, `pace zones show`, where D = the date of `checkin.last` but at most 21 days back, or 7 days back when `checkin.last` is unset; `auth_required` -> ask the user to run `pace auth login`; then the run review (spec 5B: openers from the data using HR zone times, target vs actual, RPE vs intent; unplanned runs checked with `pace workout calendar` first; answers to `pace diary add --kind feedback --ref activity:<id> [--ref session:<id>] -- <text>`; "no comment" when nothing is added).
  3. `## Full check-in`: when it runs; the five steps of spec 5B (intake gaps; weight via `pace log weight list` and `checkin.weight_days` default 7; drift incl. `unconfirmed` sessions -> ask, `pace plan set ID --status done` on the user's word; due decisions closed with `pace diary set-due ID --clear` and a follow-up `--ref entry:<id>`; thresholds due per `pace zones show`); end with `pace diary set checkin.last now` and the dashboard refresh (read `dashboard.md`).
  4. `## Intake`: the profile keys listed literally (goal, event.name, event.date, availability, background, other_sports, strength, preferences, checkin.weight_days), open questions one at a time, `pace diary set` after each answer; injuries and constraints as entries; `pace diary resolve` when one heals.
  5. `## Data access`: envelope rules, token ladder, units, error codes; read `reference.md` for the command map; `pace schema <group>` for exact flags.
  6. `## Presenting data`: line 1 scope + freshness, one table, at most 3 notes, ASCII, no raw JSON or camelCase; a chart when it answers better, path rule above.
  7. `## Writes`: the confirmation rules above; every `write` and every `local_write` command named as `` `pace <name>` ``; `--dry-run` when unsure; after a workout or plan push, `pace diary add --kind plan`.
  8. `## Building and adjusting the plan`: read `coaching.md` before proposing plan structure, a load change, or a test; spec 5C building and adjusting with `pace plan add -` (JSON list on stdin), `pace plan push` for the next days only on yes, `--force` only to replace, never past sessions; re-push stale sessions on yes; strength and mobility as text sessions.
  9. `## Thresholds and zones`: read `coaching.md` (thresholds section) first; `pace zones show` (current, `in_sync`, `due`), `pace zones estimate --save` when due, planning a test with `pace zones test-workout` into the plan, `pace zones analyze --save` after it, `pace zones add` for lab values as reported, `pace zones accept` and `pace zones push` on yes; the intensity rule above; lab current for 6 months, then suggest a retest. Exact command names and flags follow plan 5 as built.
  10. `## Coaching`: framing; use of history; pushback protocol with `decision` entries (`advised: ... / chose: ... / because: ... / check: ...`, `--due`); the runtime citation rule; guardrails.
  11. `## Charts and dashboard`: kinds and when to use each; read `dashboard.md` before touching the dashboard.
- `reference.md`: command map (one row per command from `pace schema`: group, command, keyed, question it answers), the workout DSL example, presentation tables:
  - Activities: `Date | Type | Name | Dist (km) | Time (h:mm) | Pace (min/km) or Speed (km/h) | Avg HR | RPE | Feel` (RPE/Feel from `plan status`, else `-`)
  - Plan status: `Date | Session | Status | Planned | Actual | Zones (s in Z1-5) | RPE | Feel`
  - Sleep: `Night | Total (h:mm) | Deep | Light | REM | Awake | Resting HR` (from `pace wellness sleep-range`)
  - Weekly load: `Week | Km | D+ (m) | Hours | Projected km | Rolling 7d km`
  - Zones: `Zone | HR | Pace (min/km)`
  - Workout detail: `Time | HR | Pace (min/km) | Dist (km) | Elev (m) | Cadence`
- `coaching.md`: one section per principle of spec 5C plus "Thresholds and zones" (from `docs/superpowers/specs/2026-11-17-zones-methods.md`, plan 5 Task 1); each section: principle (2-3 lines), what it means for this athlete, `Evidence: strong | moderate | weak/expert opinion`, `Sources:` one per line with DOI or URL and `(full text)` or `(abstract)`, or `Sources: none verified`.

- [ ] **Step 1: Write failing tests** `tests/test_skill.py`:
  - `test_files_exist_ascii_frontmatter`.
  - `test_every_group_and_command_in_reference`: each group, and each command name matched as `` `pace <name>` `` followed by a space, backtick or end.
  - `test_pace_spans_resolve`: every `` `pace ...` `` span in the four files resolves, by longest prefix, to a schema command, or to an allowlist (`pace schema`, `pace auth login`, `pace <group> --help`); every `--flag` in the span is in that command's `flags` or `global_flags`; a bare `--` (end of options) and everything after it are ignored.
  - `test_writes_section_complete`: inside `## Writes`, every `write` and `local_write` command appears with a terminator (so `diary set` is not satisfied by `diary set-due`).
  - `test_session_start_spans`: `## Session start` contains `pace auth status`, `pace diary show`, `pace plan status`; `## Full check-in` contains `pace diary set checkin.last now`.
  - `test_constants_match`: the profile keys listed in `## Intake` equal `PROFILE_KEYS` minus `checkin.last` and `dashboard.url`; the weight default in SKILL.md equals `WEIGHT_DAYS_DEFAULT`.
  - `test_coaching_sections`: every section has an `Evidence:` grade and either `Sources: none verified` or source lines each with a DOI or URL and `(full text)` / `(abstract)`.
  - `test_skill_md_size`: at most 250 lines.
- [ ] **Step 2: Run** -> fail.
- [ ] **Step 3: Research `coaching.md`**: open each source (candidates to check, not to trust: Seiler 2010; Stoggl and Sperlich 2014; Bosquet et al. 2007; Blagrove et al. 2018; Impellizzeri et al. 2020; Nielsen et al. 2014; Buist et al. 2008; Mountjoy et al. 2023; Fullagar et al. 2015); record only what the opened source supports; check the DOI resolves to the stated title; drop what cannot be verified.
  Merge the thresholds section from the plan 5 methods doc.
- [ ] **Step 4: Write** the four files (`reference.md` command map generated from `.venv/bin/pace schema` at authoring time) and the README install section:

```markdown
## Claude Code skill

ln -s "$PWD/.claude/skills/pace" ~/.claude/skills/pace

The skill loads in any Claude Code session when you talk about training or your Garmin data.
`pace` must be on PATH (it is when this repo's `.venv/bin` is on PATH, or after `uv tool install -e .`).
```

- [ ] **Step 5: Run** full suite and ruff -> green.
- [ ] **Step 6: Read SKILL.md once as an unattended run** (Review Focus 2) and fix any step that needs a human at a terminal.
- [ ] **Step 7: Commit** `git commit -m "feat: pace coaching skill"`.

---

### Task 2: `pace load --export`

**Files:**
- Modify: `pace/commands/load.py`, `pace/commands/schema.py`, `docs/CONTRACT.md`
- Test: `tests/test_load_cmd.py`

**Interfaces:**
- Consumes: plan 3 `pace.load.aggregate/default_range/fetch_inputs` (week rows carry `actual`, `planned`, `projected`; day rows `effective`, `rolling7`), plan 2 `pace.plan`, `pace.matching`.
- Produces: `pace load --export PATH` (usual load flags except `--sport`, which is `bad_args` with `--export`; both sports computed) writes one JSON file `{"rows": [...], "meta": [{"label": "Generated", "value": ISO datetime}, {"label": "Command", "value": "pace load --export"}]}` where each row has `table` in `week | day | next`:
  - `week`: `week`, `start`, `sport` (`running` | `all`), `actual_km/elev_m/hours`, `planned_km/elev_m/hours`, `projected_km/elev_m/hours`, `remaining_km/elev_m/hours` (= projected - actual for the current week, projected for future weeks, 0 for past weeks), `missing_km/elev_m/hours`; null where a side is absent.
  - `day`: `date`, `sport`, `projected` (date > today), `rolling7_km/elev_m/hours`.
  - `next`: at most one row: the first session dated today or later that is not `skipped` or `rest`, not already matched as done, and its `date`, `title`, `sport`, `intensity`, `duration_s`, `distance_m`, `status`.
  Returns `{"path": PATH, "rows": n}`.

- [ ] **Step 1: Write failing test**: export file shape, both sports, `remaining` rule for past/current/future weeks, `projected` true only after today, `next` skips rest and done-today, `--sport` with `--export` -> exit 2.
- [ ] **Step 2: Run** -> fail. **Step 3: Implement**. **Step 4: Run** full suite and ruff -> green.
- [ ] **Step 5: Commit** `git commit -m "feat: pace load --export for the dashboard"`.

---

### Task 3: Build the dashboard (controller only; Artifact tools, with the user)

- [ ] **Step 1:** Create the artifact from the Data type (`type_url` from the quickstart listing, title "Training Load", `auto_open: "after_first_write"`); read the type instructions it returns and the references it names (`design-defaults.md`, `one-writer.md`, `sources.md`, `features.md` for filters and `page`).
- [ ] **Step 2:** Make sure the real plan has at least one future session (add a temporary one if needed and remove it after Step 5); `pace load --export $PACE_HOME/dashboard/load.json`; upload it as an asset.
- [ ] **Step 3:** One `write_db` batch: `dash/meta`; dataset `load` (`title`, `description`, `source: {kind: "file", url, name, format: "json"}`, `updated: {at, by: "pace coach"}`); calcs or loaders splitting it into week, day and next rows; page filters `metric` (km, D+, hours) and `sport` (Running, All); `files/index.html`: weekly bars (past `actual` solid; current `actual` solid plus `remaining` lighter; future `projected` lighter), the `rolling7` line at daily resolution (solid to today, dashed after), tooltips, the "This week" line (actual vs projected in the selected metric) and the next-session line, all marked with `data-source` / `data-field`; sized from `clientWidth`, works at about 400 px.
- [ ] **Step 4:** Read `check`, `runs` and `page` until the page passes; fix what they report.
- [ ] **Step 5: Refresh test**: note a current-week session's target, change it, export, upload, update the dataset `url` and `updated` in one batch, delete the replaced asset; read `page` and confirm the printed "This week" projected value changed and `runs` reports no mark mismatch; then restore the original target and refresh again.
- [ ] **Step 6:** `pace diary set dashboard.url <url>`; write `dashboard.md`: the refresh exactly as run in Step 5 (export path `$PACE_HOME/dashboard/load.json`, upload, one update batch, delete the replaced asset, read `check` once; if its `at` is not newer, nobody has the page open: move on, never loop), when to refresh (end of full check-in, once after a batch of plan edits), what to do when `dashboard.url` is unset (offer to create; never unattended), how to change the page on the user's request (and record a diary `note`), deep-dive artifacts as separate plain pages, and the project-2 note that the unattended session needs the Bash `pace`, Artifact upload/delete and ArtifactData batch permissions.
- [ ] **Step 7:** Commit `git commit -m "docs: dashboard procedure in the skill"`.

---

### Task 4: Install and smoke test (controller only, with the user)

- [ ] `ln -sfn /home/matteo/projects/trainscout/.claude/skills/pace ~/.claude/skills/pace` (ask first if `~/.claude/skills/pace` exists and is not this symlink); check the skill does not load twice in this repo (project skill + user skill).
- [ ] Smoke 1 (Review Focus 1), in a fresh Claude Code session with `PACE_HOME=$(mktemp -d)`: "let's plan my training" -> intake, one open question at a time.
- [ ] Smoke 2 (Review Focus 3), with the real `PACE_HOME`: "hi" -> run review for runs since `checkin.last`, one line each, no RPE question when set; reply nothing for one -> "no comment" recorded; a second session does not raise it again.
- [ ] Smoke 3: disagree with one piece of advice -> `decision` entry with `--due` today; ask "check in" -> the due decision surfaces and is closed.
- [ ] Fix what the smoke tests show; rerun `tests/test_skill.py`; commit `git commit -m "fix: skill adjustments from first live sessions"`.
- [ ] Spec post-hoc notes for sections 4, 5, 7 (export shape, dataset and filter names, chart path rule, FR55 data limits, refresh debounce); commit `git commit -m "docs: post-hoc notes for the coach skill and dashboard"`.
