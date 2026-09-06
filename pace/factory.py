"""Build Typer commands from EndpointSpec entries."""

import asyncio
import inspect
from datetime import date
from typing import Any

import typer

from pace.cache import Cache, CacheTracker, ttl_for_day
from pace.commands._helpers import cached_call
from pace.config import load_config
from pace.dates import date_range, resolve_range
from pace.envelope import PaceError, Result, fail, run
from pace.shape import apply, parse_bucket, parse_fields
from pace.spec import GROUPS, TTL_STATIC, EndpointSpec, specs_for

MAX_DAYS = 366
DAY_S = 86400

FIELDS_OPT = typer.Option("default", "--fields", help="default | all | comma-separated dotted paths.")
BUCKET_OPT = typer.Option("auto", "--bucket", help="Time-series resolution: auto | none | 1m 5m 15m 1h 3h 1d.")
RAW_OPT = typer.Option(False, "--raw", help="Garmin JSON verbatim (no pruning, fields, bucketing).")
NO_CACHE_OPT = typer.Option(False, "--no-cache", help="Bypass the cache read for this call (still writes the fresh result).")
FROM_OPT = typer.Option(None, "--from", "-F", help="Start date (YYYY-MM-DD | today | yesterday | -Nd). Overrides --days.")
TO_OPT = typer.Option("today", "--to", "-T", help="End date (inclusive).")
DAYS_OPT = typer.Option(1, "--days", "-n", help="Number of days ending at --to.")


def shape_args(fields: str, bucket: str, raw: bool) -> tuple[str | list[str], str, dict[str, Any]]:
    f = parse_fields(fields)
    b = parse_bucket(bucket)
    return f, b, {"fields": f, "bucket": b, "raw": raw}


def today_ttl(spec: EndpointSpec) -> int:
    return load_config().cache_ttl_overrides.get(f"{spec.group}.{spec.name}", spec.ttl)


async def resolve_upn(cache: Cache | None, tracker: CacheTracker, refresh: bool = False) -> str:
    # Same cache key as `device last-used`, so it must use that command's TTL: a longer one
    # here would poison the entry and freeze `device last-used` for a week.
    data = await cached_call("get_device_last_used", cache=cache, ttl=TTL_STATIC, tracker=tracker, refresh=refresh)
    upn = (data or {}).get("userProfileNumber") if isinstance(data, dict) else None
    if upn is None:
        raise PaceError("internal", "Cannot resolve userProfileNumber from the last-used device.")
    return str(upn)


def _unwrap(spec: EndpointSpec, data: Any) -> Any:
    if spec.unwrap == "first" and isinstance(data, list):
        return data[0] if data else {}
    return data


def _shape_kwargs(spec: EndpointSpec, fields, bucket, raw, span_s: int = DAY_S) -> dict[str, Any]:
    return dict(series=spec.series, default_fields=spec.default_fields, fields=fields,
                bucket=bucket, raw=raw, span_s=span_s)


async def fetch_spec(spec: EndpointSpec, *, ident: Any = None, dates: list[date] | None = None,
                     no_cache: bool = False, fields: str | list[str] = "default",
                     bucket: str = "auto", raw: bool = False) -> Result:
    # `no_cache` means "refresh": always open the cache so a fresh result is still written,
    # just skip the read on this call. It never leaves the DB unopened.
    cache = Cache()
    tracker = CacheTracker()
    try:
        prefix: list[Any] = []
        if spec.resolver == "upn":
            prefix.append(await resolve_upn(cache, tracker, refresh=no_cache))
        if spec.id_arg is not None:
            prefix.append(ident)
        ttl = today_ttl(spec)

        if spec.dated is None:
            data = _unwrap(spec, await cached_call(spec.method, *prefix, cache=cache, ttl=ttl,
                                                    tracker=tracker, refresh=no_cache))
            shaped, report = apply(data, **_shape_kwargs(spec, fields, bucket, raw))
            return Result(shaped, tracker.report(), report)

        if dates is None:
            raise PaceError("internal", "dated spec called without dates")

        async def one(d: date) -> tuple[date, Any, PaceError | None]:
            iso = d.isoformat()
            dargs = (iso,) if spec.dated == "single" else (iso, iso)
            try:
                payload = await cached_call(spec.method, *prefix, *dargs, cache=cache,
                                            ttl=ttl_for_day(d, ttl), tracker=tracker, refresh=no_cache)
            except PaceError as err:
                return d, None, err
            return d, _unwrap(spec, payload), None

        results = await asyncio.gather(*(one(d) for d in dates))
    finally:
        cache.close()

    errors = [e for _, _, e in results if e is not None]
    if errors and len(errors) == len(results):
        raise errors[0]

    good_positions = [i for i, (_, _, e) in enumerate(results) if e is None]
    good = [results[i][1] if isinstance(results[i][1], dict) else {"value": results[i][1]} for i in good_positions]
    # `auto` is scored against the whole requested range, not one day, so a week of days
    # gets one coarse grid instead of seven fine ones.
    shaped_good, report = apply(good, **_shape_kwargs(spec, fields, bucket, raw, len(dates) * DAY_S))
    shaped_by_pos = dict(zip(good_positions, shaped_good, strict=True))
    items = []
    for i, (d, _, err) in enumerate(results):
        if err is not None:
            items.append({"date": d.isoformat(), "error": {"code": err.code, "message": err.message}})
        else:
            # Spread the payload first so a "date" field it may carry never overrides
            # the actually-requested day.
            items.append({**shaped_by_pos[i], "date": d.isoformat()})
    return Result(items, tracker.report(), report)


def build_command(app: typer.Typer, spec: EndpointSpec) -> None:
    P = inspect.Parameter
    params: list[inspect.Parameter] = []
    if spec.id_arg is not None:
        params.append(P("ident", P.POSITIONAL_OR_KEYWORD, annotation=spec.id_type,
                        default=typer.Argument(..., metavar=spec.id_arg)))
    if spec.dated is not None:
        params += [
            P("from_", P.KEYWORD_ONLY, annotation=str | None, default=FROM_OPT),
            P("to", P.KEYWORD_ONLY, annotation=str, default=TO_OPT),
            P("days", P.KEYWORD_ONLY, annotation=int, default=DAYS_OPT),
        ]
    params += [
        P("fields", P.KEYWORD_ONLY, annotation=str, default=FIELDS_OPT),
        P("bucket", P.KEYWORD_ONLY, annotation=str, default=BUCKET_OPT),
        P("raw", P.KEYWORD_ONLY, annotation=bool, default=RAW_OPT),
        P("no_cache", P.KEYWORD_ONLY, annotation=bool, default=NO_CACHE_OPT),
    ]

    def command(**kw: Any) -> None:
        args: dict[str, Any] = {}
        dates: list[date] | None = None
        try:
            if spec.id_arg is not None:
                args[spec.id_arg.lower()] = kw["ident"]
            if spec.dated is not None:
                start, end = resolve_range(kw["from_"], kw["to"], kw["days"])
                dates = list(date_range(start, end))
                if len(dates) > MAX_DAYS:
                    raise PaceError("bad_args", f"Range too long ({len(dates)} days); max {MAX_DAYS}.")
                args.update(**{"from": start.isoformat(), "to": end.isoformat()})
            fields, bucket, shape_echo = shape_args(kw["fields"], kw["bucket"], kw["raw"])
        except PaceError as err:
            fail(spec.command, err)
        args.update(shape_echo)
        run(spec.command, args, lambda: fetch_spec(
            spec, ident=kw.get("ident"), dates=dates, no_cache=kw["no_cache"],
            fields=fields, bucket=bucket, raw=kw["raw"]))

    command.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    command.__name__ = spec.name.replace("-", "_")
    command.__doc__ = spec.help
    app.command(spec.name, help=spec.help)(command)


def build_group(group: str) -> typer.Typer:
    app = typer.Typer(no_args_is_help=True, help=GROUPS[group])
    for spec in specs_for(group):
        build_command(app, spec)
    return app
