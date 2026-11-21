# Coach: Diary, Training Plan, Charts and Skill: Design

Date: 2026-11-17
Status: approved by the user, 2026-11-17.
Extends `docs/superpowers/specs/2026-09-04-llm-cli-design.md` (sections 5 skill, 8 diary).
Supersedes the unimplemented plan `docs/superpowers/plans/2026-09-06-diary-and-skill.md`: its diary design is kept and extended here, and a new plan replaces it.

## Why

The user wants a running coach that lives on their laptop, reads their Garmin data, builds a training plan together with them, asks how workouts went, and adjusts the plan from their answers and the data.
The long-term shape is a proactive coach reached through a dedicated Telegram bot.
This spec is the first of three projects:

1. **Coach brain (this spec):** athlete diary, training plan, charts, and the Claude Code skill that coaches.
   Usable from a normal Claude Code session.
2. Proactive runtime: a separate Telegram bot (its own BotFather bot, its own `TELEGRAM_STATE_DIR`, its own Claude Code session, not the bot used for leftovers/whatdidido) that runs check-ins on a schedule.
3. Optional data webapp (Telegram Mini App), only if daily interactive dashboards turn out to be wanted.

Projects 2 and 3 get their own brainstorm.
Everything in project 1 must work the same when project 2 drives it, so no step may depend on the user sitting at a terminal.

## What the coach is

- **Collaborative, not prescriptive.**
  The user brings goals, preferences, constraints and opinions.
  The coach does not adopt one named system (Daniels, Pfitzinger, 80/20, Lydiard, ...) and impose it.
  It builds the plan with the user from well-tested principles.
- **Evidence-grounded and willing to challenge.**
  When the user proposes something that goes against established principles, research, or the user's own history, the coach says so: the concern, the reason, the principle or source, and how confident the evidence is.
  Then the user decides.
  The decision and the user's reason are recorded; the coach later checks whether it worked.
- **Gets to know the athlete itself.**
  Intake (running history, current volume, PBs, goals, races, availability, injuries, other sports, strength routine, preferences) is the skill's job, done conversationally and stored in the diary.
- **Running first.**
  Plans are built for running.
  The coach also advises on other endurance sports (cycling, swimming, hiking) and on strength and general wellbeing (sleep, recovery, weight), and can put those in the plan as sessions.
- **Checks in.**
  It asks how each workout went (RPE, feel, free comment) and keeps body weight current.

## Goals

1. Durable athlete memory: profile, intake, decisions, feedback, constraints, injuries (`pace diary`).
2. A structured training plan stored in pace, built and edited one session at a time, with optional batches, not tied to weeks (`pace plan`).
3. Push individual plan sessions to Garmin as scheduled structured workouts, one at a time or several in one call.
4. Planned versus done: match Garmin activities to plan sessions and report done, missed, modified, unplanned.
5. Charts as PNG files (`pace chart`), usable in a terminal and sendable as Telegram photos later.
6. A persistent training load dashboard as a private Claude artifact, kept current by the skill; further deep dives as artifacts on request.
7. The coaching skill: data access, intake, coaching principles with pushback, a review after each run, check-ins (weight, drift, decisions), presentation.
8. Lactate thresholds and training zones: estimated from past runs, refined by field tests, overridden by lab tests, kept in sync on the watch (section 7).

## Non-goals

- Telegram bot, scheduling, background running (project 2).
- Telegram Mini App or any self-hosted web UI (project 3).
- Derived load models (CTL/ATL/TSB, ACWR) as CLI features.
  Weekly sums for charts and status are in scope; anything beyond is computed inline by the skill with the arithmetic shown, as today.
- Pushing strength or other non-DSL sessions to Garmin: the workout DSL covers running, cycling, swimming, walking, hiking only.
  Strength sessions live in the plan as text.
- Garmin's own training plans (Garmin Coach, adaptive plans): pace keeps its own plan.
- Medical diagnosis.

## Anti-goals

- A coach that imposes a system or overrides the user's call after the user has decided.
- A coach that agrees with everything: pushback on evidence-backed concerns is required, not optional.
- Invented citations or overstated evidence.
  If the skill is not sure a source says something, it states the principle without a citation, or looks it up.
- Garmin and the plan drifting apart: a session pushed twice creating duplicate workouts on the watch, or a pace session deleted while its Garmin workout stays scheduled.
- Writes the user did not approve (the section 7 write contract of the 2026-09-04 spec stays unchanged).
- Nagging: a check-in question (feedback on one activity, weight) is asked once per session and not repeated after the user declines it.

## Constraints

- All existing CLI contracts hold: one compact JSON envelope on stdout, error codes and exit codes, `--dry-run` on every Garmin write with the exact payload, writes never read the cache, success invalidates cache keys, no `--yes` flag, Garmin writes go through `pace/writes.py` `run_write`.
- Local stores (diary, plan) are files under `$PACE_HOME` with atomic writes (`*.tmp` + `os.replace`), corrupt-file detection, never overwritten when corrupt (same rules as plan 3's diary).
  Local writes need no `--dry-run`; they are reversible through the same commands.
- Every new command is listed in `pace schema` (drift test).
- New dependency: `matplotlib` (user approved), rendering with the non-interactive `Agg` backend.
- ASCII only in skill and docs; markdown one sentence per line.

## Approaches considered

| | Approach | Trade-off |
|---|---|---|
| A | Plan as free-text diary entries; skill does the rest. | Cheapest, but done-vs-planned is guesswork and projects 2 and 3 cannot read the plan. |
| **B (chosen)** | **Structured `pace plan` store next to the diary, with session-level commands, Garmin push, and status matching.** | **Bigger build and a schema to get right; makes "adjust based on how workouts went" rest on data.** |
| C | Markdown files (`athlete.md`, `plan.md`) the agent edits directly. | Flexible and readable, but breaks the envelope contract and leaves matching and later UIs parsing prose. |

The user chose B, with one change: set and push work one session at a time (batches optional), not week by week.

Charts, considered:

| | Approach | Trade-off |
|---|---|---|
| 1 | Telegram Mini App. | Interactive, but needs public HTTPS (tunnel or hosting), auth on Telegram `initData`, a frontend; down when the laptop sleeps. Deferred to project 3. |
| **2 (chosen)** | **PNG from `pace chart`.** | **No hosting, works now and in Telegram as photos; not interactive.** |
| **3 (chosen)** | **Private Claude artifact: one persistent dashboard plus on-request deep dives.** | **Interactive, no hosting; data goes to claude.ai, which the user accepts (same risk class as Garmin's servers).** |

## 1. Diary (`pace diary`)

The plan 3 design (2026-09-04 spec section 8, plan `2026-09-06-diary-and-skill.md` Global Constraints) is kept: `$PACE_HOME/diary.json`, `show`, `set`, `add`, `remove`, incrementing never-reused ids, atomic writes, corrupt-file handling, `active` = constraint and injury entries.

Changes:

- Profile keys extended for intake (designer's call: fixed keys keep the profile predictable for the skill and later UIs; free text inside each key keeps it flexible):
  `goal`, `event.name`, `event.date`, `availability`, `background` (running history, current weekly volume, PBs), `other_sports`, `strength`, `preferences` (likes, dislikes, preferred long-run day, terrain, ...), `checkin.weight_days` (integer, default 7 when unset), `dashboard.url` (section 4).
- Entry kinds extended: `note | plan | recommendation | feedback | constraint | injury | decision`.
- Constraints and injuries can be resolved (user's call, 2026-11-17): `diary resolve ID [--date D]` sets a `resolved` date; a resolved entry leaves `active` but stays in the diary as history the coach learns from (for example how long an injury took to heal).
  `--reopen` clears it.
  `decision` records a pushback outcome: what the coach advised, what the user chose, the user's reason, and what to check later.
  Text is free-form; the skill writes it in the shape `advised: ... / chose: ... / because: ... / check: ...`.
- Entries may carry an optional `ref` naming what they are about: `session:<id>` or `activity:<id>`.
  `diary show` gains `--ref R` to filter entries by it.
  Purpose: workout feedback is attached to the session and activity it concerns, so the coach can read it back next to the data.
- Post-hoc changes made while building (2026-11-17):
  `refs` is a list (feedback concerns an activity and a session), and it also accepts `entry:<id>`;
  `diary set-due ID --date D | --clear` and `diary unset KEY` exist;
  `diary resolve` is implemented as above;
  schema marks diary writes `local_write` (not `write`), so the Garmin confirmation rule and `--dry-run` loop stay untouched.
- Entries may carry an optional `due` date (designer's call).
  `decision` entries set it to when the outcome should be checked.
  `diary show` returns a `due` list: entries whose `due` is today or earlier and that have no later entry closing them (the skill closes one by `diary set-due ID --clear` or by removing the date; builder picks the mechanism).
  Purpose: due checks surface at session start without the model rereading every decision, which matters in unattended project 2 runs.
- Check-in state is stored, so a new session (and every scheduled project 2 run) knows what was already asked (designer's call):
  profile key `checkin.last` (ISO datetime of the last completed check-in), set by the skill at the end of the check-in;
  an unanswered feedback question is recorded as `diary add --kind feedback --ref activity:<id> "no comment"`, which counts as handled.
  On the first run (`checkin.last` unset) the check-in looks back 7 days only.
  `activity list` does not carry RPE/feel, so finding unrated activities costs one `activity show` per activity (rate limit 10 per minute); the `checkin.last` bound keeps that small, and activities that already have a `feedback` entry are skipped without a call.

## 2. Plan (`pace plan`)

### Store

`$PACE_HOME/plan.json`, one current plan.

```json
{"plan": {"name": "Spring half 2027", "goal": "sub-1:45 half", "event_date": "2027-04-11",
          "created": "2026-11-17", "updated": "2026-11-17"},
 "blocks": [
   {"id": "b1", "name": "Base", "phase": "base", "start": "2026-11-23", "end": "2027-01-17",
    "focus": "aerobic volume, strides, 2x strength"}],
 "sessions": [
   {"id": 12, "date": "2026-11-25", "sport": "running", "title": "Easy + strides",
    "purpose": "aerobic base, neuromuscular", "intensity": "easy", "block": "b1",
    "target": {"duration_s": 3000, "distance_m": null, "elevation_m": null},
    "workout": {"name": "...", "sport": "running", "steps": [...]},
    "notes": "", "status": "planned",
    "garmin": null, "activity_id": null}],
 "next_id": 13}
```

- `sport`: the DSL sports plus `strength`, `mobility`, `other`.
- `intensity`: `rest | easy | moderate | hard`, used for the intensity-distribution view.
- `workout`: optional workout DSL object (the existing `pace workout push` format); required only to push.
- `status`: stored states are `planned | pushed | skipped`; `done`, `missed`, `modified` are derived by `plan status` (below), never stored.
  `skipped` means the user decided not to do it (recorded, not a failure).
- `garmin`: after a push, `{"workout_id": ..., "schedule_id": ...}` (whatever ids Garmin returns that are needed to unschedule and delete).
- `activity_id`: optional manual link to the activity that fulfilled the session; overrides automatic matching.
- Blocks are optional grouping for periodization; sessions need not belong to one.
- Starting a new plan (`plan new`) moves the current file to `$PACE_HOME/plans/<created>-<slug>.json` before writing the new one.
  Old plans stay readable (`plan show --archived NAME`, builder may simplify) so the coach can learn from past blocks.

### Commands

Command names and semantics are the design; flag spelling is the builder's.

| command | effect |
|---|---|
| `plan new --name N [--goal G] [--event-date D]` | archive current plan, start an empty one |
| `plan show [--from D] [--to D] [--days N]` | plan header, blocks, sessions in range (default: from 7 days ago to 28 days ahead) |
| `plan meta KEY VALUE` | set `name`, `goal`, `event_date` |
| `plan block add/set/remove` | manage blocks |
| `plan add FILE` | add one session or a list of sessions from a JSON file or stdin; returns them with ids |
| `plan set ID [fields...]` | edit one session (date, title, purpose, intensity, target, workout, notes, block, status, activity) |
| `plan remove ID...` | remove sessions |
| `plan push ID... [--dry-run]` | for each session: create the Garmin workout from its DSL and schedule it on the session date; store `garmin` ids; status `pushed` |
| `plan unpush ID... [--dry-run]` | delete the Garmin workout (and its schedule); clear `garmin`; status `planned` |
| `plan status [--from D] [--to D] [--days N]` | planned vs done for the range (below) |

Push rules (these prevent drift, an anti-goal):

- Pushing a session that is already `pushed` replaces it: delete the old Garmin workout, push and schedule the new one.
  This is how a changed date or changed workout reaches the watch.
- `plan set` on a `pushed` session that changes `date` or `workout` succeeds locally and flags the session `stale: true` in its output and in `plan show` / `plan status`; the skill then pushes it again.
- `plan remove` of a `pushed` session is refused with a hint to `unpush` first.
- A batch push or unpush runs session by session and reports per-session results; one failure does not roll back the others (Garmin writes are not transactional and not idempotent; see `writes.idempotency`).
- Deleting a pushed workout is within the "deletes only for resources this CLI creates" rule.
- Pushed workouts stay in the Garmin library after their date (user's call): no automatic cleanup, because deleting a workout erases its calendar history.
  Their names are prefixed `pace: ` so they stand apart in the Garmin app and a bulk cleanup stays possible later.
- `plan new` is refused while the current plan has pushed sessions dated today or later (user's call); the hint says to `unpush` them first, which the skill does after the user's yes.
  Past pushed sessions are left on Garmin.
- Verified live on 2026-11-17: deleting a workout (`DELETE /workout-service/workout/{id}`) also removes every calendar schedule of it, past and future (both schedule ids return 404 afterwards, and neither appears in the calendar).
  So `unpush` needs only the workout delete, and deleting a past session's workout would erase that day from the Garmin calendar.
- Watch sync, verified the same day with the user's watch: a pushed and scheduled workout reaches the watch only through the training calendar (past and future days both shown); it does not appear in the watch's Workouts list.
  pace never calls "send to device", so pushes do not clutter the watch.
- Garmin calendar read, verified the same day: `GET /calendar-service/year/{Y}/month/{M}` with `M` 0-indexed returns `calendarItems`; a scheduled workout has `itemType: "workout"`, `id` (= the schedule id), `date`, `title`, `workoutId`.

### Status matching

`plan status` reads activities for the range (`activity list`, cache rules apply) and produces, per day:

- `done`: a session matched to an activity, with planned target vs actual (duration, distance, avg pace, avg HR) and RPE/feel when set.
- `modified`: matched, but actual duration or distance differs from target by more than 20 % (builder may tune; state the threshold in `pace schema`).
- `missed`: past date, no match, not `skipped`.
- `skipped`, `planned` (future), `pushed` (future).
- `unplanned`: activities with no session.

Matching: a session's manual `activity_id` wins.
Otherwise, same local date and same sport family (running includes treadmill and trail; the builder maps Garmin activity type keys), one-to-one, closest target duration first.
Strength/mobility sessions match Garmin strength or similar activity types where they exist, else stay unmatched unless linked manually.

A new read command `workout calendar [range flags]` returns the scheduled workouts in the Garmin calendar for a range (endpoint verified live, see push rules above), so the skill can check an unplanned activity against what Garmin had scheduled.

Output also carries range totals: planned vs actual time and distance per sport, and time per intensity (planned) for the intensity-distribution check.

### Decisions after plan review (user, 2026-11-17)

- A `rest` session is never `missed`; a past unmatched strength, mobility or other session is `unconfirmed` and the coach asks about it at check-in.
  The user can mark a session `done` by hand when Garmin has no activity for it (stored status `done`).
- `plan push` skips a session that is already pushed and unchanged; `--force` replaces it anyway.
- Sessions dated in the past are never pushed, re-pushed or unpushed (deleting the workout would erase that day from the Garmin calendar).
  Refined after the plan 2 review: push needs a session date of today or later; unpush (and the delete step of a re-push) is refused only when both the session date and the day its workout occupies on Garmin (`pushed_as.date`) are in the past; a past session that still has its Garmin workout can be marked `skipped` or `done`; `plan remove` and `plan new` wait for any push in flight.
- E-bike activities count as cycling; the status carries activity type and average HR so the coach can judge whether the effort was comparable.
- `plan status` includes RPE/feel for matched and unplanned activities (one cached call per activity; the user logs both after every run).
- An activity's `workoutId` links it to the pushed session it was run from; that link wins over duration-based matching.

## 3. Weekly load (`pace load`) and charts (`pace chart`)

### Weekly load

`pace load [--weeks N] [--ahead M] [--sport running|all]` returns two series over the same range:

- `weeks`: one row per ISO week (Monday start, local time):
  `{"week": "2026-W47", "start": "2026-11-16", "actual": {"km": .., "elev_m": .., "hours": ..}, "planned": {"km": .., "elev_m": .., "hours": ..}}`.
- `days`: one row per day with the same `actual` / `planned` sums for that day, plus `rolling7`: the sum of the 7 days ending on that day, in each metric.
  For days up to today `rolling7` uses actual values; for future days it uses actual values for past days in the window and planned values for today onward, so the line continues into the projection without a jump.
  (Today counts actual if any activity is recorded, else planned; the builder states the exact rule in `pace schema`.)

- `actual` sums Garmin activities (distance, elevation gain, moving time) for past and current weeks.
- `planned` sums plan session targets (`distance_m`, `elevation_m`, `duration_s`) for current and future weeks; sessions marked `skipped` are excluded.
  A target left null contributes nothing and the row says how many sessions lacked it (`planned.missing`), so a projection with gaps is visible.
- Defaults: 16 weeks back, ahead to the plan's last session (at most 26 weeks); `--sport running` (running family as in plan status) by default.
- The rolling line is a 7-day sum computed daily (user's call): it moves within a week and equals the week's bar on each Sunday.

This is the one source of weekly numbers for the dashboard, the `volume` chart and the skill.

### Charts

`pace chart KIND [range flags] [--out PATH]` renders one PNG with matplotlib and returns an envelope with `data: {"path": ..., "kind": ..., "range": ..., "summary": {...}}`.
`summary` carries the numbers plotted (compact), so the agent can describe the chart without reading the image.
Default output directory `$PACE_HOME/charts/`, file name `<kind>-<from>-<to>.png`; `--out` overrides.
Data comes through the same fetch and cache paths as the read commands.

Initial kinds:

| kind | shows |
|---|---|
| `volume` | the same view as the dashboard (below), one metric per image (`--metric km|elev|hours`) |
| `plan` | planned vs done per session over the range, colored by status |
| `intensity` | weekly time by intensity (planned) and by HR zone (actual, where Garmin provides time in zones) |
| `hrv` | overnight HRV and resting HR, with Garmin's baseline band where present |
| `sleep` | sleep duration and score |
| `weight` | weigh-ins with a 7-day rolling mean |
| `activity ID` | one activity: pace, HR, elevation over distance |

One fixed visual style (palette, fonts, sizes legible on a phone screen, 1200 px wide), so every chart looks like the same product.
For one-off questions outside these kinds, the skill may render an ad hoc chart from `pace` JSON with a short Python script; the fixed kinds are the default.

In a terminal session the skill passes `--out` into the current repo root (the user opens files from there) and gives the path.
In project 2 the same file is sent as a Telegram photo.

### Post-hoc notes (plan 3, implemented 2026-11-17)

- `pace load` takes `--weeks N` (back, default 16) and `--weeks-ahead N` (default through the plan's last session, at most 26), not `--ahead`; rows carry `actual`, `planned` (with `missing`), `projected` and per day `effective` and `rolling7`; the data includes `today`.
  `rolling7` on a week's Sunday equals that week's `projected`; the aggregation reads 6 days before the first row so the first rolling values are complete.
- Chart kinds as built: `volume` (weeks flags, `--metric km|elev|hours`, `--sport`), `plan`, `recovery`, `sleep`, `weight`, `intensity` (`--sport`, default 56 days back and 14 ahead snapped to whole ISO weeks), `activity ID`.
  The `hrv` kind became `recovery`: daily resting HR with a 7-day mean, HRV on a second axis only when the watch records it (the Forerunner 55 does not); the sleep score is drawn only when present.
- Health charts read the new `pace wellness sleep-range` (sleep-stats range endpoint, fixed 28-day windows) instead of per-day reads, which keeps them inside the rate limit.
- Volume file names carry the metric and the sport (`volume-<metric>[-all]-<from>-<to>.png`); without planned sessions the rolling line stops at today.
- matplotlib (3.11.2, PyPI) is imported only when a chart is drawn; `.python-version` pins Python 3.14.

## 4. Artifacts

The user accepts their training data on claude.ai (private artifacts); no per-publish confirmation is needed.

### Training load dashboard (persistent)

One private artifact, created once from the account's "Data" artifact type (dashboard template), its URL stored in the diary profile as `dashboard.url` so every session (and project 2) updates the same page.

Default content, minimal by design (user's spec, extras are designer's call):

- **Training load chart:** weekly bars, past weeks solid (actual), current and future weeks lighter (planned), the current week showing actual against planned.
  A line drawn over the bars at daily resolution: the rolling 7-day sum (`days[].rolling7`), so it shows load building or dropping inside a week and meets each bar's top on Sunday; solid for past days, dashed for projected days (user's call).
  A switch for the metric: km, D+ (elevation gain, m), hours.
  Modeled on Strava's weekly progress chart, with the rolling 7-day line overlaid on the week bins.
- **Sport switch** (designer's call): running (default) or all sports.
- **This week line** (designer's call): done vs planned for the current week in the selected metric, and the next planned session.

Data: the skill runs `pace load` (both sport settings) and `plan show` for the next session, and publishes them as the artifact's data, following the Data type's own instructions for how data reaches the page.
The skill refreshes it at the end of a check-in and after plan changes.
If the Data type cannot draw the bar plus moving-average overlay or the metric switch, the builder falls back to a plain artifact page with a charting library, keeping the same URL rule.

The page belongs to the user's preferences: the skill may add, change or remove views when the user asks, and records the change as a `note` in the diary so later sessions keep it.

### Deep dives

On request ("show me my last block in detail"), the skill builds a separate artifact for the question, from data fetched through `pace`.
Data on the page stays minimal: what the question needs, no raw dumps.

No CLI work for artifacts beyond `pace load`.
Project 2 risk, noted for its brainstorm: the scheduled Telegram session must have the Artifact tool to refresh the dashboard.

### Post-hoc notes (plan 4, skill and dashboard, implemented 2026-11-19)

- Section 4: `pace load --export PATH` writes `{"rows", "meta"}` for the dashboard; rows carry `table` = `week`, `day` or `next`, both sports, and meta has `Generated` (ISO time with UTC offset) and `Command`.
  The export shape is in `docs/CONTRACT.md` (`### Export`) and the procedures in `.claude/skills/pace/dashboard.md`.
- The Data artifact holds dataset `load` with calcs `weeks`, `days`, `thisweek` and `nextsession`, and the page filters `metric` (`km`, `elev_m`, `hours`) and `sport` (`running`, `all`).
- Charts: a terminal session writes PNGs to the repo root through `--out`; an unattended run omits `--out` and gets `$PACE_HOME/charts/` (see `SKILL.md`).
- Section 5, Forerunner 55 limits: no HRV status, no sleep score and no Garmin lactate threshold; the skill uses resting HR, sleep, RPE/feel and load, and HRV only when present.
- Dashboard refresh is debounced: at the end of a full check-in and once after a batch of plan edits, not after each edit.
- Section 7: the run review judges intensity from `hr_zones_s` when `pace zones show` reports `in_sync: true`, otherwise from the HR series of `pace activity details` against pace's LT1 and LT2.

## 5. Skill (`.claude/skills/pace/`)

`SKILL.md` plus reference files loaded on demand: `reference.md` (command map, presentation tables, from plan 3) and `coaching.md` (principles and sources).
Installed by symlink into `~/.claude/skills/pace` as planned.

### A. Data access

As in the 2026-09-04 spec section 5A, extended with `diary`, `plan`, `chart`.
Presentation contract unchanged (scope line, one table, at most 3 notes, ASCII), plus: when a chart answers the question better than a table, render it and give the path; the table stays for exact numbers.

### B. Session start: run reviews and check-in

Revised by the user on 2026-11-17: the coach checks on the user after each run, and the full check-in runs only when it is due.

**Run review (every session start, and in project 2 after each synced run).**
Run together: `auth status`, `diary show`, `diary show --kind feedback --since <checkin.last date>`, `plan status --from <checkin.last date, at most 21 days back; 7 days on first run> --ahead 7`.
For each activity since the last review that has no `feedback` entry, the coach writes one short, specific opener built from the data, not a generic question:
- matched and on target: "Your easy run looked good, anything to add?"
- matched but off target (intensity vs HR zone time, duration or distance vs target, RPE vs the session's intent): "I think you ran your easy run a bit too hard (mostly zone 3-4, RPE 6). Was there a change of plans?"
- unplanned: first read the Garmin calendar around its date (did the user move the planned workout?), then ask what it was.
Several pending runs go in one message, one line each.
RPE and feel already on the activity are used as given (the user logs them on the watch); only when they are missing does the coach ask, and the user's own statement is the approval for that one `activity effort` write (the skill echoes what it wrote).
The answer goes to `diary add --kind feedback --ref activity:<id>` (and `session:<id>` when matched); no answer, or "nothing", is recorded as feedback "no comment", so the run is not raised again.
Reported pain becomes an `injury` entry after the user confirms the wording.
Zones for the intensity judgement come from section 7 (current thresholds), falling back to the Garmin HR zones.

**Due entries (every session start, after the run review).**
Each entry in `due` from `diary show` gets one line, whether or not a full check-in is due; due decisions are checked against the data and discussed.
A settled entry is closed (`diary set-due ID --clear` plus a follow-up entry with `--ref entry:<id>`); one the user puts off gets a new due date.
Follow-ups agreed for a later day (a weigh-in tomorrow, the outcome of a visit) are recorded right away as a `note` with `--due`, so the next session knows about them.

**Full check-in (when the user asks for one, or `checkin.last` is dated before today).**
After the run review, in this order, each at most once per session:

1. Intake gaps: if profile keys the coach needs are empty, ask (open questions, one at a time), then `diary set`.
   A full intake happens on the first session; afterwards only gaps and changes.
2. Weight: if the latest weigh-in (`log weight list`) is older than `checkin.weight_days`, ask for it.
   A number the user gives is the approval for one `log weight add`.
   The coach never comments on weight in a way that pushes restriction; see guardrails.
3. Plan drift: missed, modified, unconfirmed, stale or unplanned items from `plan status` are mentioned and, when they matter, lead to a proposed adjustment; apart from the calendar check above, pace assumes nobody edits pushed workouts on Garmin (user's call).
4. Thresholds: if section 7 says a retest or a new estimate is due, propose it.
5. End: `diary set checkin.last now`, refresh the dashboard (section 4).

A quick data question ("how did I sleep?") gets the run review if runs are pending, then its answer, without the rest of the check-in.

### C. Coaching

- **Building the plan with the user:** ask what the user wants (goal, race, timeline, how they like to train), propose structure (blocks, weekly pattern, key sessions) with reasons, adjust to the user's input, then add sessions with `plan add`.
  Push to Garmin only sessions the user approves, typically the next few days, via `plan push`, following the write contract (show, get a yes).
- **Adjusting:** after feedback or drift, propose one concrete change with its reason, apply with `plan set` on yes, re-push stale sessions on yes.
  Record the change as `recommendation` (or `decision` if the user overrode advice).
- **Principles** (`coaching.md`), each with its evidence strength and sources the builder verifies before writing them in: progressive overload and gradual volume increase (and the weak evidence behind the "10 % rule"); intensity distribution (polarized and pyramidal models, mostly easy volume); specificity toward the event; periodization and recovery weeks; taper; long-run progression; strength training for runners (running economy, injury risk); sleep and recovery markers (HRV trends, resting HR) as context, not verdicts; low energy availability / RED-S as a reason never to push weight loss during heavy training; load-ratio metrics such as ACWR and their published critiques.
  When a claim needs a source the file does not have, the skill may look it up on the web and cites what it found, or says it states the principle without a citation.
- **Pushback protocol:** when the user's choice conflicts with a principle or with their own recorded history, state: the concern, the reason, the principle or source, and how strong the evidence is (strong, moderate, weak/expert opinion).
  Offer an alternative.
  Then accept the user's decision without repeating the argument, record a `decision` entry, and check it at the point named in `check:`.
  Also challenge in the other direction: if the user's plan is sound and the coach's own earlier advice was weaker, say so.
- **Using history:** before proposing load changes, look at the user's recorded responses (feedback, decisions, past plan status, HRV/sleep around hard weeks) and say what in their history supports the proposal.
- **Framing:** observations, then one main recommendation, then what to watch next.
- **Guardrails:** not medical advice; persistent HRV drop, rising resting HR, poor sleep, or pain suggest rest or easy days and, for pain that persists or worsens, seeing a professional; never diagnose; no weight-loss targets during heavy training blocks; no dieting advice beyond general guidance.

### D. Other endurance sports, strength, wellbeing

Advise on them with the same principles and the same pushback protocol.
They enter the plan as sessions (`sport` other than running); cycling, swimming, walking and hiking may carry a DSL workout and be pushed; strength and mobility stay text.

## 6. Tests and docs

- Diary: plan 3's tests plus new profile keys, `decision` kind, `ref` and `--ref` filter.
- Plan store: add/set/remove, id allocation, block validation, session validation (date, sport, intensity, DSL validated by the existing translator on add/set), archive on `new`, corrupt file.
- Plan push/unpush: dry-run payloads, replace-on-repush, refuse remove of pushed, stale flag, batch partial failure reporting (fixture-driven, no network).
- Plan status: matching rules (manual link wins, sport family, one-to-one, closest duration, modified threshold, missed vs skipped, unplanned), totals.
- Load: week bucketing (Monday start, local time, week spanning a month or year boundary), actual vs planned split for the current week, skipped excluded, null targets counted in `missing`, sport filter, `rolling7` equals the week total on Sundays, the actual-to-planned switch at today.
- Chart: each kind renders a non-empty PNG from fixtures; `summary` numbers equal the plotted data; `--out` honored.
- Schema drift test covers new groups; skill drift test (plan 3) covers `plan` and `chart`.
- Docs: `docs/CONTRACT.md` (diary, plan, chart sections), `README.md` (commands, skill install), this spec's post-hoc notes after implementation.

## 7. Thresholds and zones (added 2026-11-17)

The coach helps the user find their lactate thresholds (LT1, LT2) and keeps training zones current.

### Sources of a threshold record, and precedence

- **Estimate** from past workouts, computed by pace (default, kept current as data arrives).
- **Field test**: a specific test run the coach plans with the user (for example a heart-rate drift / aerobic decoupling run or a DFA alpha1 ramp for LT1, a time trial for LT2), analyzed by pace.
- **Lab test** reported by the user (values typed in).

A record holds: date, source, method, LT1 and LT2 as heart rate and pace where known, max and resting HR used, the activities it came from, a confidence note.
The **current** records are the ones the zones come from, one per threshold (the current LT1 and the current LT2 may come from different records); it changes only on the user's yes, except that a reported lab test becomes current, for each threshold it has, when entered (unless it is older than that threshold's current record).
A lab test stays current until the user accepts another record, but is flagged expired after 6 months (user's call); the coach then suggests a retest and shows newer estimates or field tests next to it without applying them until the user accepts one.

### Methods

The normative source for every method (formulas, evidence grades, verified sources, worked examples) is `docs/superpowers/specs/2026-11-17-zones-methods.md`; `coaching.md` carries a summary of it (written in plan 4).
Constraints that are already known:
- The user's Forerunner 55 has no Garmin lactate threshold detection, and its wrist sensor gives no beat-to-beat data.
  With the chest strap, though, the original FIT file of a run holds RR intervals in `hrv` messages (verified on the user's account, since about September 2026), so DFA alpha1 is usable on strap runs (methods doc sections 6 and 9a).
- The user runs with a chest strap on almost every run (24 of the last 25, checked 2026-11-17); each activity's `metadataDTO.sensors` shows a heart-rate sensor (`bleDeviceType: HEART_RATE`) when the strap was used.
  Threshold estimates use strap runs only, or weight wrist runs down, and say which they used.
- Candidates to check, not to trust: critical speed from best efforts or race results for LT2; average HR of the last 20 minutes of a 30-minute time trial for LT2 HR; HR drift / aerobic decoupling (pace:HR) over a long steady run for LT1.

All arithmetic (drift %, critical speed, zone boundaries) is done by pace, deterministically; the coach explains it.

### Zones

Zones are derived once both thresholds have a current record, from the current LT1 and the current LT2: a three-zone model (below LT1, between, above LT2) and a five-zone mapping for the watch, with the mapping rule documented in `pace schema`.
HR zones and pace zones are both kept; HR is primary (terrain distorts pace).
pace keeps the watch's HR zones in sync with the current zones (user's call): a Garmin write, shown and confirmed like every other.
Garmin's current zones are readable today (`GET /biometric-service/heartRateZones`: method, max and resting HR used, five zone floors); the write endpoint is verified live before it is built.
Run reviews (section 5B) and workout DSL HR targets use pace's zones once both thresholds have a current record, Garmin's zones before.

### Commands (names are the design; flags the builder's)

| command | effect |
|---|---|
| `zones show` | current record per threshold, derived zones, history (all records), and Garmin's zones on the watch |
| `zones estimate` | estimates from recent history per method, with the activities used and confidence; logs a summary locally, no Garmin write |
| `zones analyze ACTIVITY_ID --test KIND` | analysis of a test run (drift, time trial, ramp) |
| `zones test-workout KIND` | a DSL workout for the test, to add to the plan |
| `zones add` | add a record (lab, field test, estimate) |
| `zones accept ID` | make a record current |
| `zones push [--dry-run]` | write the current zones to Garmin |

Storage: `$PACE_HOME/zones.json`, same store rules as the diary and plan.
This section is built as coach plan 5, after plan 2.

### Post-hoc notes (plan 5, zones commands, implemented 2026-11-18)

- `zones estimate` and `zones analyze` are `"local_write": true` (the estimates log, `hr_max` and the alpha1 memory are written on every run); `--save` on either also stores a record.
  Neither makes a record current: `zones accept` does (a lab record is the one exception, on `zones add`).
- A cold `zones estimate` takes up to about 4 minutes; callers use a 10-minute timeout.
  `--max-calls` (default 40) caps every Garmin call; deferred reads make the estimate partial and the due rule skips it.
- `zones show` reads the activity list from 56 days before the last complete estimate (84 days back without one), so `due` also sees the last run before that estimate.
  An unreadable watch-zones read leaves `garmin` and `in_sync` null instead of failing the command.
- An estimate runs on a snapshot of `zones.json` and merges its log entry, `hr_max` and alpha1 memory into the file under a short lock, because the reads outlast the lock timeout; records and `current` written meanwhile are kept.
- `--save` refusals (`bad_args`) happen after the local writes (`hr_max`, alpha1 outcome, estimate log) are committed.
- `pace.fit.FitUnreadable` (an `internal` PaceError) marks the parse failure; only it marks `fit_unreadable` in `no_rr.json`.
  Any other `internal` error on a FIT download defers that read and marks nothing.
- A wrist-HR run analyzed with `zones analyze --save` is saved with confidence `low`.
- `zones push` is not part of this step (it waits for the live verification of the Garmin write).
- `zones push` (plan 5 Task 5c, after the live check of the write) writes the five floors, and max HR only when higher than the watch's (pace's is a lower bound), into every `DEFAULT` and `RUNNING` entry of the watch zones and keeps the rest as read; its body is built on a live read, so `--dry-run` makes that one read (the only dry-run that touches the network).

## Implementation notes (post-hoc)

Where the shipped plan (section 2) departs from, or pins down, the design above:

- Stored `status` also has `done`, set by hand for sessions Garmin does not record; `unconfirmed` and `rest` are derived by `plan status`, next to `done`, `modified` and `missed`.
- A push records a `pushed_as` snapshot (date and workout as pushed); `stale` is derived from it.
- `plan show` and `plan status` take `--past` and `--ahead` (days) instead of `--days`; `--from` and `--to` win when given.
- Pushing an already pushed, unchanged session is skipped unless `--force`; sessions dated before today are never pushed, re-pushed or unpushed.
- `plan show --archived` is not built: archives are plain JSON files under `$PACE_HOME/plans/`.
- E-bike activity types count as cycling.
- `plan status` matches in three steps (manual link, workout id, closest pairing) and reads RPE and feel per activity; `--no-effort` skips that.

## Left to the builder

- Flag spelling, sub-command grouping details, exact JSON field names beyond those fixed above.
- Garmin activity type key mapping to sport families.
- Garmin ids needed for unschedule and delete, verified live.
- Chart styling details within the stated constraints.
- Whether `plan add` accepts DSL inline only or also a path to a DSL file.
- Task order, with the diary first (the skill and plan status depend on it), then plan store, push, status, load, charts, skill, dashboard.
