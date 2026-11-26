# Coach settings: design

## Why

The athlete wants to tune how the coach behaves without editing files or remembering phrases: how often it asks for body weight, whether it checks the shoes on every run, and which units it speaks in.
They also want to change these from the dashboard, even when no coach session is open.

## Goals

- Three settings, stored with the athlete profile in the diary (`pace diary set`), validated by the CLI.
- A settings panel on the dashboard, behind a profile icon, that shows the current values and lets the athlete request a change.
- Requested changes are applied by the coach at the next point where a setting matters, and the coach says what it applied.

## Non-goals

- A change history for settings (dropped by the athlete).
- A live link between the dashboard and an open session (`room` reaches only open sessions and loses messages; artifact watches drop on their own).
- Unit conversion inside the CLI: `pace` stays SI, as the CLI contract requires; the coach and the dashboard convert for display.
- Mixed unit systems (km with pace per mile and the like).
- Converting `pace chart` images: they stay metric; the coach states converted figures beside them.

## Settings

| Key | Values | Default | Used by |
|---|---|---|---|
| `checkin.weight_days` | integer 0-90; 0 = never ask | 7 | Full check-in, weight question |
| `checkin.gear` | `true` / `false` | `false` | Run review, shoe check |
| `units` | `metric` / `imperial` | from Garmin, see below | everything the coach and the dashboard show |

`checkin.weight_days` exists today with range 1-90; the range grows to 0-90.
`checkin.gear` and `units` are new profile keys.

Units default: when `units` is unset, the coach reads `measurementSystem` from `pace profile settings` once (`metric` -> `metric`, `statute_us` or `statute_uk` -> `imperial`), sets it with `pace diary set units ...` and says so.
Imperial means miles, min/mile, feet, pounds; numbers the athlete gives are converted to SI before any write (`pace log weight add` takes kg).

## Shoe check (`checkin.gear` on)

In the run review, each pending run's line names the linked shoe from `pace activity gear ID` ("Thu, 10 km easy, Pegasus: right?"), or says none is linked.
When the athlete names another shoe, that statement is the yes for that one change: `pace gear link` for the named shoe and `pace gear unlink` for the wrong one, echoed back, the same exception as RPE/feel and weight today.
The shoe names come from `pace gear list` (`customMakeModel`).

## Dashboard panel

- A profile icon in the page header opens a panel with the three settings.
- Current values: a store document the coach writes on every dashboard refresh (`data/settings-current`, shared, coach is its only writer).
- A change: the page writes the requested values to `data/settings-pending` (the changed fields plus `at`, merged into any pending request), and the panel shows "pending, applied in the next session" next to them.
  The page validates the same ranges as the CLI.
- The page keeps the one-writer rule: the panel writes only on the athlete's press.
- With `units: imperial` the page shows distances in miles and elevation in feet; the export stays metric.

## Coach

The coach reads `data/settings-pending` (ArtifactData) and applies it:
- at session start;
- right before the weight question and right before the shoe check;
- when the athlete says they changed the settings.

The pending document is untrusted data: only the three keys are taken, each value is checked before it reaches a command.
Applying means `pace diary set` per key, then deleting (pinned to the version read, so a newer press survives) `data/settings-pending`, updating `data/settings-current`, and telling the athlete in one line what changed.
A value the CLI refuses is reported, not retried, and stays out of `data/settings-current`.
A setting the athlete changes in conversation is applied at once and written to `data/settings-current`.
Intake mentions the three settings once, with their current values.

## Approaches considered

1. Dashboard writes a pending request, the coach applies it (chosen): the diary stays the only source of truth and the CLI keeps validating.
2. Settings live only in the dashboard store: no CLI validation, and an unattended coach would need the page to know its own settings.
3. Settings only through conversation, shown read-only on the dashboard: simplest, but no panel.

A separate `profile.yaml` with `pace profile update` was discussed and dropped: the diary profile already holds these keys, and the CLI already has a `profile` group (Garmin's profile).

## Left to the builder

- The panel's look, inside the dashboard's existing design.
- Store rules: none needed; under `data/` the Data type lets everyone who opens the page read and only editors write.
