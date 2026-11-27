"""Health logs: weight (add/list/delete), hydration, blood pressure (add/list)."""

from typing import Any

import typer

from pace.api import safe_call
from pace.cache import Cache, CacheTracker
from pace.commands._helpers import cached_call
from pace.dates import parse_date, resolve_range
from pace.envelope import PaceError, Result, fail, run
from pace.factory import BUCKET_OPT, DAYS_OPT, FIELDS_OPT, FROM_OPT, NO_CACHE_OPT, RAW_OPT, TO_OPT, shape_args
from pace.shape import apply
from pace.writes import DRY_RUN_OPT, WriteSpec, run_write
from pace.writespecs import manual_weigh_in_ids, weigh_in_spec

app = typer.Typer(no_args_is_help=True, help="Health logs written to Garmin Connect: weight, hydration, blood pressure.")
weight_app = typer.Typer(no_args_is_help=True, help="Weigh-ins and body composition.")
bp_app = typer.Typer(no_args_is_help=True, help="Blood pressure readings.")
app.add_typer(weight_app, name="weight")
app.add_typer(bp_app, name="bp")

DATE_OPT = typer.Option("today", "--date", "-d", help="YYYY-MM-DD | today | yesterday | -Nd")
_LIST_TTL = 600


def _check(name: str, value: float, lo: float, hi: float, inclusive: bool = False) -> None:
    ok = lo <= value <= hi if inclusive else lo < value < hi
    if not ok:
        raise PaceError("bad_args", f"{name} out of range ({lo}-{hi}): {value}")


def _noon(date_str: str) -> tuple[str, str]:
    d = parse_date(date_str).isoformat()
    return d, f"{d}T12:00:00"


async def _resolve_weight_id(d: str, kg: float) -> Any:
    """`add_weigh_in` / `add_body_composition` responses carry no samplePk, so look the new
    entry up in the day view.

    Only entries that match the weight just written (`manual_weigh_in_ids`: within 100 g, manual
    source) are candidates: a scale sync or an earlier weigh-in on the same day must not hand back
    its id. Of those the greatest samplePk is the newest, i.e. ours. Never raises -- a failed,
    empty or ambiguous lookup leaves the id null, which the caller can still resolve with
    `log weight list`."""
    try:
        ok, data, err = await safe_call("get_daily_weigh_ins", d)
    except Exception:  # noqa: BLE001 -- id resolution is best-effort, never fatal
        return None
    if not ok or err is not None:
        return None
    pks = manual_weigh_in_ids(data, kg)
    return max(pks) if pks else None


def _ranged_read(cmd: str, method: str, default_fields: tuple[str, ...], from_: str | None, to: str, days: int,
                 fields: str, bucket: str, raw: bool, no_cache: bool) -> None:
    try:
        start, end = resolve_range(from_, to, days)
        f, b, echo = shape_args(fields, bucket, raw)
    except PaceError as err:
        fail(cmd, err)
    s, e = start.isoformat(), end.isoformat()

    async def work() -> Result:
        cache = Cache()
        tracker = CacheTracker()
        try:
            data = await cached_call(method, s, e, cache=cache, ttl=_LIST_TTL, tracker=tracker, refresh=no_cache)
        finally:
            cache.close()
        shaped, report = apply(data or {}, series=(), default_fields=default_fields, fields=f, bucket=b, raw=raw, span_s=0)
        return Result(shaped, tracker.report(), report)

    run(cmd, {"from": s, "to": e, **echo}, work)


@weight_app.command("add")
def weight_add(kg: float = typer.Argument(..., metavar="KG"), date: str = DATE_OPT,
               fat: float | None = typer.Option(None, "--fat", help="Body fat %"),
               water: float | None = typer.Option(None, "--water", help="Body water %"),
               muscle: float | None = typer.Option(None, "--muscle", help="Muscle mass kg"),
               bone: float | None = typer.Option(None, "--bone", help="Bone mass kg"),
               bmi: float | None = typer.Option(None, "--bmi"),
               dry_run: bool = DRY_RUN_OPT) -> None:
    """Record a weigh-in (plus optional body composition)."""
    cmd = "log weight add"
    try:
        _check("KG", kg, 20, 400)
        for name, v in (("fat", fat), ("water", water)):
            if v is not None:
                _check(name, v, 0, 100, inclusive=True)
        for name, v, lo, hi in (("muscle", muscle, 0, 200), ("bone", bone, 0, 20), ("bmi", bmi, 5, 100)):
            if v is not None:
                _check(name, v, lo, hi)
        d = parse_date(date).isoformat()
    except PaceError as err:
        fail(cmd, err)
    extras = {"percent_fat": fat, "percent_hydration": water, "muscle_mass": muscle, "bone_mass": bone, "bmi": bmi}
    extras = {k: float(v) for k, v in extras.items() if v is not None}
    echo: dict[str, Any] = {"weight_kg": kg, "date": d}
    echo.update({"fat_pct": fat, "water_pct": water, "muscle_kg": muscle, "bone_kg": bone, "bmi": bmi})
    echo = {k: v for k, v in echo.items() if v is not None}
    spec = weigh_in_spec(kg, d, extras, echo)

    async def work() -> Result:
        result = await run_write(spec, dry_run=dry_run)
        if not dry_run:
            result.data["id"] = result.data["id"] or await _resolve_weight_id(d, kg)
        return result

    run(cmd, {**echo, "dry_run": dry_run}, work)


@weight_app.command("list")
def weight_list(from_: str | None = FROM_OPT, to: str = TO_OPT, days: int = DAYS_OPT, fields: str = FIELDS_OPT,
                bucket: str = BUCKET_OPT, raw: bool = RAW_OPT, no_cache: bool = NO_CACHE_OPT) -> None:
    """Weigh-ins in a date range (one Garmin call)."""
    _ranged_read("log weight list", "get_weigh_ins", ("dailyWeightSummaries", "totalAverage"),
                 from_, to, days, fields, bucket, raw, no_cache)


@weight_app.command("delete")
def weight_delete(sample_pk: str = typer.Argument(
                      ..., metavar="SAMPLE_PK",
                      help="samplePk from `log weight list` (dailyWeightSummaries[].allWeightMetrics[].samplePk)."),
                  date: str = typer.Option(..., "--date", "-d", help="Date of the weigh-in."),
                  dry_run: bool = DRY_RUN_OPT) -> None:
    """Delete one weigh-in."""
    cmd = "log weight delete"
    try:
        d = parse_date(date).isoformat()
    except PaceError as err:
        fail(cmd, err)
    kwargs = {"weight_pk": sample_pk, "cdate": d}
    spec = WriteSpec(resource="weight", action="delete", method="delete_weigh_in", kwargs=kwargs,
                     payload={"method": "delete_weigh_in", **kwargs}, echo={"sample_pk": sample_pk, "date": d},
                     invalidate=("get_weigh_ins:", f"get_daily_weigh_ins:{d}"))
    run(cmd, {"sample_pk": sample_pk, "date": d, "dry_run": dry_run}, lambda: run_write(spec, dry_run=dry_run))


@app.command("hydration")
def hydration(ml: float = typer.Argument(..., metavar="ML", help="Millilitres; negative subtracts."),
              date: str = DATE_OPT, dry_run: bool = DRY_RUN_OPT) -> None:
    """Add water intake for a day."""
    cmd = "log hydration"
    try:
        if ml == 0:
            raise PaceError("bad_args", "ML must be non-zero")
        _check("ML", ml, -5000, 5000, inclusive=True)
        d = parse_date(date).isoformat()
    except PaceError as err:
        fail(cmd, err)
    kwargs = {"value_in_ml": float(ml), "cdate": d}
    spec = WriteSpec(resource="hydration", action="create", method="add_hydration_data", kwargs=kwargs,
                     payload={"method": "add_hydration_data", **kwargs}, echo={"ml": ml, "date": d},
                     invalidate=(f"get_hydration_data:{d}",))
    run(cmd, {"ml": ml, "date": d, "dry_run": dry_run}, lambda: run_write(spec, dry_run=dry_run))


@bp_app.command("add")
def bp_add(systolic: int = typer.Argument(..., metavar="SYS"), diastolic: int = typer.Argument(..., metavar="DIA"),
           pulse: int = typer.Argument(..., metavar="PULSE"), date: str = DATE_OPT,
           notes: str | None = typer.Option(None, "--notes"), dry_run: bool = DRY_RUN_OPT) -> None:
    """Record a blood pressure reading."""
    cmd = "log bp add"
    try:
        # Bounds are garminconnect's own (`set_blood_pressure` raises ValueError
        # outside them), so a --dry-run that passes here also passes live.
        _check("SYS", systolic, 70, 260, inclusive=True)
        _check("DIA", diastolic, 40, 150, inclusive=True)
        _check("PULSE", pulse, 20, 250, inclusive=True)
        d, ts = _noon(date)
    except PaceError as err:
        fail(cmd, err)
    kwargs = {"timestamp": ts, "notes": notes or ""}
    echo = {"systolic": systolic, "diastolic": diastolic, "pulse": pulse, "date": d, "notes": notes}
    echo = {k: v for k, v in echo.items() if v is not None}
    spec = WriteSpec(resource="bp", action="create", method="set_blood_pressure", args=(systolic, diastolic, pulse),
                     kwargs=kwargs, payload={"method": "set_blood_pressure", "args": [systolic, diastolic, pulse], **kwargs},
                     echo=echo, invalidate=("get_blood_pressure:",),
                     id_of=lambda r: r.get("version") if isinstance(r, dict) else None)
    run(cmd, {**echo, "dry_run": dry_run}, lambda: run_write(spec, dry_run=dry_run))


@bp_app.command("delete")
def bp_delete(version: str = typer.Argument(..., metavar="VERSION",
                                            help="measurementSummaries[].measurements[].version from `log bp list`."),
              date: str = typer.Option(..., "--date", "-d", help="Date of the reading."),
              dry_run: bool = DRY_RUN_OPT) -> None:
    """Delete one blood pressure reading."""
    cmd = "log bp delete"
    try:
        d = parse_date(date).isoformat()
    except PaceError as err:
        fail(cmd, err)
    kwargs = {"version": version, "cdate": d}
    spec = WriteSpec(resource="bp", action="delete", method="delete_blood_pressure", kwargs=kwargs,
                     payload={"method": "delete_blood_pressure", **kwargs}, echo={"version": version, "date": d},
                     invalidate=("get_blood_pressure:",))
    run(cmd, {"version": version, "date": d, "dry_run": dry_run}, lambda: run_write(spec, dry_run=dry_run))


@bp_app.command("list")
def bp_list(from_: str | None = FROM_OPT, to: str = TO_OPT, days: int = DAYS_OPT, fields: str = FIELDS_OPT,
            bucket: str = BUCKET_OPT, raw: bool = RAW_OPT, no_cache: bool = NO_CACHE_OPT) -> None:
    """Blood pressure readings in a date range (one Garmin call)."""
    _ranged_read("log bp list", "get_blood_pressure", ("measurementSummaries",),
                 from_, to, days, fields, bucket, raw, no_cache)
