# Stage 4 -- Interactive browse, exports, history, plugins

Goal: turn pace from a one-shot CLI into a full platform.
Interactive Textual mode for exploration, file exports, multi-day trend charts, man pages, and a plugin interface so third parties can register new commands.

Exit criteria: `pace browse activity` opens a navigable TUI; `pace activity export` writes GPX/FIT/TCX; `pace wellness trend hrv --days 90` shows an inline chart; an external pip package can add a top-level subcommand.

Prerequisite: Stage 3 (dashboards + progress UI) complete.
Several pieces (Live, Rich panels, aionode DAG) carry over directly.

## Task breakdown

### 1. Interactive TUI (`pace browse <group>`)

- Add dep: `textual>=0.80`.
- New module `pace/tui/`:
  - `app.py` -- `class PaceApp(App)` root with header + tabbed content.
  - `widgets/data_table.py` -- wraps Textual `DataTable`, populated from a `ListModel`.
Sort, filter, column toggle.
  - `widgets/detail.py` -- `Tree` widget that expands nested dicts/lists when a row is selected (fixes the `<dict>` cell limitation noted in Stage 2).
  - `screens/activity.py`, `wellness.py`, etc. -- one screen per command group.
- New commands:
  - `pace browse activity` -- launches activity list TUI with drill-down to splits/weather.
  - `pace browse wellness` -- daily metrics with date picker.
  - `pace browse today` -- live dashboard, refreshes via aionode-tracked fetches.
- Reuse: existing `Cache`, `safe_call`, `RateLimiter`, Pydantic models.
The TUI is a new presentation layer, not a parallel data path.
- Keep one-shot CLI as default; TUI strictly opt-in (`browse` subcommand or `--interactive`/`-i` flag).
- Check: `pace browse activity` opens; arrow keys navigate; Enter drills into selected row; `q` quits.

### 2. Activity exports

- `pace activity export <id> --format {gpx,fit,tcx} [--out PATH]` -- uses `download_activity(id, dl_fmt)`.
- `pace activity export-range --from --to --format gpx [--out-dir DIR]` -- iterate `get_activities_by_date`, fetch each in parallel through the rate limiter.
- Progress UI (Stage 3) shows per-file status.
- Check: `pace activity export 10000000001 --format gpx --out ./run.gpx`; file is valid GPX (parseable with `xmllint --noout`).

### 3. Historical trends

- `pace wellness trend <metric> --days N [--from --to]` -- wraps `paginate`-style fetch across a date range.
- New model `TrendSeries(metric: str, points: list[TrendPoint])` where `TrendPoint = {date: date, value: float|None}`.
- New renderer `render/chart.py` -- inline Rich bar chart using unicode block characters (or `rich.bar.Bar`).
Color-coded thresholds (good/warn/bad) reused from Stage 3.
- Supported metrics: hrv, rhr, sleep duration, steps, body-battery, training-readiness, weight, vo2max.
- CSV/JSON/MD output still works (table of date,value).
- Check: `pace wellness trend hrv --days 90 --format table` renders a chart; `--format csv` exports raw points.

### 4. Man pages

- Dev script `scripts/build_manpages.py` -- iterates the Typer app's Click subcommands, calls `click-man` to write nroff to `docs/man/`.
- `pyproject.toml`: add `[tool.hatch.build.targets.wheel.shared-data]` entry so `pace` man pages install to `share/man/man1/`.
- README section: `man pace`, `man pace-activity`, etc.
- Check: `MANPATH=docs/man man pace-activity` renders correctly after build.

### 5. Plugin interface

- Define entry-point group `pace.commands`:
  ```toml
  # in a third-party plugin's pyproject.toml
  [project.entry-points."pace.commands"]
  myplugin = "myplugin.cli:app"
  ```
- `pace/cli.py` discovers entry points at startup via `importlib.metadata.entry_points(group="pace.commands")` and `app.add_typer(...)` each one.
- Failure mode: a plugin import error is logged to stderr, doesn't crash pace.
- Document plugin contract in `docs/PLUGINS.md`: must export a `typer.Typer` instance named `app`; should reuse `pace.api.safe_call` and `pace.cache.Cache` for rate-limit and cache compliance.
- Check: build a toy plugin `pace-hello`, install with `pip install -e ./pace-hello`, verify `pace hello world` runs.

### 6. Notification / watch mode

- `pace watch <metric> --threshold <expr> --on-cross <cmd>` -- polls the metric on a schedule (e.g. every 5 min), runs a shell hook when a threshold is crossed.
- Use Stage 3's progress UI to show poll status.
- Simple expression: `--threshold "body_battery < 20"`.
- Single-shot only; for long-running, suggest systemd timer or cron.
- Check: `pace watch body-battery --threshold "value < 50" --on-cross 'echo low'`; manually trigger by editing the cached value to verify hook fires.

### 7. Polish

- Update README with new commands + screenshots/asciicast of TUI.
- Add `pace --completion` install instructions to README.
- Bump version to `1.0.0`.

## Out of scope

- Write/upload operations on activities (Garmin's write API is separate; explicit user request only).
- Mobile/web UI -- pace stays a terminal tool.
- Cloud sync of cache.

## Risks / open questions

- Textual's keybindings may collide with terminal multiplexer bindings (tmux, screen).
Document `--no-mouse` fallback.
- Plugin entry-point startup overhead -- if many plugins are installed, `pace --help` could slow down.
Mitigate with lazy loading (defer plugin import until subcommand actually invoked).
- click-man pipeline brittle if Typer/Click bump majors -- pin versions, add CI smoke.
