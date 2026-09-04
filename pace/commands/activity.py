"""Activity subcommands."""

import asyncio
from enum import Enum
from pathlib import Path

import typer

from garminconnect import Garmin
from pace.api import paginate, safe_call
from pace.commands._helpers import cache_for, cached_call, run
from pace.dates import parse_date
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


class ExportFormat(str, Enum):
    GPX = "gpx"
    TCX = "tcx"
    KML = "kml"
    ORIGINAL = "original"  # zip containing .fit


_DL_FMT_MAP = {
    ExportFormat.GPX: Garmin.ActivityDownloadFormat.GPX,
    ExportFormat.TCX: Garmin.ActivityDownloadFormat.TCX,
    ExportFormat.KML: Garmin.ActivityDownloadFormat.KML,
    ExportFormat.ORIGINAL: Garmin.ActivityDownloadFormat.ORIGINAL,
}


def _ext_for(fmt: ExportFormat) -> str:
    return ".zip" if fmt is ExportFormat.ORIGINAL else f".{fmt.value}"


async def _download_one(activity_id: int, fmt: ExportFormat) -> bytes:
    ok, data, err = await safe_call("download_activity", str(activity_id), _DL_FMT_MAP[fmt])
    if not ok:
        raise RuntimeError(err.message)
    return data


@activity_app.command("export")
def export(
    activity_id: int = typer.Argument(..., help="Activity ID."),
    fmt: ExportFormat = typer.Option(ExportFormat.GPX, "--format", "-f", help="Export format."),
    out: Path | None = typer.Option(
        None,
        "--out",
        "-o",
        help="Output file (default: <activity_id>.<ext> in cwd).",
    ),
) -> None:
    """Download an activity in GPX/TCX/KML/original format."""
    try:
        data = asyncio.run(_download_one(activity_id, fmt))
    except RuntimeError as err:
        typer.secho(f"Error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    target = out or Path(f"{activity_id}{_ext_for(fmt)}")
    target.write_bytes(data)
    typer.secho(f"Wrote {len(data)} bytes -> {target}", fg=typer.colors.GREEN)


@activity_app.command("export-range")
def export_range(
    from_: str = typer.Option(..., "--from", "-F", help="Start date (YYYY-MM-DD | -Nd)."),
    to: str = typer.Option("today", "--to", "-T"),
    fmt: ExportFormat = typer.Option(ExportFormat.GPX, "--format", "-f"),
    out_dir: Path = typer.Option(Path("./activities"), "--out-dir", "-d"),
    activity_type: str | None = typer.Option(None, "--type", "-t"),
) -> None:
    """Download every activity in a date range to `--out-dir`."""
    async def go() -> int:
        start_d = parse_date(from_).isoformat()
        end_d = parse_date(to).isoformat()
        ok, listing, err = await safe_call(
            "get_activities_by_date", start_d, end_d, activity_type, 1000
        )
        if not ok:
            raise RuntimeError(err.message)
        listing = listing or []
        out_dir.mkdir(parents=True, exist_ok=True)

        async def fetch_one(aid: int) -> Path:
            data = await _download_one(aid, fmt)
            target = out_dir / f"{aid}{_ext_for(fmt)}"
            target.write_bytes(data)
            return target

        await asyncio.gather(*(fetch_one(a["activityId"]) for a in listing))
        return len(listing)

    try:
        n = asyncio.run(go())
    except RuntimeError as err:
        typer.secho(f"Error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    typer.secho(f"Exported {n} activities to {out_dir}", fg=typer.colors.GREEN)


def _flatten_activity(a: dict) -> dict:
    """Lift commonly-needed nested fields into top-level columns."""
    out = dict(a)
    if isinstance(a.get("activityType"), dict):
        out["activityType"] = a["activityType"].get("typeKey")
    return out
