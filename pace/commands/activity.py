"""Activity subcommands."""

import typer

from pace.api import paginate
from pace.commands._helpers import cache_for, cached_call, run
from pace.models.list import ListModel
from pace.models.raw import RawData
from pace.render import Format

activity_app = typer.Typer(no_args_is_help=True, help="Workouts and activity history.")

_LIST_TTL = 600
_DETAIL_TTL = 86400

_LIST_COLUMNS = [
    "activityId",
    "activityName",
    "startTimeLocal",
    "activityType",
    "distance",
    "duration",
    "averageHR",
]


@activity_app.command("list")
def list_(
    limit: int = typer.Option(20, "--limit", "-n", help="Max activities to return."),
    type_: str | None = typer.Option(
        None,
        "--type",
        "-t",
        help="Filter by activity type (e.g. running, cycling).",
    ),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """List recent activities."""
    async def fetch() -> ListModel:
        cache = cache_for(no_cache)
        try:
            if type_:
                async def fetch_page():
                    return await paginate(
                        "get_activities",
                        page_size=min(limit, 1000),
                        max_items=limit,
                        activitytype=type_,
                    )
            else:
                async def fetch_page():
                    return await paginate(
                        "get_activities",
                        page_size=min(limit, 1000),
                        max_items=limit,
                    )
            key = f"activities:limit={limit}:type={type_}"
            if cache is None:
                data = await fetch_page()
            else:
                data = await cache.get_or_fetch(key, fetch_page, ttl=_LIST_TTL)
        finally:
            if cache is not None:
                cache.close()
        rows = [_flatten_activity(a) for a in data]
        return ListModel(items=rows, columns=_LIST_COLUMNS, title="activities")

    run(fetch, fmt)


@activity_app.command("show")
def show(
    activity_id: int = typer.Argument(...),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Show one activity by ID."""
    async def fetch() -> RawData:
        cache = cache_for(no_cache)
        try:
            data = await cached_call("get_activity", activity_id, cache=cache, ttl=_DETAIL_TTL)
        finally:
            if cache is not None:
                cache.close()
        return RawData.from_response(data)

    run(fetch, fmt)


@activity_app.command("splits")
def splits(
    activity_id: int = typer.Argument(...),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Show splits/laps for an activity."""
    async def fetch() -> RawData:
        cache = cache_for(no_cache)
        try:
            data = await cached_call("get_activity_splits", activity_id, cache=cache, ttl=_DETAIL_TTL)
        finally:
            if cache is not None:
                cache.close()
        return RawData.from_response(data)

    run(fetch, fmt)


@activity_app.command("weather")
def weather(
    activity_id: int = typer.Argument(...),
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Weather conditions during an activity."""
    async def fetch() -> RawData:
        cache = cache_for(no_cache)
        try:
            data = await cached_call("get_activity_weather", activity_id, cache=cache, ttl=_DETAIL_TTL)
        finally:
            if cache is not None:
                cache.close()
        return RawData.from_response(data)

    run(fetch, fmt)


@activity_app.command("last")
def last(
    fmt: Format = typer.Option(None, "--format", "-f"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Show the most recent activity."""
    async def fetch() -> RawData:
        cache = cache_for(no_cache)
        try:
            data = await cached_call("get_last_activity", cache=cache, ttl=_LIST_TTL)
        finally:
            if cache is not None:
                cache.close()
        return RawData.from_response(data)

    run(fetch, fmt)


def _flatten_activity(a: dict) -> dict:
    """Lift commonly-needed nested fields into top-level columns."""
    out = dict(a)
    if isinstance(a.get("activityType"), dict):
        out["activityType"] = a["activityType"].get("typeKey")
    return out
