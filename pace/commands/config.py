"""Config subcommands: show, path."""

import sys

import typer

from pace.config import CONFIG_FILE, load_config
from pace.models.raw import RawData
from pace.render import Format, default_format, render

config_app = typer.Typer(no_args_is_help=True, help="Inspect the user configuration.")


@config_app.command("show")
def show(
    fmt: Format = typer.Option(None, "--format", "-f"),
) -> None:
    """Display the current effective config."""
    cfg = load_config()
    render(RawData.from_response(cfg.model_dump()), fmt or default_format(), sys.stdout)


@config_app.command("path")
def path() -> None:
    """Print the config file path."""
    typer.echo(str(CONFIG_FILE))
