"""Pace CLI entry point."""

import typer

from pace import __version__
from pace.commands import activity, device, gear, goal, profile, training, wellness
from pace.commands.auth import auth_app
from pace.commands.cache import cache_app
from pace.commands.config import config_app
from pace.plugins import load_plugins

app = typer.Typer(
    name="pace",
    help="Pace -- a CLI for Garmin Connect data.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

app.add_typer(activity.app, name="activity")
app.add_typer(auth_app, name="auth", help="Authentication and token management.")
app.add_typer(cache_app, name="cache", help="Manage the local response cache.")
app.add_typer(config_app, name="config", help="Inspect the user configuration.")
app.add_typer(device.app, name="device")
app.add_typer(gear.app, name="gear")
app.add_typer(goal.app, name="goal")
app.add_typer(profile.app, name="profile")
app.add_typer(training.app, name="training")
app.add_typer(wellness.app, name="wellness")
load_plugins(app)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"pace {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    pass
