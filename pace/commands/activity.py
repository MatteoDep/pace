"""Activity commands: list, show, gear, splits, details, weather, last, export, effort."""

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import typer

from garminconnect import Garmin
from pace.api import paginate, safe_call
from pace.cache import Cache, CacheTracker
from pace.commands._helpers import cached_call
from pace.dates import parse_date, resolve_range
from pace.effort import parse_feel, parse_rpe, with_effort
from pace.envelope import PaceError, Result, fail, run
from pace.factory import BUCKET_OPT, FIELDS_OPT, FROM_OPT, NO_CACHE_OPT, RAW_OPT, TO_OPT, shape_args
from pace.shape import BUCKETS, MAX_ROWS, apply, choose_bucket, prune, tz_offset_s
from pace.writes import DRY_RUN_OPT, run_write
from pace.writespecs import effort_spec

app = typer.Typer(no_args_is_help=True, help="Workouts and activity history; set effort (RPE/feel) on one activity.")

_LIST_TTL = 600
DETAIL_TTL = 86400
#: Gear can change in the Garmin app, which pace cannot invalidate: keep it short.
GEAR_TTL = 300
ACTIVITY_GEAR_FIELDS: tuple[str, ...] = ("uuid", "customMakeModel", "gearTypeName", "gearStatusName")

LIST_FIELDS: tuple[str, ...] = (
    "activityId", "activityName", "activityType", "startTimeLocal", "duration", "distance",
    "averageHR", "maxHR", "elevationGain", "averageSpeed", "aerobicTrainingEffect",
    "anaerobicTrainingEffect", "activityTrainingLoad", "calories",
)

# Garmin per-sample metric key -> column name in `activity details`.
DETAIL_METRICS: dict[str, str] = {
    "directHeartRate": "hr",
    "directSpeed": "speed_mps",
    "sumDistance": "dist_m",
    "directElevation": "elev_m",
    "directRunCadence": "cadence",
    "directBikeCadence": "cadence",
    "directPower": "power_w",
}
_LAST_VALUE_COLS = {"dist_m"}


def _flatten_type(a: dict) -> dict:
    out = dict(a)
    if isinstance(a.get("activityType"), dict):
        out["activityType"] = a["activityType"].get("typeKey")
    return out


def _shaped_result(data: Any, tracker: CacheTracker, *, default_fields=(), fields, bucket, raw,
                    truncated=False) -> Result:
    shaped, report = apply(data, series=(), default_fields=default_fields, fields=fields, bucket=bucket,
                            raw=raw, span_s=0)
    return Result(shaped, tracker.report(), report, truncated)


@app.command("list")
def list_(
    from_: str | None = FROM_OPT, to: str = TO_OPT,
    days: int | None = typer.Option(None, "--days", "-n",
                                    help="Number of days ending at --to (enables range mode)."),
    type_: str | None = typer.Option(None, "--type", "-t", help="running, cycling, swimming, ..."),
    limit: int = typer.Option(20, "--limit", "-l", help="Max activities returned (newest first)."),
    fields: str = FIELDS_OPT, bucket: str = BUCKET_OPT, raw: bool = RAW_OPT, no_cache: bool = NO_CACHE_OPT,
) -> None:
    """Recent activities, or every activity in a date range."""
    cmd = "activity list"
    ranged = from_ is not None or days is not None or to != "today"
    args: dict[str, Any] = {"limit": limit, "type": type_}
    try:
        f, b, echo = shape_args(fields, bucket, raw)
        if ranged:
            start, end = resolve_range(from_, to, days if days is not None else 1)
            args.update({"from": start.isoformat(), "to": end.isoformat()})
        else:
            args["to"] = parse_date(to).isoformat()
    except PaceError as err:
        fail(cmd, err)
    args.update(echo)

    async def work() -> Result:
        cache = Cache()
        tracker = CacheTracker()
        try:
            if ranged:
                rows = await cached_call("get_activities_by_date", args["from"], args["to"], type_,
                                          cache=cache, ttl=_LIST_TTL, tracker=tracker, refresh=no_cache)
            else:
                async def fetch_page() -> list:
                    return await paginate("get_activities", page_size=min(limit + 1, 1000),
                                           max_items=limit + 1, **({"activitytype": type_} if type_ else {}))

                rows, hit, age = await cache.get_or_fetch(f"activities:{limit}:{type_}", fetch_page,
                                                           ttl=_LIST_TTL, refresh=no_cache)
                tracker.add(hit, age)
        finally:
            cache.close()
        rows = rows or []
        truncated = len(rows) > limit
        rows = [_flatten_type(a) for a in rows[:limit]]
        return _shaped_result(rows, tracker, default_fields=LIST_FIELDS, fields=f, bucket=b, raw=raw,
                               truncated=truncated)

    run(cmd, args, work)


def _by_id(name: str, method: str, help_: str, default_fields: tuple[str, ...] = (),
           transform: Callable[[Any], Any] | None = None, ttl: int = DETAIL_TTL) -> None:
    def command(activity_id: int = typer.Argument(..., metavar="ACTIVITY_ID"),
                fields: str = FIELDS_OPT, bucket: str = BUCKET_OPT, raw: bool = RAW_OPT,
                no_cache: bool = NO_CACHE_OPT) -> None:
        cmd = f"activity {name}"
        try:
            f, b, echo = shape_args(fields, bucket, raw)
        except PaceError as err:
            fail(cmd, err)

        async def work() -> Result:
            cache = Cache()
            tracker = CacheTracker()
            try:
                data = await cached_call(method, activity_id, cache=cache, ttl=ttl, tracker=tracker,
                                          refresh=no_cache)
            finally:
                cache.close()
            if transform is not None and not raw:
                data = transform(data)
            return _shaped_result(data, tracker, default_fields=default_fields, fields=f, bucket=b, raw=raw)

        run(cmd, {"activity_id": activity_id, **echo}, work)

    command.__name__ = name
    command.__doc__ = help_
    app.command(name, help=help_)(command)


_by_id("show", "get_activity", "One activity: summary metrics, plus rpe (1-10) and feel (label) when set.",
       transform=with_effort)
_by_id("gear", "get_activity_gear", "Gear linked to one activity.", default_fields=ACTIVITY_GEAR_FIELDS, ttl=GEAR_TTL)
_by_id("splits", "get_activity_splits", "Laps / splits of one activity.")
_by_id("weather", "get_activity_weather", "Weather during one activity.")


@app.command("last")
def last(fields: str = FIELDS_OPT, bucket: str = BUCKET_OPT, raw: bool = RAW_OPT,
          no_cache: bool = NO_CACHE_OPT) -> None:
    """Most recent activity."""
    try:
        f, b, echo = shape_args(fields, bucket, raw)
    except PaceError as err:
        fail("activity last", err)

    async def work() -> Result:
        cache = Cache()
        tracker = CacheTracker()
        try:
            data = await cached_call("get_last_activity", cache=cache, ttl=_LIST_TTL, tracker=tracker,
                                      refresh=no_cache)
        finally:
            cache.close()
        return _shaped_result(_flatten_type(data or {}), tracker, default_fields=LIST_FIELDS,
                               fields=f, bucket=b, raw=raw)

    run("activity last", echo, work)


def details_table(payload: dict, bucket: str, max_rows: int = MAX_ROWS) -> tuple[dict, str]:
    """Reshape Garmin per-sample metrics into one bucketed table. Returns (table, bucket_used)."""
    index = {
        d.get("key"): d.get("metricsIndex")
        for d in payload.get("metricDescriptors", [])
        if isinstance(d.get("key"), str) and isinstance(d.get("metricsIndex"), int)
    }
    if "directTimestamp" not in index:
        raise PaceError("not_found", "Activity has no per-sample metrics.")

    cols_present: list[tuple[str, str]] = []
    seen_cols: set[str] = set()
    for key, col in DETAIL_METRICS.items():
        if key in index and col not in seen_cols:
            cols_present.append((key, col))
            seen_cols.add(col)

    samples = []
    for row in payload.get("activityDetailMetrics", []):
        m = row.get("metrics") or []
        ts = m[index["directTimestamp"]] if index["directTimestamp"] < len(m) else None
        if not isinstance(ts, (int, float)):
            continue
        samples.append((int(ts), [m[index[k]] if index[k] < len(m) else None for k, _ in cols_present]))
    samples.sort(key=lambda s: s[0])
    span_s = (samples[-1][0] - samples[0][0]) // 1000 if len(samples) > 1 else 0
    used = choose_bucket(span_s, len(samples), max_rows) if bucket == "auto" else bucket
    offset = tz_offset_s(payload, samples[0][0] if samples else None)
    names = [c for _, c in cols_present]

    def fmt_exact(sec: int) -> str:
        return datetime.fromtimestamp(sec + offset, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    def fmt_minute(sec: int) -> str:
        return datetime.fromtimestamp(sec + offset, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M")

    if used == "none":
        rows = [[fmt_exact(ts // 1000), *vals] for ts, vals in samples]
        return {"cols": ["t", *names], "rows": rows}, "none"

    size = BUCKETS[used]
    groups: dict[int, list[list]] = {}
    for ts, vals in samples:
        groups.setdefault((ts // 1000 // size) * size, []).append(vals)
    rows = []
    for start in sorted(groups):
        agg = []
        for i, name in enumerate(names):
            col = [v[i] for v in groups[start] if isinstance(v[i], (int, float))]
            if not col:
                agg.append(None)
            elif name in _LAST_VALUE_COLS:
                agg.append(round(col[-1], 1))
            else:
                agg.append(round(sum(col) / len(col), 1))
        rows.append([fmt_minute(start), *agg])
    return {"cols": ["t", *names], "rows": rows}, used


@app.command("details")
def details(activity_id: int = typer.Argument(..., metavar="ACTIVITY_ID"),
            bucket: str = BUCKET_OPT, raw: bool = RAW_OPT, no_cache: bool = NO_CACHE_OPT) -> None:
    """Per-sample HR / speed / distance / elevation / cadence / power as one bucketed table."""
    cmd = "activity details"
    try:
        _, b, echo = shape_args("all", bucket, raw)
    except PaceError as err:
        fail(cmd, err)

    async def work() -> Result:
        cache = Cache()
        tracker = CacheTracker()
        try:
            payload = await cached_call("get_activity_details", activity_id, cache=cache, ttl=DETAIL_TTL,
                                         tracker=tracker, refresh=no_cache)
        finally:
            cache.close()
        if raw:
            return Result(payload, tracker.report(), {"raw": True})
        table, used = details_table(payload or {}, b)
        summary = prune({k: (payload or {}).get(k) for k in ("activityId", "measurementCount", "totalMetricsCount")})
        return Result({**summary, "table": table}, tracker.report(),
                       {"bucket_used": used, "reduced_series": ["table"] if used != "none" else [],
                        "fields": "all"})

    run(cmd, {"activity_id": activity_id, **echo}, work)


class ExportFormat(str, Enum):
    GPX = "gpx"
    TCX = "tcx"
    KML = "kml"
    ORIGINAL = "original"


_DL_FMT_MAP = {
    ExportFormat.GPX: Garmin.ActivityDownloadFormat.GPX,
    ExportFormat.TCX: Garmin.ActivityDownloadFormat.TCX,
    ExportFormat.KML: Garmin.ActivityDownloadFormat.KML,
    ExportFormat.ORIGINAL: Garmin.ActivityDownloadFormat.ORIGINAL,
}


def _ext_for(fmt: ExportFormat) -> str:
    return ".zip" if fmt is ExportFormat.ORIGINAL else f".{fmt.value}"


async def _download(activity_id: int, fmt: ExportFormat, target: Path) -> dict[str, Any]:
    ok, data, err = await safe_call("download_activity", str(activity_id), _DL_FMT_MAP[fmt])
    if not ok:
        assert err is not None
        raise PaceError.from_api(err)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"activityId": activity_id, "path": str(target), "bytes": len(data)}


@app.command("export")
def export(activity_id: int = typer.Argument(..., metavar="ACTIVITY_ID"),
           fmt: ExportFormat = typer.Option(ExportFormat.GPX, "--format", "-f"),
           out: Path | None = typer.Option(None, "--out", "-o", help="Default: <id>.<ext> in cwd.")) -> None:
    """Download one activity file (gpx, tcx, kml, original zip)."""
    target = out or Path(f"{activity_id}{_ext_for(fmt)}")

    async def work() -> Result:
        return Result({"files": [await _download(activity_id, fmt, target)]})

    run("activity export", {"activity_id": activity_id, "format": fmt.value, "out": str(target)}, work)


@app.command("export-range")
def export_range(from_: str = typer.Option(..., "--from", "-F"), to: str = TO_OPT,
                  fmt: ExportFormat = typer.Option(ExportFormat.GPX, "--format", "-f"),
                  out_dir: Path = typer.Option(Path("./activities"), "--out-dir", "-d"),
                  type_: str | None = typer.Option(None, "--type", "-t")) -> None:
    """Download every activity in a date range into --out-dir."""
    cmd = "activity export-range"
    try:
        start, end = resolve_range(from_, to, 1)
    except PaceError as err:
        fail(cmd, err)
    args = {"from": start.isoformat(), "to": end.isoformat(), "format": fmt.value, "out_dir": str(out_dir),
            "type": type_}

    async def work() -> Result:
        ok, listing, err = await safe_call("get_activities_by_date", args["from"], args["to"], type_)
        if not ok:
            assert err is not None
            raise PaceError.from_api(err)
        files = await asyncio.gather(*(
            _download(a["activityId"], fmt, out_dir / f"{a['activityId']}{_ext_for(fmt)}") for a in listing or []))
        return Result({"files": list(files)})

    run(cmd, args, work)


@app.command("effort")
def effort(activity_id: int = typer.Argument(..., metavar="ACTIVITY_ID"),
           rpe: int | None = typer.Option(None, "--rpe", help="Perceived exertion 1-10."),
           feel: str | None = typer.Option(None, "--feel", help="very-weak | weak | normal | strong | very-strong"),
           dry_run: bool = DRY_RUN_OPT) -> None:
    """Set perceived exertion and/or feel on one activity; only the given field(s) are sent."""
    cmd = "activity effort"
    args = {"activity_id": activity_id, "rpe": rpe, "feel": feel, "dry_run": dry_run}
    label: str | None = None
    try:
        if activity_id <= 0:
            raise PaceError("bad_args", f"ACTIVITY_ID must be positive: {activity_id}")
        if rpe is None and feel is None:
            raise PaceError("bad_args", "Give --rpe and/or --feel.")
        if rpe is not None:
            parse_rpe(rpe)
        if feel is not None:
            label, _ = parse_feel(feel)
    except PaceError as err:
        fail(cmd, err)
    spec = effort_spec(activity_id, rpe, label)
    run(cmd, args, lambda: run_write(spec, dry_run=dry_run))
