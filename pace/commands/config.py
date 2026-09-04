"""Config commands: show, path."""

import typer

from pace import config
from pace.envelope import Result, run

app = typer.Typer(no_args_is_help=True, help="Inspect the user configuration.")


@app.command("show")
def show() -> None:
    """Effective configuration."""
    async def work() -> Result:
        return Result(config.load_config().model_dump())

    run("config show", {}, work)


@app.command("path")
def path() -> None:
    """Config file path."""
    async def work() -> Result:
        return Result({"path": str(config.CONFIG_FILE)})

    run("config path", {}, work)
