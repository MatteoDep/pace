# Gear and Effort Writes: Design

Extends `docs/superpowers/specs/2026-09-04-llm-cli-design.md` section 7 (Mutations).
Status: implemented and live-verified, 2026-11-06 (both fallbacks below were not needed).

## Why

The user forgot to set the right shoes on many past activities and finds the Garmin app clunky for fixing them one at a time.
They want `pace` to gain the writes needed for this, and then have the LLM (Claude, driving `pace`) walk through activities with them and fix each one on their say-so.
Selection is one by one: the LLM shows an activity with its current gear and effort, the user says what it should be, the LLM writes it.
The same commands later serve the coach (logging how a session felt).

## Goals

1. Change which gear is linked to a single activity (link and unlink).
2. Set perceived exertion (RPE) and feel on a single activity.
3. Read back what is needed to review activities one by one: an activity's gear, RPE and feel, and which activities a gear item is on.

## Non-goals

- Bulk rules ("all runs since X get shoe Y").
  The LLM loops over single-activity commands instead.
- Creating gear (`gear add`): garminconnect 0.2.38 has no call for it, and the user chose to leave it out until it does.
  New gear is added in the Garmin app.
- Editing or retiring an existing gear item, deleting gear, setting gear defaults per activity type.
- Any other activity edit (name, type, notes, metrics) or activity delete.
- A UI beyond the CLI.

## Anti-goals

- A write that touches more than the user approved, e.g. a gear change that silently also unlinks a different gear item, or an effort update that resets other activity fields.
- Writes the user cannot preview: every new command must support `--dry-run` with the exact payload.

## Constraints

Everything in the existing section 7 safety contract applies unchanged: one JSON envelope on stdout, `--dry-run` everywhere with the exact Garmin payload, writes never read the cache, success invalidates affected cache keys, no `--yes` flag (confirmation is the caller's job), all writes go through `pace/writes.py` `run_write`.

The spec line "Permanently excluded: activity delete or edit, gear assignment, ..." is amended to: activity delete, and activity edits other than gear links and RPE/feel, stay excluded; manual activity creation and file upload stay excluded.
The "deletes only for resources this CLI creates" rule is unchanged; unlinking gear from an activity is not a delete of either resource.

## Garmin facts (verified read-only on 2026-11-06)

- `get_activity(id)` returns `summaryDTO.directWorkoutRpe` and `summaryDTO.directWorkoutFeel`.
  Observed on one of the user's runs: RPE 40, feel 75.
  Garmin's app shows RPE as 1-10, stored x10 (10..100); feel has five levels stored as 0, 25, 50, 75, 100 (very weak, weak, normal, strong, very strong).
  The x10 mapping and the feel level order are from memory of the Garmin app and must be confirmed live (set a value in the app or via the command, read it back).
- `get_activity_gear(id)` returns a list of gear items (`uuid`, `customMakeModel`, `gearTypeName`, `gearStatusName`, `dateBegin`, `maximumMeters`, ...).
- The user's gear are all `gearMakeName: "Other"`, `gearModelName: "Unknown Shoes"`, with the real name in `customMakeModel`.
- garminconnect 0.2.38 wraps: `add_gear_to_activity(gearUUID, activity_id)` (PUT `/gear-service/gear/link/{uuid}/activity/{id}`), `remove_gear_from_activity` (PUT `.../unlink/...`), `get_activity_gear`, `get_gear_activities(uuid, limit)`.
- Not wrapped: creating gear (out of scope, see Non-goals), and setting RPE/feel.
  RPE/feel stays in scope because it goes through the library's generic `connectapi(path, method="PUT", json=...)`, the same route `workout schedule`/`delete` already use (user's call).
  The library's `set_activity_name` / `set_activity_type` do a partial PUT to `/activity-service/activity/{id}` with only `activityId` plus the changed DTO; the expected RPE/feel call is the same shape with `{"activityId": id, "summaryDTO": {"directWorkoutRpe": .., "directWorkoutFeel": ..}}`.

## Commands

Names are the design; flag spelling details are the builder's.

| command | Garmin call | notes |
|---|---|---|
| `gear link ACTIVITY_ID GEAR_UUID` | `add_gear_to_activity` | adds one link; does not remove other gear on the activity |
| `gear unlink ACTIVITY_ID GEAR_UUID` | `remove_gear_from_activity` | |
| `activity effort ACTIVITY_ID [--rpe 1-10] [--feel very-weak\|weak\|normal\|strong\|very-strong]` | partial PUT to `/activity-service/activity/{id}` | at least one of the two flags; only the given field(s) are sent |
| `activity gear ACTIVITY_ID` (read) | `get_activity_gear` | curated fields: uuid, customMakeModel, gearTypeName, gearStatusName |
| `gear activities GEAR_UUID [--limit]` (read) | `get_gear_activities` | activity list fields |

`activity show` gains two derived top-level keys, `rpe` and `feel`, added to the payload before shaping (so `--fields rpe,feel` selects them), reported on the 1-10 scale and as the feel label, so the user sees the same values the Garmin app shows.
The stored `summaryDTO.directWorkoutRpe`/`directWorkoutFeel` keys stay as they are; `--raw` returns the untouched payload without the derived keys.
A missing value leaves the key out (shaping prunes nulls, and absent means unset across the CLI); an RPE that is not a multiple of 10 gives the stored value / 10 (e.g. `4.5`); a feel outside the five stored values leaves `feel` out (the raw key still shows it).
`activity list`/`last` are unchanged: Garmin's list rows do not carry RPE, feel or gear (checked 2026-11-06), and adding them would cost one call per row.
(Builder-check call, made by Claude.)

"Change shoes on activity X" is two writes the caller composes; the order (link new first, or unlink old first) is fixed after the live check of whether Garmin keeps both.
A combined swap command was considered and dropped: two primitives are enough for one-by-one review, and each step is previewable.

### Result envelope

The section 7 `action` enum grows: `create | delete | update | link | unlink`; `resource` grows `activity_gear | activity_effort`.
`gear link` -> `link`/`activity_gear`; `gear unlink` -> `unlink`/`activity_gear`; `activity effort` -> `update`/`activity_effort`.
`echo` carries the parsed input on the user's scale (RPE 1-10, feel label), not the stored value.

### Validation

- `--rpe` integer 1..10; `--feel` one of the five labels; neither given -> `bad_args`.
- `gear link`/`unlink` do not pre-check that the uuid or activity exists; Garmin's error maps through the usual error codes.

### Cache invalidation

- `gear link`/`unlink`: `get_activity_gear:<activity_id>`, and the whole `get_gear_activities:` and `get_gear_stats:` prefixes (reads key the uuid as typed; a replacement would touch another shoe).
- `activity effort`: `get_activity:<activity_id>`.

## Live verification (human-gated)

As in the mutations plan, live writes against the user's account happen only after the user's explicit go-ahead, on an activity the user picks.
Check: set RPE/feel and read back via `activity show` and the Garmin app; unlink and relink a shoe on one activity.

### Behaviours to settle live, with the fallback decided now (Claude's calls)

- **Partial effort PUT.**
  The `set_activity_name` precedent replaces top-level DTOs; a partial `summaryDTO` might null the keys it does not send.
  Live check: set only `--rpe`, read back, confirm feel and other `summaryDTO` metrics are intact.
  If Garmin wipes them, the command becomes read-modify-write: the real run reads `get_activity` live (never the cache) and sends both current RPE and feel with the new one.
  `--dry-run` then also does that live read (a read, never a write), and its payload is the exact one that would be sent.
  This is a stated exception to "dry-run makes no network call", recorded in CONTRACT.md.
  Never send the whole `summaryDTO` back.
- **Link replacing existing gear.**
  Live check: link a second shoe to an activity that already has one, read back `activity gear`.
  If both stay, `gear link` is as designed.
  If Garmin swaps the old one out, `gear link` documents it, the real run reads `activity gear` live first and reports any removed uuids in `echo.replaced`, and also invalidates those uuids' `get_gear_activities`/`get_gear_stats` keys; `--dry-run` stays offline and notes in its output that Garmin may replace same-type gear.

## Docs and tests

`docs/CONTRACT.md` writes section, `README.md`, `pace schema` entries, spec section 7 amendment.
Tests follow the existing write tests: dry-run payloads, validation errors, invalidation keys, result envelope.

## Left to the builder

- Where the hand-written gear write commands live alongside the generated `gear` group (`pace/commands/gear.py` is built from `pace.spec.REGISTRY`).
- Exact cache key strings, matching how `cached_call` builds them.
