# Pace

Machine-first CLI for Garmin Connect, built to be driven by an LLM coach.
Every invocation prints exactly one compact JSON envelope on stdout; humans are not the audience.

## Install

```bash
uv sync
```

## Authenticate (human step)

```bash
pace auth login      # interactive email/password + MFA; tokens cached in ~/.garminconnect
pace auth status     # {"ok":true,...,"data":{"logged_in":true,...}}
```

An agent must never run `pace auth login`; it asks the user to.

## Discover

```bash
pace schema              # every command, flag, unit, exit code
pace schema wellness     # one group
pace <group> --help      # Typer help still works
```

## Use

```bash
pace wellness sleep --days 7
pace wellness hr --to yesterday --bucket 1h
pace activity list --days 14 --type running
pace activity details 10000000001
pace training readiness
pace workout push plan.json --dry-run
pace workout push plan.json
pace workout schedule <id> +1d
pace log weight add 66.3
pace log hydration 500
pace activity effort <activity-id> --rpe 6 --feel strong
pace activity gear <activity-id>
pace gear link <activity-id> <gear-uuid>
pace gear unlink <activity-id> <gear-uuid>
pace diary show
pace diary add --kind feedback --ref activity:<id> "felt heavy, legs tired"
pace plan add sessions.json
pace plan show --from 2026-11-20 --to 2026-11-30
pace plan set 3 --duration 50m --distance 10km
```

To review which shoes are on many activities, avoid one `activity gear` call per activity (the rate limit is 10 calls a minute).
Run `pace gear list`, then `pace gear activities <uuid>` once per shoe, then `pace activity list` for the range: an activity in no shoe's list has no gear.
To change shoes, `gear link` the new one first, then `gear unlink` the old one.

Writes to Garmin require explicit user confirmation in the coaching skill; the CLI itself has no `--yes`.
The diary and the plan are local files under `$PACE_HOME` (default `~/.pace`), not Garmin data; the confirmation rule covers Garmin writes only, and the coaching skill decides when to ask about local writes.

Output contract: `docs/CONTRACT.md`.
Skill for Claude Code: `.claude/skills/pace/SKILL.md` -- planned (plan 3; not yet in the repo), to be installed with `ln -s "$PWD/.claude/skills/pace" ~/.claude/skills/pace`.

## Cache and config

Responses are cached in `~/.pace/cache.db` (`PACE_HOME` overrides the directory).
TTL for today's data is short (5 min wellness, 1 h training); yesterday 1 day; older 7 days.
`~/.pace/config.toml`:

```toml
[cache_ttl_overrides]
"wellness.sleep" = 60
```

An override applies to today's TTL of one registry command (`<group>.<name>`); yesterday and older days keep their fixed 1-day / 7-day TTLs.
`profile show` is hand-written and its 7-day TTL is fixed, not overridable.

## Development

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/record_fixtures.py     # refresh tests/fixtures (needs login)
```

`scripts/garmin_api_reference.py` is the upstream interactive demo, kept as a reference for method names and payload shapes.
It is not part of the dependency set: running it needs `readchar` installed by hand (`.venv/bin/pip install readchar`).
