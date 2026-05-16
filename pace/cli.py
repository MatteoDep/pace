"""Pace CLI entry point."""

import typer

from pace import __version__
from pace.commands.auth import auth_app
from pace.commands.profile import profile_app

app = typer.Typer(
    name="pace",
    help="Pace -- a CLI for Garmin Connect data.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

app.add_typer(auth_app, name="auth", help="Authentication and token management.")
app.add_typer(profile_app, name="profile", help="User profile information.")


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
