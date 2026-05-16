"""Cache subcommands: stats, clear, path."""

import sys

import typer

from pace.cache import Cache
from pace.config import CACHE_DB
from pace.models.cache import CacheStats
from pace.render import Format, default_format, render

cache_app = typer.Typer(no_args_is_help=True, help="Manage the local response cache.")


@cache_app.command("stats")
def stats(
    fmt: Format = typer.Option(None, "--format", "-f", help="Output format."),
) -> None:
    """Show cache entries and size."""
    c = Cache()
    s = c.stats()
    c.close()
    render(
        CacheStats(path=str(CACHE_DB), entries=s["entries"], size_bytes=s["size_bytes"]),
        fmt or default_format(),
        sys.stdout,
    )


@cache_app.command("clear")
def clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete all cached responses."""
    if not yes and not typer.confirm("Delete all cache entries?", default=False):
        raise typer.Exit()
    c = Cache()
    removed = c.clear()
    c.close()
    typer.secho(f"Removed {removed} entries.", fg=typer.colors.GREEN)


@cache_app.command("path")
def path() -> None:
    """Print the cache database path."""
    typer.echo(str(CACHE_DB))
