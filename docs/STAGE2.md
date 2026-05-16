# Stage 2 -- Core read commands

Goal: full domain coverage of the most-used Garmin Connect endpoints, all four output formats (table, json, csv, md), shared date helpers, and `cache` subcommand.

Exit criteria: every command in PROPOSAL.md's Stage 2 table is implemented, returns a Pydantic model, and renders correctly in all four formats.

## Task breakdown

### 1. CSV + Markdown formatters

- `pace/render/csv_.py` + `pace/render/md.py` registered via `@register`.
- For scalar models: 2-column field/value rows.
- For list-of-records models (see `models/list.py`): header + one row per record.

### 2. List-aware table renderer

- `pace/render/table.py` extended: if model has a single `items: list[BaseModel]` field, render as multi-column table using the inner model's fields as columns.
- All collection commands (activity list, weigh-ins, badges, devices ...) use a `ListModel(items=[...])` wrapper.

### 3. Date helpers

- `pace/dates.py`: `parse_date(s) -> date` (accepts `YYYY-MM-DD`, `today`, `yesterday`, `-7d`).
- `DateOption`, `FromOption`, `ToOption` Typer Option factories.
- `date_range(start, end) -> Iterable[date]` for multi-day pulls.

### 4. Generic models + pagination

- `pace/models/raw.py`: `RawData(BaseModel)` -- wraps a dict via `model_config = {"extra": "allow"}` so any Garmin response can be turned into a model without hand-spec.
- `pace/models/list.py`: `ListModel[T](items: list[T])` for collections.
- `pace/api.py`: helper `paginate(method_name, *, page_size=1000, max_items=None)` for `get_activities`.

### 5. `cache` subcommand

- `pace/commands/cache.py`: `clear`, `stats`, `path`. Wired into `cli.py`.

### 6. Profile -- add `settings`, `unit-system`

- New commands using existing `Profile` patterns.

### 7. Wellness commands (8)

`today`, `sleep`, `hr`, `stress`, `body-battery`, `hrv`, `hydration`, `spo2` -- all take optional `--date` (default today).

### 8. Training commands (6)

`status`, `readiness`, `race-predictions`, `endurance`, `fitness-age`, `hill-score`.

### 9. Activity commands (5)

`list` (paginated, `--limit`, `--type`), `show <id>`, `splits <id>`, `weather <id>`, `last`.

### 10. Device commands (5)

`list`, `last-used`, `settings <id>`, `alarms`, `solar <id>`.

### 11. Gear commands (3)

`list`, `stats <uuid>`, `defaults`.

### 12. Goal commands (2)

`list`, `personal-records`.

### 13. Verify

- `uv run pace <group> --help` covers all subcommands.
- One smoke test per group hitting a real account.
- `uv run pytest`, `uv run ruff check`, `uv run ty check` all green.

## Out of scope (Stage 3+)

- Dashboards / aggregate views (`pace today`).
- Progress UI via `aionode.walk_dag`.
- Sparklines, color-coded thresholds.
- Config TOML.
