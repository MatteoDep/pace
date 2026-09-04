"""Cache commands: stats, clear, path."""

import typer

from pace import config
from pace.cache import Cache
from pace.envelope import Result, run

app = typer.Typer(no_args_is_help=True, help="Manage the local response cache.")


@app.command("stats")
def stats() -> None:
    """Entry count and size."""
    async def work() -> Result:
        c = Cache()
        try:
            s = c.stats()
        finally:
            c.close()
        return Result({"path": str(config.CACHE_DB), **s})

    run("cache stats", {}, work)


@app.command("clear")
def clear() -> None:
    """Delete all cached responses."""
    async def work() -> Result:
        c = Cache()
        try:
            return Result({"removed": c.clear()})
        finally:
            c.close()

    run("cache clear", {}, work)


@app.command("path")
def path() -> None:
    """Cache database path."""
    async def work() -> Result:
        return Result({"path": str(config.CACHE_DB)})

    run("cache path", {}, work)
