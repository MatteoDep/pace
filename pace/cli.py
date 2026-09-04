"""Pace CLI entry point."""

import sys

import click
import typer

from pace import __version__, envelope
from pace.commands import activity, auth, cache, config, device, gear, goal, profile, training, wellness
from pace.plugins import load_plugins

app = typer.Typer(
    name="pace",
    help="Machine-first CLI for Garmin Connect data. Every call prints one JSON envelope.",
    no_args_is_help=True,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
)

for module, name in (
    (activity, "activity"), (auth, "auth"), (cache, "cache"), (config, "config"), (device, "device"),
    (gear, "gear"), (goal, "goal"), (profile, "profile"), (training, "training"), (wellness, "wellness"),
):
    app.add_typer(module.app, name=name)
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


def main() -> None:
    """Console entry point: usage errors become bad_args envelopes."""
    try:
        app(standalone_mode=False)
    except click.UsageError as err:
        cmd = " ".join(sys.argv[1:2])
        envelope.emit(envelope.error(cmd, envelope.PaceError("bad_args", err.format_message())))
        sys.exit(2)
    except click.exceptions.Exit as err:
        sys.exit(err.exit_code)
