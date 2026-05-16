# Stage 1 -- Skeleton

Goal: installable `pace` CLI with `auth` subcommands + `profile show`, async rate-limited API client, SQLite cache, table + JSON output.

Exit criteria: `uv run pace profile show` prints user profile as Rich table; `... profile show --format json` prints valid JSON; cache hit on second run; `uv run pace auth status` reports token state.

## Task breakdown

Ordered for incremental verifiability.
Each task ends with a runnable check.

### 1. Dependencies & entry point

- Edit `pyproject.toml`:
  - Add to `dependencies`: `typer>=0.12`, `rich>=13.7`, `aionode>=0.3.0`.
  - Add to `dev`: `click-man>=0.5`, `pytest>=8`, `pytest-asyncio>=0.23`.
  - Add `[project.scripts]` table: `pace = "pace.cli:app"`.
  - Add `[tool.hatch.build.targets.wheel]` (or equivalent) so the package is built.
- `uv sync` to install.
- Check: `uv run python -c "import typer, rich, aionode"` exits 0.

### 2. Package skeleton

Create empty modules so imports resolve:

```
pace/
  __init__.py
  __main__.py             # from .cli import app; app()
  cli.py                  # Typer app + sub-app registration
  config.py
  auth.py
  ratelimit.py
  api.py
  cache.py
  models/
    __init__.py
    profile.py
  render/
    __init__.py
    table.py
    json_.py
  commands/
    __init__.py
    auth.py
    profile.py
```

- Delete top-level `pace` shell stub once package entry point works.
- Check: `uv run pace --help` lists `auth` and `profile` sub-apps (even if empty).

### 3. `config.py`

- Resolve paths: `PACE_HOME = Path(os.getenv("PACE_HOME", "~/.pace")).expanduser()`.
- Sub-paths: `CACHE_DB = PACE_HOME / "cache.db"`, `TOKEN_STORE = Path(os.getenv("GARMINTOKENS", "~/.garminconnect")).expanduser()`.
- `ensure_home()` -- mkdir parents.
- No TOML loading yet (deferred to Stage 3).
- Check: `python -c "from pace.config import PACE_HOME, ensure_home; ensure_home(); print(PACE_HOME)"`.

### 4. `ratelimit.py` (async port)

- Port `_rate_limit` from `garmin_api.py:37-74` to async.
- Public surface:
  - `class RateLimiter` with caps as constructor args (defaults: 2.0 s, 10/min, 100/hr, 500/day).
  - `async def acquire(self) -> None`: replicates sliding-window math; `await asyncio.sleep(wait)` in place of `time.sleep`.
  - Guards mutation of `_call_history` / `_last_call_time` with `asyncio.Lock`.
- Module-level default instance: `limiter = RateLimiter()`.
- Module-level `concurrency = asyncio.Semaphore(3)`.
- Check: unit test `tests/test_ratelimit.py` -- assert `acquire` returns immediately when empty; assert 11th call within 60 s waits ~`60 - elapsed` s (use monkeypatched clock).

### 5. `auth.py`

- Port `init_api`, `get_credentials` from `garmin_api.py:132-207`.
- Keep them sync (auth is one-shot at startup; async port adds no value here).
- Add `auth_status() -> dict` returning `{"authenticated": bool, "token_path": str, "expires_at": str | None}` (inspect `garth` token expiry if accessible; else just check file exists).
- Add `logout()`: delete `TOKEN_STORE` directory if exists; confirm to user.
- Check: `uv run pace auth status` prints expected dict.

### 6. `api.py`

- Module-level `_api: Garmin | None = None`; `def get_api() -> Garmin` lazy-inits via `auth.init_api()`.
- `async def safe_call(method_name: str, *args, **kwargs) -> tuple[bool, Any, str | None]`:
  - `await limiter.acquire()`; `async with concurrency:` `await asyncio.to_thread(getattr(get_api(), method_name), *args, **kwargs)`.
  - Wrap with the error-handling block from `garmin_api.py:81-125` (port verbatim).
- Optionally wrap with `aionode.node` decorator so calls appear in the DAG.
- Check: `python -c "import asyncio; from pace.api import safe_call; print(asyncio.run(safe_call('get_full_name')))"`.

### 7. `cache.py`

- `class Cache` with constructor `Cache(path: Path)`.
- Schema (created on init):
  ```sql
  CREATE TABLE IF NOT EXISTS cache (
      key TEXT PRIMARY KEY,
      value BLOB NOT NULL,
      fetched_at INTEGER NOT NULL,
      ttl INTEGER NOT NULL
  );
  ```
- `async get_or_fetch(self, key: str, fetch: Callable[[], Awaitable[Any]], ttl: int) -> Any`:
  - Lookup row; if `now - fetched_at < ttl`, deserialize JSON and return.
  - Else `await fetch()`, store, return.
- `clear()`, `stats() -> {"entries": int, "size_bytes": int}` (Stage 2 wires CLI commands).
- Use `sqlite3` directly; serialize value with `json.dumps`/`json.loads`.
- Check: unit test `tests/test_cache.py` -- write a value, read it back; advance clock past TTL, verify re-fetch.

### 8. `render/`

- `render/__init__.py`:
  - `Formatter = Protocol` with `def render(self, model: BaseModel, stream: IO) -> None`.
  - `REGISTRY: dict[str, Formatter] = {}`; `def register(name: str)` decorator.
  - `def default_format() -> str` -- `"table"` if `sys.stdout.isatty()` else `"json"`.
  - `def render(model, fmt: str | None, stream=sys.stdout)` dispatcher.
- `render/json_.py`: dump via `model.model_dump_json(indent=2)`.
- `render/table.py`: generic Pydantic -> two-column Rich table (field, value); specialized renderers can override per-model.
- Check: `python -c "from pace.render import render, default_format; ..."` (sanity).

### 9. `models/profile.py`

- `class Profile(BaseModel)`: `full_name: str | None`, `user_profile_number: int | None`, `unit_system: str | None`, `display_name: str | None`.
- Factory `from_api(full_name, device_info, unit_system) -> Profile`.

### 10. `commands/auth.py`

- Typer sub-app `auth_app = typer.Typer()`.
- `@auth_app.command("login")` -> calls `auth.init_api()`.
- `@auth_app.command("status")` -> renders `auth.auth_status()` via formatter.
- `@auth_app.command("logout")` -> `auth.logout()` w/ confirmation prompt.

### 11. `commands/profile.py`

- Typer sub-app `profile_app = typer.Typer()`.
- `@profile_app.command("show")`:
  - Args: `--format` (Enum), `--no-cache` (bool), `--cache-ttl` (int, default 604800 = 7 d).
  - `asyncio.run` an inner coroutine that:
    - Parallel-fetches `get_full_name`, `get_device_last_used`, `get_unit_system` via `safe_call` wrapped in `cache.get_or_fetch` (unless `--no-cache`).
    - Builds `Profile` model.
    - Calls `render(model, fmt)`.

### 12. `cli.py`

- `app = typer.Typer(help="Trainscout -- Garmin Connect CLI", rich_markup_mode="rich")`.
- `app.add_typer(auth_app, name="auth")`, `app.add_typer(profile_app, name="profile")`.
- Global `--version`.

### 13. README quickstart

- Install: `uv sync` or `pipx install .`.
- First-run auth: `pace auth login` (env vars EMAIL/PASSWORD or prompt; MFA prompt if needed).
- Examples: `pace profile show`, `... --format json`.
- Caching: `~/.pace/cache.db`; bypass with `--no-cache`.

## Verification (end of Stage 1)

- `uv run pace --help` shows `auth` and `profile` groups.
- `uv run pace auth status` prints token state.
- `uv run pace auth login` succeeds (interactive MFA still works).
- `uv run pace profile show` renders a 4-row Rich table.
- `uv run pace profile show --format json` outputs valid JSON (verify with `... | jq`).
- Second run of `profile show` is fast (cache hit) -- verify by adding a debug log in `Cache.get_or_fetch` and observing only the first run prints "MISS".
- `uv run pytest` green (rate-limiter + cache tests).
- `uv run ruff check` and `uv run ty check` green.

## Out of scope for Stage 1

- CSV / Markdown formatters (Stage 2).
- All non-`profile` commands (Stage 2).
- Progress UI / `aionode.walk_dag` Live display (Stage 3) -- but `safe_call` should already register nodes so Stage 3 only adds the renderer.
- Config TOML, shell completions, man pages.
