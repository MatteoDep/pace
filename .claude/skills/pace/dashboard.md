# Dashboard

The training load dashboard is one claude.ai artifact made from the Data type; its URL is the profile's `dashboard.url` (`pace diary show`).
Its page reads one attached file, `load.json`, produced by `pace load --export`: dataset `load` (`datasets/load` in the artifact's store), with calcs `weeks`, `days`, `thisweek` and `nextsession`, and the page filters `metric` (`km`, `elev_m`, `hours`) and `sport` (`running`, `all`).

## Create

Only when the user asks, never in an unattended run.
Run `pace load --export "${PACE_HOME:-$HOME/.pace}/dashboard/load.json"` (`PACE_HOME` is often unset), then follow the Data type's instructions: create the artifact from its `type_url` with title "Training Load", upload the file as an asset, and write `dash/meta`, `dash/params` (`{"metric": "km", "sport": "running"}`), `datasets/load` (a `file` source with `updated: {at, by: "pace coach"}`) and `files/index.html` in one batch.
The page source used for the current dashboard is in the artifact itself (`files/index.html`); read it there rather than rewriting it from scratch.
Then `pace diary set dashboard.url <url>`.
Tell the user the attached file is readable by anyone who can open the link.

## Refresh

When: at the end of a full check-in, and once after a batch of plan edits (not after each edit).
Steps, exactly:
1. `pace load --export "${PACE_HOME:-$HOME/.pace}/dashboard/load.json"`.
2. Upload it to the artifact as an asset (`asset: true`); note the new `/_blob/<id>`.
3. Read `datasets/load` for its `version` and its current `source.url`; one `update` pinned to that version: `{"source": {"url": "<new>"}, "updated": {"at": "<export Generated time>", "by": "pace coach"}}`.
4. Keep the asset you just replaced, since a page open during the refresh may still fetch it; delete every older one (Artifact `list` with `scope: "assets"`, then delete each asset that is neither the new one nor the replaced one).
5. ArtifactData `list` on collection `data/users/me`, once: a `check` with `failed` means the page broke; fix it now. No `check` means nobody has the page open or it passed; move on, never loop.
6. Write the current settings (Settings below).

## Settings

The profile panel on the page shows the coach settings and lets the user request changes.
Two store documents under `data/`, where everyone who can open the page reads and only editors write:
- `data/settings-current`: `{"weight_days": int, "gear": bool, "units": "metric"|"imperial", "at": "<ISO time>"}`, defaults filled in for unset keys.
  Only the coach writes it, with ArtifactData `set`: at the end of every Refresh, after applying pending settings, and after a change made in conversation.
- `data/settings-pending`: the requested fields (any of the three) plus `at`.
  Only the page writes it, merging a new request into the existing document; the coach reads it with ArtifactData `get`, applies it (SKILL.md, Settings), then `delete`s it pinned to the version it read.
The page reads both through the datasets `settings_current` and `settings_pending` (`kind: "store"`).

## Change a view

On the user's request only: read `files/index.html` again first (they may have edited words with the pencil), change what was asked, keep the `<meta name="dash-one-writer">` rule, write it back pinned to its version, read `check` once, and record a diary `note` with what changed and why.
Deep dives (one run, one block, a comparison) are separate plain artifacts or `pace chart` PNGs, never extra sections bolted on here.

## Unattended runs

The unattended session (project 2) refreshes only; it never creates or redesigns the dashboard.
It needs permission for the Bash `pace` commands, the Artifact asset upload and delete, and ArtifactData reads, updates, sets and deletes on this artifact (the settings documents).
