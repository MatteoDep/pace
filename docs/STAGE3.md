# Stage 3 -- Dashboards & polish

Goal: aggregate `pace today` dashboard, live progress UI for parallel fetches, sparkline trend cells, color-coded thresholds, config file for defaults, shell completion.

Exit criteria: `pace today` renders a multi-panel dashboard with sparklines and color cues; long-running parallel commands show progress in the terminal; user can persist defaults via `~/.pace/config.toml`.

## Task breakdown

### 1. Progress UI (`pace/progress.py`)

- `rich.live.Live` loop polls `aionode.walk_dag()` every 100 ms while a context manager is active.
- For each `TaskInfo`: row with status icon (WAITING/RUNNING/DONE/FAILED), name, `info.duration()`.
- Auto-enable when stderr is a TTY AND ≥2 in-flight requests; disable in pipes/CI.
- Public API: `async with progress_ui(): ...` -- launches/teardowns Live; commands wrap their `gather(...)` block in it.
- Wire `safe_call` to register every call as an `aionode.node` so it shows up automatically.
- Check: `pace today` shows a live table of fetches while running.

### 2. Sparkline + chart helpers (`pace/render/chart.py`)

- Pure unicode block bars: `_BLOCKS = " ▁▂▃▄▅▆▇█"`.
- `sparkline(values: list[float | None], width: int | None = None) -> str` -- returns the unicode string; `None` rendered as space.
- `bar_row(label, value, max_value, width=20)` -- single horizontal bar for a metric.
- Used by `wellness trend` (Stage 4) AND `today` dashboard inline cells.

### 3. Threshold / color helpers (`pace/render/colors.py`)

- `class Threshold(BaseModel)` -- `{good_min, warn_min, value_dir}` (higher better vs lower better).
- `threshold_color(value, threshold) -> str` -- returns Rich style name (`green`/`yellow`/`red`/`dim`).
- Curated thresholds per metric in `pace/metrics.py`: body_battery, training_readiness, sleep_score, stress, hrv, rhr, ...
- Used inline in dashboard panels and (later) in trend renderers.

### 4. Scalar nested flattening

- Improve `RawData` rendering: flatten one level of nested dicts so `sleepStages.deep`, `dailySleepDTO.sleepTimeSeconds` show as their own rows rather than `<dict>`.
- New helper `pace/render/_flatten.py`: `flatten_dict(d, sep=".", max_depth=2) -> dict[str, scalar]`.
- Wire into `render/table.py`, `csv_.py`, `md.py` scalar paths.
- Lists of scalars stay inline as `"1, 2, 3"`; lists of dicts still show as `<list[N]>`.
- Check: `pace wellness sleep --format table` now shows individual sleep-stage durations.

### 5. `pace today` dashboard

- `pace/commands/today.py`: top-level `@app.command` (not a sub-app).
- Parallel-fetches via `asyncio.gather`:
  - `get_user_summary(today)` -- steps, calories, distance, floors.
  - `get_sleep_data(today)` -- last night's score + duration.
  - `get_body_battery(today)` -- current battery level + sparkline of last 24 h.
  - `get_training_readiness(today)` -- score + balance.
  - `get_hrv_data(today)` -- HRV status.
  - `get_heart_rates(today)` -- resting HR + min/max + 24 h sparkline.
- Rendered with `rich.layout.Layout`: 2 columns x 3 rows of panels; each panel = title + key value (colored by threshold) + sparkline.
- Progress UI active during fetch.
- Cache TTL 5 min.
- Formats: only `table` (rich) and `json` (dump all six raw payloads). CSV/MD don't fit a dashboard; gracefully error: `Format X not supported for 'today'; use individual subcommands.`
- Check: `pace today` shows all six panels with sparklines + color cues.

### 6. Config file (`~/.pace/config.toml`)

- `pace/config.py`: extend with `class PaceConfig(BaseModel)`:
  - `default_format: Format | None`
  - `cache_ttl_overrides: dict[str, int]` (keyed by command path, e.g. `"wellness.today"`)
  - `dashboard.panels: list[str]` -- order/subset for `pace today`.
- `load_config() -> PaceConfig` reads `~/.pace/config.toml` (Python 3.11+ stdlib `tomllib`); missing file -> defaults.
- Commands consult config when picking format and TTL: env var > flag > config > built-in default.
- `pace config show` / `pace config path`.
- Check: setting `default_format = "json"` in config makes piped CSV the default in a TTY.

### 7. Shell completion

- Typer ships `--install-completion` / `--show-completion` out of the box (already visible in `pace --help`).
- README: short section on enabling for bash/zsh/fish.
- Add `pace completion install bash|zsh|fish` thin wrapper that calls the underlying Typer mechanism with a friendlier name.
- Check: `pace completion install bash` writes the completion script and prints next-step instructions.

### 8. Smoke + tests + lint

- Unit tests for: sparkline math, threshold_color, flatten_dict.
- Manual smoke for `pace today` (live data).
- `pytest`, `ruff`, `ty` green.

## Out of scope (Stage 4)

- Interactive Textual TUI (`pace browse`).
- File exports (gpx/fit/tcx).
- Historical trend command + chart renderer (uses the Stage 3 chart helpers).
- Man pages.
- Plugin entry points.
- Watch / threshold hooks.
