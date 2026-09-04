"""Pace CLI entry point."""

import typer

from pace import __version__
from pace.commands.activity import activity_app
from pace.commands.auth import auth_app
from pace.commands.cache import cache_app
from pace.commands.config import config_app
from pace.commands.device import device_app
from pace.commands.gear import gear_app
from pace.commands.goal import goal_app
from pace.commands.profile import profile_app
from pace.commands.training import training_app
from pace.commands.wellness import wellness_app
from pace.plugins import load_plugins

app = typer.Typer(
    name="pace",
    help="Pace -- a CLI for Garmin Connect data.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

app.add_typer(activity_app, name="activity", help="Workouts and activity history.")
app.add_typer(auth_app, name="auth", help="Authentication and token management.")
app.add_typer(cache_app, name="cache", help="Manage the local response cache.")
app.add_typer(config_app, name="config", help="Inspect the user configuration.")
app.add_typer(device_app, name="device", help="Registered Garmin devices.")
app.add_typer(gear_app, name="gear", help="Gear inventory and usage.")
app.add_typer(goal_app, name="goal", help="User goals and personal records.")
app.add_typer(profile_app, name="profile", help="User profile information.")
app.add_typer(training_app, name="training", help="Training load and performance metrics.")
app.add_typer(wellness_app, name="wellness", help="Daily health metrics.")
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
