"""Goal subcommands: user goals and personal records."""

import typer

from pace.commands._helpers import cache_for, cached_call, run
from pace.models.list import ListModel
from pace.render import Format

goal_app = typer.Typer(no_args_is_help=True, help="User goals and personal records.")

_TTL = 86400


@goal_app.command("list")
def list_(
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """List configured goals."""
    async def fetch() -> ListModel:
        cache = cache_for(no_cache)
        try:
            data = await cached_call("get_goals", cache=cache, ttl=_TTL)
        finally:
            if cache is not None:
                cache.close()
        items = data if isinstance(data, list) else (data.get("goals", []) if isinstance(data, dict) else [])
        return ListModel(items=items, title="goals")

    run(fetch, fmt)


@goal_app.command("personal-records")
def personal_records(
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Personal records across activity types."""
    async def fetch() -> ListModel:
        cache = cache_for(no_cache)
        try:
            data = await cached_call("get_personal_record", cache=cache, ttl=_TTL)
        finally:
            if cache is not None:
                cache.close()
        items = data if isinstance(data, list) else []
        return ListModel(items=items, title="personal records")

    run(fetch, fmt)
