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

## Shell completion

Typer ships completion for bash, zsh, fish, and PowerShell:

```bash
pace --install-completion           # auto-detects current shell
pace --show-completion              # print the script to stdout
```

After install, restart your shell.
Then tab-complete subcommands: `pace acti<TAB>` -> `pace activity`.

## Configuration

User config lives at `~/.pace/config.toml` (or `$PACE_HOME/config.toml`).

```toml
default_format = "json"     # used when no --format flag and no TTY hint
dashboard_panels = ["steps", "sleep", "hr", "body_battery", "readiness", "hrv"]

[cache_ttl_overrides]
"wellness.today" = 60       # override per-command TTLs (seconds)
```

Run `pace config show` to view the effective config, `pace config path` to print the file location.

## Development

```bash
uv run pytest
uv run ruff check
uv run ty check
```

## Status

- Stage 1 (skeleton, auth, cache, `profile show`): done.
- Stage 2 (full read-command coverage, all formats): done.
- Stage 3 (dashboards, progress UI, sparklines, config, completion): done.
- Stage 4 (TUI browse, exports, history, plugins, man pages): pending.

See `docs/STAGE1.md`, `docs/STAGE2.md`, `docs/STAGE3.md`, `docs/STAGE4.md`, `PROPOSAL.md` for details.
