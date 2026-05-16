# Pace CLI -- Proposal & Implementation Plan

## Context

The current `pace` project is a sketch: `main.py` calls a handful of Garmin endpoints and discards their output, `demo.py` is a 4k-line interactive reference exercising 100+ API methods, and the `pace` entry script is a stub.
The goal is a polished, documented, scriptable CLI that lets the user navigate the full Garmin Connect surface area (66+ read endpoints across activities, wellness, training, devices, gear, goals, profile) with nicely rendered output.

The CLI must be:

- Read-only (no mutations).
- Subcommand-driven, scriptable, pipe-friendly.
- Multi-format: Rich tables (default), JSON, CSV, Markdown.
- Async-native with progress feedback for slow multi-endpoint operations.
- Backed by a local SQLite cache to respect Garmin's rate limits and enable offline browsing.

## Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| CLI parsing | `typer` | Type-hint driven, auto-help, integrates with Rich. |
| Rendering | `rich` | Tables, panels, Live progress, markdown. |
| Async tracking | `aionode` (0.3.0) | DAG tracking, `TaskInfo.status`/`duration()` for progress UI. |
| Data models | `pydantic` v2 (already installed) | Typed wrappers around raw Garmin dicts; one model per command output. |
| Cache | stdlib `sqlite3` | Per-endpoint table, TTL column. No ORM. |
| HTTP | `garminconnect` (sync) wrapped via `aionode.make_async` | Keep existing auth/MFA flow. |
| Config | env vars + `~/.garminconnect` token file (unchanged) | Plus optional `~/.pace/config.toml` for prefs (default format, cache TTL). |
| Man pages | `click-man` over Typer's underlying Click app | Generated at build time. |
| Linting/types | `ruff` + `ty` (unchanged) | |

Python `>=3.14` (already pinned).

## Repository Layout

```
pace/
  __init__.py
  cli.py                  # Typer app root; wires subcommand groups
  config.py               # Settings (env + TOML), paths (~/.pace/)
  auth.py                 # init_api(), MFA, token store (move from garmin_api.py)
  ratelimit.py            # Async token bucket + sliding window
  api.py                  # Async wrappers around Garmin methods; uses aionode.node
  cache.py                # SQLite read-through cache with TTL
  models/                 # Pydantic models per domain (activity, wellness, ...)
  render/                 # Output formatters
    __init__.py           # Formatter registry, format dispatcher
    table.py              # Rich tables/panels
    json_.py              # JSON serializer
    csv_.py               # CSV writer
    md.py                 # Markdown writer
  progress.py             # Rich Live + walk_dag() -> progress bars
  commands/               # One module per domain group
    activity.py
    wellness.py
    training.py
    device.py
    gear.py
    goal.py
    profile.py
    today.py              # Cross-domain daily dashboard
  __main__.py             # `python -m pace`
pyproject.toml             # Add deps, define [project.scripts] pace = "pace.cli:app"
docs/
  man/                    # Generated man pages
README.md                 # Quickstart + cheat sheet
```

Delete the current top-level `pace` shell stub; replace with the entry point declared in `pyproject.toml` (`pace = "pace.cli:app"`).

## Architecture

### Async + rate limiting

- Every Garmin call is wrapped by `aionode.node(make_async(api.method))`.
- A module-level `asyncio.Semaphore(N)` (default `N=3`) caps in-flight requests.
- `ratelimit.py` implements an async token bucket behind an `asyncio.Lock`:
  - Caps: 2.0 s min interval, 10/min, 100/hr, 500/day (same as `garmin_api.py:24-31`).
  - `await acquire()` blocks via `asyncio.sleep` until a token is free.
- The existing sync `_rate_limit()` (`garmin_api.py:37-74`) is the reference algorithm -- port the sliding window logic, replace `time.sleep` with `await asyncio.sleep`.
- The `safe_api_call` 3-tuple pattern (`garmin_api.py:81-125`) is preserved as an async decorator `@safe_call` that returns `(ok, data, err)` -- feeds directly into renderers.

### Progress UI

- `progress.py` runs a `rich.live.Live` loop that polls `aionode.walk_dag()` every 100 ms.
- For each `TaskInfo` it renders a row with name, status icon (WAITING/RUNNING/DONE/FAILED), and `info.duration()`.
- For aggregate commands (e.g. `pace today`), the DAG visualises which endpoints are fetching in parallel.

### Cache

- `cache.py` exposes `async get_or_fetch(key, fetch_coro, ttl)`.
- Schema: single table `cache(key TEXT PRIMARY KEY, value BLOB, fetched_at INTEGER, ttl INTEGER)`; `value` is JSON-serialized response.
- Keys: `f"{endpoint}:{args_hash}"`.
- Default TTLs per endpoint family (today's data: 5 min; historical: 24 h; static like devices/profile: 7 d).
- `--no-cache` flag and `pace cache clear|stats` subcommand.

### Rendering

- `render/__init__.py` defines `Formatter` protocol (`render(model, stream) -> None`) and a registry keyed by format name.
- Commands return a Pydantic model + a `--format` flag picks the formatter.
Default = `table` when stdout is a TTY, else `json`.
- One model per command output, so each formatter has a stable schema to work against.

### Commands (read-only, grouped by domain)

| Group | Sample subcommands |
|---|---|
| `activity` | `list`, `show <id>`, `splits <id>`, `weather <id>`, `last` |
| `wellness` | `today`, `sleep [date]`, `hr [date]`, `stress [date]`, `body-battery [date]`, `hrv [date]`, `hydration [date]`, `spo2 [date]` |
| `training` | `status`, `readiness`, `race-predictions`, `endurance`, `fitness-age`, `hill-score` |
| `device` | `list`, `last-used`, `settings <id>`, `alarms`, `solar <id>` |
| `gear` | `list`, `stats <uuid>`, `defaults` |
| `goal` | `list`, `personal-records` |
| `profile` | `show`, `settings`, `unit-system` |
| (top-level) | `today` (cross-domain dashboard), `cache`, `auth` |

Each subcommand accepts: `--format {table,json,csv,md}`, `--no-cache`, `--date YYYY-MM-DD`, `--from/--to` for ranges.

## Stages

### Stage 1 -- Skeleton (auth, cache, one command)

Goal: `pace profile show` works end-to-end with table + JSON output.

- Add deps: `typer`, `rich`, `aionode`, `click-man` (dev).
- Create package layout above.
- Port `init_api`/`safe_api_call`/rate limiter from `garmin_api.py` into `auth.py` + `ratelimit.py` (async port).
- Build `cache.py` (SQLite, TTL).
- Build `render/table.py` + `render/json_.py` + format dispatcher.
- One command: `profile show`.
- `pace auth login` / `auth status` / `auth logout` (wraps existing token flow).
- `pyproject.toml` entry point.
Replace top-level shell stub.

Deliverable: installable CLI with one command, auth flow, cache, two formats.

### Stage 2 -- Core read commands

Goal: full domain coverage of the most-used endpoints.

- Implement all `activity`, `wellness`, `training`, `device`, `gear`, `goal`, `profile` subcommands listed above.
- Pydantic models for each output.
- Add `csv` and `md` formatters.
- `--date` / `--from` / `--to` date helpers (shared util).
- Pagination handled internally for `activity list` (Garmin caps `limit` at 1000).
- `pace cache clear|stats`.

Deliverable: feature-complete read CLI for typical daily/weekly use.

### Stage 3 -- Dashboards & polish

Goal: aggregate views, polished rendering, progress UI.

- `pace today`: parallel fetch of summary, sleep, HR, body-battery, training-readiness; rendered as a multi-panel Rich dashboard.
- `progress.py` Live UI wired via `aionode.walk_dag()`; shown when >=2 in-flight requests or stderr is a TTY.
- Sparklines for step/HR trends (Rich's bar plot or unicode block chars).
- Color-coded status (good/warn/bad thresholds per metric).
- Config file `~/.pace/config.toml` for default format, cache TTLs, dashboard layout.
- Shell completions (`typer` built-in: `pace --install-completion`).

Deliverable: production-quality daily-driver CLI.

### Stage 4 -- Advanced

Goal: extensibility, history, exports.

- `pace activity export --from --to --format gpx|fit|tcx` (uses `download_activity`).
- Historical trends: `pace wellness trend hrv --days 90` with inline chart.
- Man page generation pipeline (`click-man` -> `docs/man/`), packaged with the wheel.
- Plugin hooks: entry point `pace.commands` so third-party packages can register subcommands.
- Optional notification hooks (e.g. low body-battery alert) via a simple `pace watch` subcommand.

Deliverable: extensible platform; advanced users get history, exports, plugins.

## Critical Files To Modify / Create

- `pyproject.toml` -- add deps, entry point.
- New: `pace/` package (per layout).
- Reuse logic from `garmin_api.py:22-125` (rate limiter, safe call) and `garmin_api.py:132-207` (auth/MFA).
- Reference: `demo.py:3354` (`execute_api_call` router) for endpoint coverage during Stage 2.
- Delete (later): top-level `pace` shell stub once the entry point is wired.

## Verification

Per stage:

- Unit: pytest for rate limiter (token bucket math, sliding window), cache (TTL expiry, hit/miss), formatters (snapshot tests against fixture dicts).
- Smoke: `pace profile show`, `pace wellness today`, `pace activity list --limit 5` against a real account.
- Output formats: pipe through `jq`, import CSV into Numbers/Excel, render Markdown in a viewer.
- Rate limit: run `pace today` 20 times in a loop; observe no 429s and progress UI behaves.
- Cache: re-run the same command with `--no-cache` vs cached; verify cache hit avoids API call (assertable via aionode DAG inspection).
- Help: `pace --help`, `pace activity --help`, `man pace-activity` (after Stage 4).
- Types/lint: `ruff check` + `ty check` green.
