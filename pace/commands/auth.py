"""Auth commands: status, login (interactive, human-only), logout."""

import typer

from pace import auth as auth_mod
from pace.envelope import Result, run

app = typer.Typer(no_args_is_help=True, help="Authentication and token management.")


@app.command("status")
def status() -> None:
    """Whether cached Garmin tokens exist."""
    async def work() -> Result:
        s = auth_mod.auth_status()
        return Result({"logged_in": bool(s["authenticated"]), "token_store": str(s["token_path"])})

    run("auth status", {}, work)


@app.command("login")
def login() -> None:
    """Interactive login with MFA. Meant to be run by a human in a terminal, not by an agent."""
    client = auth_mod.init_api()
    if client is None:
        typer.echo("Login failed.", err=True)
        raise typer.Exit(1)
    typer.echo("Logged in. Tokens stored at " + str(auth_mod.TOKEN_STORE))


@app.command("logout")
def logout() -> None:
    """Delete cached tokens."""
    async def work() -> Result:
        return Result({"removed": auth_mod.logout()})

    run("auth logout", {}, work)
