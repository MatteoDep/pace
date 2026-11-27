# Dashboard

The training load dashboard is one claude.ai artifact made from the Data type; its URL is the profile's `dashboard.url` (`pace diary show`).
Its page reads one attached bundle, produced by `pace export` from the recipe `$PACE_HOME/dashboard/recipe.json` (or the shipped default): one section per dataset, at `answer.datasets["<id>"]`.
Each dataset of the artifact (`datasets/<id>`) points at the same uploaded bundle and reads its own section with a loader declared in the page script: `dash.loader("<id>", {description, fn: ({answer}) => ...})`.
A section loader is `({answer}) => answer.datasets["<id>"].rows` (brackets, since an id may contain `-`); throw an `Error` when the section is missing, which fails that source only.
A loader may use nothing from outside itself, since readers see its code.
Dataset `load` keeps its calcs `weeks`, `days`, `thisweek` and `nextsession` and the page filters `metric` (`km`, `elev_m`, `hours`) and `sport` (`running`, `all`).
`PACE_HOME` is often unset: the bundle is `${PACE_HOME:-$HOME/.pace}/dashboard/bundle.json`.

## Create

Only when the user asks, never in an unattended run.
Run `pace export` (default recipe) with a 10-minute timeout (Refresh, step 1); when the athlete's page needs a custom recipe, run `pace export --init` first, edit the file, and check it with `pace export --check`.
Then follow the Data type's instructions: create the artifact from its `type_url` with title "Training Load", upload the bundle as an asset, and write in one batch `dash/meta`, `dash/params` (`{"metric": "km", "sport": "running"}`), one `datasets/<id>` per section the page uses (each a `file` source with the same `/_blob/<id>` url, `name`, `format: "json"`, and `updated: {at, by: "pace coach"}` with `at` as in Refresh step 3), and `files/index.html` with a `dash.loader` for each dataset.
The page source used for the current dashboard is in the artifact itself (`files/index.html`); read it there rather than rewriting it from scratch.
Then `pace diary set dashboard.url <url>`.
Tell the user the attached file is readable by anyone who can open the link.

## Refresh

When: at the end of a full check-in, and once after a batch of plan edits (not after each edit).
Steps, exactly:
1. `pace export`, with a 10-minute timeout: its time budget is 8 minutes and a cold run took over 4, so the default 2-minute tool timeout can kill it before it writes the bundle.
   Its summary lists per dataset `stale`, `reason` and `error` (`code`, `message`).
   If it fails with `auth_required` (exit 3, no bundle written): stop, no refresh this time, and tell the athlete to log in again with `pace auth login` (you never run it).
2. Upload the bundle (`path` of the summary) to the artifact as an asset (`asset: true`); note the new `/_blob/<id>`.
3. ArtifactData `list` the `datasets` collection.
   The previous bundle's `/_blob/<id>` is the `source.url` that the file datasets share in this listing, read before any update; matching on it skips the `settings_*` store datasets.
   For each dataset with that `source.url`, one `update` in a single `batch`, pinned to that dataset's `version`: `{"source": {"url": "<new>"}, "updated": {"at": "<at>", "by": "pace coach"}}`.
   `<at>` is the value of the `meta` entry labelled `Generated` in the bundle file (the `path` in the summary); for a dataset whose section in that file is stale, it is the section's `meta.generated` instead, so old rows never show as fresh.
4. Keep the asset you just replaced, since a page open during the refresh may still fetch it; delete the older bundles (Artifact `list` with `scope: "assets"`, then delete each `application/json` asset that is neither the new one nor the replaced one; never touch assets of other types).
5. ArtifactData `list` on collection `data/users/me`, once: a `check` with `failed` means the page broke; fix it now. No `check` means nobody has the page open or it passed; move on, never loop.
6. Write the current settings (Settings below).

A dataset with `stale: true` and `reason: "deferred"` is not reported: the call budget ran out and the next refresh continues from the cache.
Any other stale dataset (`reason: "error"`): tell the athlete once, with its `error` message; its section still holds the previous rows.
A dataset whose `errors` has an item with a code other than `deferred`, or whose `day_errors` is above 0, is mentioned once the same way: those parents or days kept their previous rows or have none.
`day_errors` carries no codes and also counts days the budget deferred, so leave it out when anything in the run was deferred.

## Settings

The profile panel on the page shows the coach settings and lets the user request changes.
Two store documents under `data/`, where everyone who can open the page reads and only editors write:
- `data/settings-current`: `{"weight_days": int, "gear": bool, "units": "metric"|"imperial", "at": "<ISO time>"}`, defaults filled in for unset keys.
  Only the coach writes it, with ArtifactData `set`: at the end of every Refresh, after applying pending settings, and after a change made in conversation.
- `data/settings-pending`: the requested fields (any of the three) plus `at`.
  Only the page writes it, merging a new request into the existing document; the coach reads it with ArtifactData `get`, applies it (SKILL.md, Settings), then `delete`s it pinned to the version it read.
The page reads both through the datasets `settings_current` and `settings_pending` (`kind: "store"`).

## Requests

The page's quick actions (a shoe picker and RPE and feel controls on an activity, a weigh-in form) never write to Garmin themselves.
Each press adds one document, with a fresh id from `crypto.randomUUID()`, to the store collection `data/requests/pending`, which everyone who can open the page reads and only editors write.
A document is `{"op", "params", "at"}`, with `at` the press time from `new Date().toISOString()` and numbers in SI (the page converts pounds to kg):
- `shoe.set`: `{"activity_id", "gear_uuid"}`, offered only for the active shoes of the bundle's `shoes` section.
- `effort.set`: `{"activity_id", "rpe", "feel"}` with either or both; RPE an integer 1-10, feel one of `very-weak`, `weak`, `normal`, `strong`, `very-strong`.
- `weight.add`: `{"kg", "date"}`, the date `YYYY-MM-DD`, today or up to a year back.
Before the press the page shows exactly what will be written; after it, the request shows as pending ("applied at the next session") until its document is gone.
`pace schema requests` has the exact rules.

Apply them at session start (SKILL.md) and in unattended runs, when `dashboard.url` is set:
1. ArtifactData `list` the collection `data/requests/pending`; when it is empty, stop.
2. Write the documents to a scratch file as a JSON list of `{"id", "version", "at", "op", "params"}` (id and version from the listing, the other fields copied unchanged from the document), and run `pace requests apply FILE` with a 5-minute timeout.
3. In one `batch`, delete each document whose result is `applied` or `refused`, pinned to the `version` you read; keep the `failed` ones for the next run.
4. Tell the athlete in one line what was applied, and each refused request with its reason, once.
5. When anything was applied, refresh the dashboard (Refresh) so the page shows it, unless a full check-in in this session refreshes it at its end.

The press is the athlete's yes for these writes, so never ask again; this holds because the dashboard is private and only its editors can write to the collection.
If it is ever shared with edit rights, stop applying requests and tell the athlete.
`auth_required` stops the run like any call (SKILL.md, Session start); the next run reports the requests already applied as `applied` ("already applied"), and they are deleted then.
`internal` with "busy" in the message means another `pace requests apply` is running: leave the requests for the next session.
Any other error of the command itself (`bad_args`, `internal`): tell the athlete once, with its message, and leave the documents.
A result with no `id` comes from a document without a valid id: tell the athlete once and leave the document.

## Add a view

On the user's request only, never unprompted:
1. `pace export --init` if `$PACE_HOME/dashboard/recipe.json` does not exist yet, then add a recipe line for the new data (CONTRACT.md, Export, Recipe).
2. `pace export --check`, then `pace export` with a 10-minute timeout; read the new section's `stale` and row count in the summary.
3. Run Refresh steps 2 to 4 so the new bundle is uploaded and the existing datasets point at it.
4. Add the dataset `datasets/<id>` (file source on the new `/_blob/<id>`, loader `({answer}) => answer.datasets["<id>"].rows`) and the page section as in Change a view.

Deep dives (one run, one block, a comparison) are separate plain artifacts or `pace chart` PNGs, never extra sections on this page.

## Change a view

On the user's request only.
Read `files/index.html` again first (they may have edited words with the pencil), change only what was asked, keep the `<meta name="dash-one-writer">` rule, and write it back pinned to its version.
Read `check` once, and record a diary `note` with what changed and why.

## Unattended runs

The unattended session (project 2) refreshes and applies requests only; it never creates or redesigns the dashboard.
It needs permission for Bash `pace export` and `pace requests apply`, the Artifact asset upload and delete, and ArtifactData reads, batched dataset updates, sets and deletes on this artifact (the dataset documents, the settings documents and the request documents).
