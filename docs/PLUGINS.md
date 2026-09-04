# Pace plugins

Third-party packages can register new top-level command groups via the
`pace.commands` entry-point.

## Contract

1.
Export a `typer.Typer` instance from a module in your package.
2.
Declare it in your `pyproject.toml`:

   ```toml
   [project.entry-points."pace.commands"]
   hello = "myplugin.cli:app"
   ```

   `hello` becomes the subcommand name -- `pace hello ...`.

3.
Reuse pace's infrastructure where applicable:

   - `pace.api.safe_call(method, ...)` -- rate-limited Garmin client; returns `(ok, data, ApiError | None)`.
   - `pace.commands._helpers.cached_call(method, *args, cache=, ttl=, tracker=)` -- cached call raising `PaceError`.
   - `pace.envelope.run(command, args, work)` -- run an async `work() -> Result` and print the envelope.
   - `pace.shape.apply(...)` -- prune, field selection, series bucketing.
   - `pace.factory.FIELDS_OPT / BUCKET_OPT / RAW_OPT / NO_CACHE_OPT / FROM_OPT / TO_OPT / DAYS_OPT` -- the standard flags.
   - `pace.factory.build_command(app, EndpointSpec(...))` -- declare an endpoint instead of writing a command.

Plugins must print nothing to stdout except through `pace.envelope`.

## Minimal example

```toml
# pyproject.toml
[project]
name = "pace-hello"
dependencies = ["pace-cli", "typer"]

[project.entry-points."pace.commands"]
hello = "pace_hello.cli:app"
```

```python
# pace_hello/cli.py
import typer

app = typer.Typer(help="Demo plugin.")

@app.command("world")
def world() -> None:
    typer.echo("hello from a pace plugin")
```

After `pip install -e .`, the subcommand appears in `pace --help` and runs
as `pace hello world`.

## Error handling

Load failures are reported to stderr but never abort pace startup; the
broken plugin is silently dropped from the command list.
Make sure your top-level imports are cheap -- they run on every pace
invocation.
