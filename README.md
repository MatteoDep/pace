# Pace

A CLI for Garmin Connect data.
Read-only, scriptable, async-native, with multi-format output (Rich tables, JSON, CSV, Markdown) and a local SQLite cache.

## Install

```bash
uv sync
```

## First-run authentication

```bash
pace auth login                 # prompts for email/password + MFA (or uses EMAIL/PASSWORD env vars)
pace auth status                # show token state
pace auth logout                # delete cached tokens
```

Tokens are cached at `~/.garminconnect/` (override with `GARMINTOKENS`).

## Command groups

| Group | Examples |
|---|---|
| `activity` | `list`, `show <id>`, `splits <id>`, `weather <id>`, `last` |
| `wellness` | `today`, `sleep`, `hr`, `stress`, `body-battery`, `hrv`, `hydration`, `spo2` |
| `training` | `status`, `readiness`, `race-predictions`, `endurance`, `fitness-age`, `hill-score` |
| `device` | `list`, `last-used`, `settings <id>`, `alarms`, `solar <id>` |
| `gear` | `list`, `stats <uuid>`, `defaults` |
| `goal` | `list`, `personal-records` |
| `profile` | `show`, `settings`, `unit-system` |
| `cache` | `stats`, `clear`, `path` |
| `auth` | `login`, `status`, `logout` |

Run `pace <group> --help` for the full list.

## Common options

- `--format {table,json,csv,md}` -- output format.
Default: `table` when stdout is a TTY, else `json`.
- `--no-cache` -- bypass the local cache.
- `--cache-ttl <seconds>` -- override the default TTL for a given command.
- `--date YYYY-MM-DD` -- for date-keyed commands; accepts `today`, `yesterday`, `-Nd`.

## Examples

```bash
pace profile show
pace wellness today --format json | jq .totalSteps
pace activity list --limit 10 --format csv > activities.csv
pace activity show 10000000001 --format md
pace cache stats
```

## Cache

Responses are cached at `~/.pace/cache.db` (override with `PACE_HOME`).
TTLs default to 5 min for today's wellness data, 1 hour for training metrics, 1 day for static data (devices, gear), 7 days for profile.

## Development

```bash
uv run pytest
uv run ruff check
uv run ty check
```

## Status

- Stage 1 (skeleton, auth, cache, `profile show`): done.
- Stage 2 (full read-command coverage, all formats): done.
- Stage 3 (dashboards, progress UI, sparklines): pending.
- Stage 4 (exports, history, plugins, man pages): pending.

See `docs/STAGE1.md`, `docs/STAGE2.md`, `PROPOSAL.md` for details.
