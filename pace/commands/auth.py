"""Auth subcommands: login, status, logout."""

import sys

import typer

from pace import auth as auth_mod
from pace.models.auth import AuthStatus
from pace.render import Format, default_format, render

auth_app = typer.Typer(no_args_is_help=True, help="Authentication and token management.")


@auth_app.command("login")
def login() -> None:
    """Authenticate with Garmin Connect (uses cached tokens or prompts)."""
    client = auth_mod.init_api()
    if client is None:
        typer.secho("Login failed.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.secho("Logged in.", fg=typer.colors.GREEN)


@auth_app.command("status")
def status(
    fmt: Format = typer.Option(None, "--format", "-f", help="Output format."),
) -> None:
    """Show whether cached tokens exist."""
    s = auth_mod.auth_status()
    model = AuthStatus(
        authenticated=bool(s["authenticated"]), token_path=str(s["token_path"])
    )
    render(model, fmt or default_format(), sys.stdout)


@auth_app.command("logout")
def logout(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete cached tokens."""
    if not yes:
        confirm = typer.confirm("Delete cached Garmin tokens?", default=False)
        if not confirm:
            raise typer.Exit()
    removed = auth_mod.logout()
    if removed:
        typer.secho("Tokens removed.", fg=typer.colors.GREEN)
    else:
        typer.secho("No tokens to remove.", fg=typer.colors.YELLOW)
