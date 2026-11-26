"""Pace CLI entry point."""

import sys

import click
import typer
from typer.main import get_command

from pace import __version__, envelope
from pace.commands import activity, auth, cache, chart, config, device, diary, export, gear, goal, load, log, plan, profile, schema, training, wellness, workout, zones
from pace.plugins import load_plugins

app = typer.Typer(
    name="pace",
    help="Machine-first CLI for Garmin Connect data. Every call prints one JSON envelope.",
    no_args_is_help=True,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
)

for module, name in (
    (activity, "activity"), (auth, "auth"), (cache, "cache"), (config, "config"), (device, "device"), (diary, "diary"),
    (gear, "gear"), (goal, "goal"), (log, "log"), (plan, "plan"), (profile, "profile"), (training, "training"), (wellness, "wellness"),
    (workout, "workout"), (zones, "zones"),
):
    app.add_typer(module.app, name=name)
app.command("schema", help="Machine-readable command manifest.")(schema.schema)
app.command("load", help="Weekly and daily training load.")(load.load)
app.command("chart", help="Render a PNG chart: volume, plan, recovery, sleep, weight, intensity or activity ID.")(chart.chart)
app.command("export", help="Run the dashboard recipe and write the bundle file.")(export.export)
load_plugins(app)


def _version(value: bool) -> None:
    if value:
        envelope.emit(envelope.ok("version", {}, envelope.Result({"version": __version__})))
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(False, "--version", "-V", callback=_version, is_eager=True),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Log fetch/cache events to stderr."),
) -> None:
    envelope.set_verbose(verbose)


def _cmd() -> str:
    """The group + command the user meant, for an envelope built outside any command.

    Flags are dropped *before* the tokens are taken, so a root flag
    (`pace -v wellness sleep`) does not push the command name out of view.

    Only tokens that actually resolve in the click tree are kept, walking it level by
    level and stopping at the first token that is not a sub-group or command. So the
    three-level `pace log weight add` (missing KG) names all three levels, while a
    positional value (`pace activity show 123`) is not mistaken for a third."""
    node: click.Command = get_command(app)
    out: list[str] = []
    for token in [a for a in sys.argv[1:] if not a.startswith("-")][:3]:
        commands = getattr(node, "commands", None)
        if not commands or token not in commands:
            break
        out.append(token)
        node = commands[token]
    return " ".join(out)


def main() -> None:
    """Console entry point: usage errors become bad_args envelopes.

    With `standalone_mode=False`, click *returns* the exit code of a
    `typer.Exit` raised inside a command (e.g. by `envelope.fail()`) rather
    than raising it -- so that exit code must be propagated explicitly via
    `sys.exit(rv)`, not just assumed to be 0.
    """
    try:
        rv = app(standalone_mode=False)
    except click.exceptions.Abort:
        envelope.emit(envelope.error(_cmd(), envelope.PaceError("internal", "Aborted by user.")))
        sys.exit(130)
    except click.ClickException as err:  # UsageError and friends
        envelope.emit(envelope.error(_cmd(), envelope.PaceError("bad_args", err.format_message())))
        sys.exit(2)
    sys.exit(rv if isinstance(rv, int) else 0)
