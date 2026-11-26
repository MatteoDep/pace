# Dashboard

The training load dashboard is one claude.ai artifact made from the Data type; its URL is the profile's `dashboard.url` (`pace diary show`).
Its page reads one attached bundle, produced by `pace export` from the recipe `$PACE_HOME/dashboard/recipe.json` (or the shipped default): one section per dataset, at `answer.datasets.<id>`.
Each dataset of the artifact (`datasets/<id>`) points at the same uploaded bundle and reads its own section with a loader declared in the page script: `dash.loader("<id>", {description, fn: ({answer}) => ...})`.
A section loader is `({answer}) => answer.datasets.<id>.rows`; throw an `Error` when the section is missing, which fails that source only.
A loader may use nothing from outside itself, since readers see its code.
Dataset `load` keeps its calcs `weeks`, `days`, `thisweek` and `nextsession` and the page filters `metric` (`km`, `elev_m`, `hours`) and `sport` (`running`, `all`).
`PACE_HOME` is often unset: the bundle is `${PACE_HOME:-$HOME/.pace}/dashboard/bundle.json`.

## Create

Only when the user asks, never in an unattended run.
Run `pace export` (default recipe); when the athlete's page needs a custom recipe, run `pace export --init` first, edit the file, and check it with `pace export --check`.
Then follow the Data type's instructions: create the artifact from its `type_url` with title "Training Load", upload the bundle as an asset, and write in one batch `dash/meta`, `dash/params` (`{"metric": "km", "sport": "running"}`), one `datasets/<id>` per section the page uses (each a `file` source with the same `/_blob/<id>` url, `name`, `format: "json"`, and `updated: {at, by: "pace coach"}`), and `files/index.html` with a `dash.loader` for each dataset.
The page source used for the current dashboard is in the artifact itself (`files/index.html`); read it there rather than rewriting it from scratch.
Then `pace diary set dashboard.url <url>`.
Tell the user the attached file is readable by anyone who can open the link.

## Refresh

When: at the end of a full check-in, and once after a batch of plan edits (not after each edit).
Steps, exactly:
1. `pace export`.
   Its summary lists per dataset `stale`, `reason` and `error` (`code`, `message`).
   If it fails with `auth_required` (exit 3, no bundle written): stop, no refresh this time, and tell the athlete to log in again with `pace auth login` (you never run it).
2. Upload the bundle (`path` of the summary) to the artifact as an asset (`asset: true`); note the new `/_blob/<id>`.
3. Read each `datasets/<id>` that points at the previous bundle, for its `version`; one ArtifactData `batch` of `update`s, each pinned to its dataset's version: `{"source": {"url": "<new>"}, "updated": {"at": "<bundle Generated time>", "by": "pace coach"}}`.
4. Keep the asset you just replaced, since a page open during the refresh may still fetch it; delete the older bundles (Artifact `list` with `scope: "assets"`, then delete each `application/json` asset that is neither the new one nor the replaced one; never touch assets of other types).
5. ArtifactData `list` on collection `data/users/me`, once: a `check` with `failed` means the page broke; fix it now. No `check` means nobody has the page open or it passed; move on, never loop.
6. Write the current settings (Settings below).

A dataset with `stale: true`: tell the athlete once, with its `error` message; its section still holds the previous rows.
`reason: "deferred"` means the call budget ran out; the next refresh continues from the cache, so say nothing more.

## Settings

The profile panel on the page shows the coach settings and lets the user request changes.
Two store documents under `data/`, where everyone who can open the page reads and only editors write:
- `data/settings-current`: `{"weight_days": int, "gear": bool, "units": "metric"|"imperial", "at": "<ISO time>"}`, defaults filled in for unset keys.
  Only the coach writes it, with ArtifactData `set`: at the end of every Refresh, after applying pending settings, and after a change made in conversation.
- `data/settings-pending`: the requested fields (any of the three) plus `at`.
  Only the page writes it, merging a new request into the existing document; the coach reads it with ArtifactData `get`, applies it (SKILL.md, Settings), then `delete`s it pinned to the version it read.
The page reads both through the datasets `settings_current` and `settings_pending` (`kind: "store"`).

## Add a view

On the user's request only, never unprompted:
1. `pace export --init` if `$PACE_HOME/dashboard/recipe.json` does not exist yet, then add a recipe line for the new data (CONTRACT.md, Export, Recipe).
2. `pace export --check`, then `pace export`; read the new section's `stale` and row count in the summary.
3. Add the dataset `datasets/<id>` (file source on the current bundle, loader `({answer}) => answer.datasets.<id>.rows`) and the page section: read `files/index.html` again first (they may have edited words with the pencil), keep the `<meta name="dash-one-writer">` rule, write it back pinned to its version.
4. Read `check` once, and record a diary `note` with what changed and why.

Deep dives (one run, one block, a comparison) are separate plain artifacts or `pace chart` PNGs, never extra sections on this page.
A change to an existing section (words, layout, chart) follows steps 3 and 4 only.

## Unattended runs

The unattended session (project 2) refreshes only; it never creates or redesigns the dashboard.
It needs permission for Bash `pace export`, the Artifact asset upload and delete, and ArtifactData reads, batched dataset updates, sets and deletes on this artifact (the dataset documents and the settings documents).
