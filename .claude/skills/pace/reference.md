# Pace reference

Command map, workout DSL and presentation tables for the pace coaching skill.
Generated from `pace schema` (version 2.0.0) when the skill was written; `pace schema <group>` is the authority for flags, fields and rules.
Keyed: `date` takes `--from/--to/--days`, `id` takes a positional id, `week` takes `--weeks`, `none` takes no range.
Writes: (G) is a Garmin write that needs the user's yes; (L) is a local write (diary, plan, zones files); see `## Writes` in SKILL.md.

## Command map

| Group | Command | Keyed | Question it answers |
|---|---|---|---|
| activity | `pace activity list` | date | Which activities did I do recently, or in this range? |
| activity | `pace activity last` | none | What was my most recent activity? |
| activity | `pace activity show` | id | How did this activity go (summary, RPE and feel when set)? |
| activity | `pace activity splits` | id | What were the laps or splits? |
| activity | `pace activity details` | id | How did HR, pace, elevation and cadence evolve through the activity? |
| activity | `pace activity weather` | id | What was the weather during it? |
| activity | `pace activity gear` | id | Which gear was linked to it? |
| activity | `pace activity effort` | id | (G) Set RPE (1-10) and/or feel on one activity. |
| activity | `pace activity export` | id | Download one activity file (FIT, GPX, ...). |
| activity | `pace activity export-range` | date | Download every activity in a range into a folder. |
| auth | `pace auth status` | none | Are Garmin tokens present? If not, the user runs `pace auth login`. |
| auth | `pace auth logout` | none | Delete the cached tokens. |
| cache | `pace cache stats` | none | How big is the local cache? |
| cache | `pace cache path` | none | Where is the cache database? |
| cache | `pace cache clear` | none | Drop every cached response. |
| chart | `pace chart` | none | Render a PNG: volume, plan, recovery, sleep, weight, intensity, or activity ID. |
| config | `pace config show` | none | What is the effective configuration? |
| config | `pace config path` | none | Where is the config file? |
| device | `pace device list` | none | Which devices are registered? |
| device | `pace device last-used` | none | Which device synced last? |
| device | `pace device settings` | id | How is this device set up? |
| device | `pace device alarms` | none | Which alarms are configured? |
| device | `pace device solar` | id+date | How much solar charging did this device get? |
| diary | `pace diary show` | none | What do I know about the athlete (profile, active injuries, due entries, recent entries)? |
| diary | `pace diary set` | none | (L) Store a profile field. |
| diary | `pace diary unset` | none | (L) Remove a profile field. |
| diary | `pace diary add` | none | (L) Record a note, plan, recommendation, feedback, constraint, injury or decision. |
| diary | `pace diary remove` | none | (L) Delete an entry. |
| diary | `pace diary set-due` | none | (L) Set or clear an entry's due date. |
| diary | `pace diary resolve` | none | (L) Mark a constraint or injury resolved, or reopen it. |
| gear | `pace gear list` | none | Which gear items exist? |
| gear | `pace gear stats` | id | How much has this gear been used? |
| gear | `pace gear activities` | id | Which activities used this gear? |
| gear | `pace gear defaults` | none | Which gear is the default per activity type? |
| gear | `pace gear link` | id | (G) Link a gear item to an activity. |
| gear | `pace gear unlink` | id | (G) Unlink a gear item from an activity. |
| goal | `pace goal list` | none | Which Garmin goals are active? |
| goal | `pace goal personal-records` | none | What are my personal records? |
| load | `pace load` | week | How much did I train per week and day (km, D+, hours), actual vs planned, with the rolling 7-day sum? |
| log | `pace log weight list` | date | When did I last weigh in, and what were the values? |
| log | `pace log weight add` | none | (G) Record a weigh-in. |
| log | `pace log weight delete` | id+date | (G) Delete a weigh-in. |
| log | `pace log bp list` | date | What were my blood pressure readings? |
| log | `pace log bp add` | none | (G) Record a blood pressure reading. |
| log | `pace log bp delete` | id+date | (G) Delete a blood pressure reading. |
| log | `pace log hydration` | none | (G) Add water intake for a day. |
| plan | `pace plan show` | none | What is the plan (meta, blocks, sessions in a range)? |
| plan | `pace plan status` | date | Planned vs done: matched sessions with actuals, zone time, RPE and feel, unplanned activities, totals. |
| plan | `pace plan new` | none | (L) Start a new plan; the old one is archived. |
| plan | `pace plan meta` | none | (L) Set the plan's name, goal or event date. |
| plan | `pace plan block add` | none | (L) Add a block (phase, start, end, focus). |
| plan | `pace plan block set` | none | (L) Change a block. |
| plan | `pace plan block remove` | none | (L) Delete a block no session uses. |
| plan | `pace plan add` | none | (L) Add sessions from a JSON file or stdin (`-`), all or nothing. |
| plan | `pace plan set` | none | (L) Change one session (date, target, workout, status, ...). |
| plan | `pace plan remove` | none | (L) Delete sessions that have no Garmin workout. |
| plan | `pace plan push` | none | (G) Upload and schedule sessions on the watch. |
| plan | `pace plan unpush` | none | (G) Delete the sessions' workouts from Garmin. |
| profile | `pace profile show` | none | Name, profile and unit system. |
| profile | `pace profile settings` | none | Profile settings, including the unit system. |
| training | `pace training status` | date | Training status, load and VO2max. |
| training | `pace training readiness` | date | Training readiness and its contributors. |
| training | `pace training endurance` | date | Endurance score. |
| training | `pace training hill-score` | date | Hill score. |
| training | `pace training fitness-age` | date | Fitness age. |
| training | `pace training race-predictions` | none | Predicted 5k, 10k, half and marathon times. |
| wellness | `pace wellness sleep-range` | date | How did I sleep over these nights (stages, resting HR)? One call per 28 days. |
| wellness | `pace wellness sleep` | date | Sleep of one night in detail (stages, overnight HR, respiration). |
| wellness | `pace wellness summary` | date | Daily steps, calories, distance, floors, resting HR. |
| wellness | `pace wellness hr` | date | Heart rate through the day. |
| wellness | `pace wellness hrv` | date | Overnight HRV (absent on the Forerunner 55). |
| wellness | `pace wellness stress` | date | Stress and body battery through the day. |
| wellness | `pace wellness body-battery` | date | Body battery through the day. |
| wellness | `pace wellness spo2` | date | Blood oxygen saturation. |
| wellness | `pace wellness hydration` | date | Water intake for the day. |
| workout | `pace workout list` | none | Which workouts are saved on Garmin? |
| workout | `pace workout show` | id | What is in this saved workout? |
| workout | `pace workout calendar` | date | What is scheduled on the Garmin calendar (did the user move a workout)? |
| workout | `pace workout push` | none | (G) Create a workout from the DSL. |
| workout | `pace workout schedule` | id+date | (G) Put a saved workout on a calendar day. |
| workout | `pace workout delete` | id | (G) Delete a saved workout. |
| zones | `pace zones show` | none | What are the current LT1 and LT2, the zones, `in_sync` with the watch, and is anything `due`? |
| zones | `pace zones estimate` | none | (L) What do my recent runs say about LT1 and LT2? `--save` stores the record. |
| zones | `pace zones analyze` | id | (L) What did this field test (drift, time_trial, ramp) or race effort (race, `--laps A-B`) show? `--save` stores the record. |
| zones | `pace zones test-workout` | none | Which workout DSL runs this field test? |
| zones | `pace zones add` | none | (L) Record a threshold record (lab values as reported). |
| zones | `pace zones accept` | none | (L) Make a record current (all thresholds, or `--only lt1` or `lt2`). |
| zones | `pace zones remove` | none | (L) Delete a record. |
| zones | `pace zones push` | none | (G) Write the current zones (five floors; max HR only when higher than the watch's) to the watch's DEFAULT and RUNNING HR zones; `--dry-run` reads the watch zones live and shows `before`, `after` and the body. |

## Workout DSL

`pace workout push -` and the `workout` field of a plan session take this JSON (full grammar in `pace schema`, key `workout_dsl`):

```json
{"name": "Tempo 3x10", "sport": "running", "steps": [
  {"type": "warmup", "duration": "15m"},
  {"repeat": 3, "steps": [
    {"type": "interval", "duration": "10m", "target": {"pace": "4:15-4:25"}},
    {"type": "recovery", "duration": "3m"}]},
  {"type": "cooldown", "distance": "1km"}]}
```

Step types: warmup, cooldown, interval, recovery, rest.
Each step ends on exactly one of `duration` (45s, 10m, 1h30m), `distance` (400m, 5.5km) or `open: true`.
Targets (optional, one of): `pace` (min:sec per km, single or range), `hr` (`z1`..`z5` or a bpm range such as `140-150`), `power`, `cadence`.
Use bpm ranges instead of `z1`..`z5` when `pace zones show` reports `in_sync` not true and both LT1 and LT2 have a current record.
Only running with pace and HR-zone targets is live-verified.

## Presentation tables

Fixed column orders; convert units; `-` for a missing value.

Activities (RPE and Feel from `pace plan status`, else `-`):

| Date | Type | Name | Dist (km) | Time (h:mm) | Pace (min/km) or Speed (km/h) | Avg HR | RPE | Feel |
|---|---|---|---|---|---|---|---|---|

Plan status:

| Date | Session | Status | Planned | Actual | Zones (s in Z1-5) | RPE | Feel |
|---|---|---|---|---|---|---|---|

Sleep (from `pace wellness sleep-range`):

| Night | Total (h:mm) | Deep | Light | REM | Awake | Resting HR |
|---|---|---|---|---|---|---|

Weekly load (from `pace load`):

| Week | Km | D+ (m) | Hours | Projected km | Rolling 7d km |
|---|---|---|---|---|---|

Zones (from `pace zones show`):

| Zone | HR | Pace (min/km) |
|---|---|---|

Workout detail (from `pace activity details`):

| Time | HR | Pace (min/km) | Dist (km) | Elev (m) | Cadence |
|---|---|---|---|---|---|
