"""Activity chart: pace (running) or speed (other sports), heart rate and elevation over distance."""

from datetime import date
from pathlib import Path
from statistics import fmean
from typing import Any

from pace.charts import style
from pace.commands._helpers import cached_call
from pace.commands.activity import details_table
from pace.sports import family

MIN_SPEED_MPS = 0.5  # slower samples are standing still: no pace for them
MAX_POINTS = 300  # the finest bucket that keeps a run under this many points (a line, not a table)


async def fetch(start: date, end: date, *, today: date, cache: Any, tracker: Any, refresh: bool,
                activity_id: int, **_: Any) -> dict:
    """The per-sample details plus the activity itself (its type picks pace or speed)."""
    payload = await cached_call("get_activity_details", activity_id, cache=cache, ttl=86400, tracker=tracker,
                                refresh=refresh)
    info = await cached_call("get_activity", activity_id, cache=cache, ttl=86400, tracker=tracker, refresh=refresh)
    info = info if isinstance(info, dict) else {}
    kind = info.get("activityTypeDTO") or info.get("activityType")
    type_key = kind.get("typeKey") if isinstance(kind, dict) else kind
    day = ((info.get("summaryDTO") or {}).get("startTimeLocal") or "")[:10]
    when = date.fromisoformat(day) if len(day) == 10 else today
    return {"details": payload or {}, "info": info, "family": family(type_key), "id": activity_id,
            "range": {"from": when.isoformat(), "to": when.isoformat()}}


def _table(data: dict) -> tuple[dict, str]:
    """(details table, bucket used): the finest bucket giving at most MAX_POINTS rows, or raw samples."""
    return details_table(data["details"], "auto", MAX_POINTS)


def _series(data: dict) -> dict[str, list[float | None]]:
    table, _ = _table(data)
    cols = table["cols"]
    rows = table["rows"]

    def col(name: str) -> list[float | None]:
        return [r[cols.index(name)] for r in rows] if name in cols else [None] * len(rows)

    return {"dist_m": col("dist_m"), "speed": col("speed_mps"), "hr": col("hr"), "elev": col("elev_m")}


def _summary(data: dict, s: dict[str, list[float | None]]) -> dict:
    dists = [d for d in s["dist_m"] if d is not None]
    hrs = [h for h in s["hr"] if h is not None]
    sm = data["info"].get("summaryDTO") or {}
    distance = sm.get("distance") if sm.get("distance") is not None else (max(dists) if dists else None)
    moving = sm.get("movingDuration") or sm.get("duration")
    speed = sm.get("averageSpeed") or (distance / moving if distance and moving else None)
    running = data["family"] == "running"
    out: dict[str, Any] = {"activity_id": data["id"], "family": data["family"],
                           "distance_km": round(distance / 1000, 2) if distance is not None else None,
                           "moving_time_s": round(moving) if moving else None}
    if running:
        out["avg_pace_min_km"] = round(1000 / speed / 60, 2) if speed and speed >= MIN_SPEED_MPS else None
    else:
        out["avg_speed_kmh"] = round(speed * 3.6, 1) if speed else None
    avg_hr = sm.get("averageHR") if sm.get("averageHR") is not None else (fmean(hrs) if hrs else None)
    out["avg_hr"] = round(avg_hr) if avg_hr is not None else None
    gain = sm.get("elevationGain")
    out["elevation_gain_m"] = round(gain) if gain is not None else None
    out["empty"] = not dists
    return out


def _pace_label(value: float, _pos: int) -> str:
    minutes, seconds = divmod(round(value * 60), 60)
    return f"{minutes}:{seconds:02d}"


def render(data: dict, out: Path, **_: Any) -> dict:
    """data = {details: get_activity_details payload, info: get_activity payload, family, id, range}."""
    from matplotlib.ticker import FuncFormatter

    s = _series(data)
    summary = _summary(data, s)
    fig, ax = style.figure()
    running = data["family"] == "running"
    name = data["info"].get("activityName") or "Activity"
    heading = f"{name} ({data['range']['from']})"
    if summary["empty"]:
        ax.set_title(heading)
        ax.set_yticks([])
        style.empty_note(ax)
        style.save(fig, out)
        return summary

    fig.clear()
    top, bottom = fig.subplots(2, 1, sharex=True, height_ratios=[3, 1])
    top.set_title(f"{heading}: {'pace' if running else 'speed'}, heart rate, elevation")
    pts = [(d / 1000, v, h, e) for d, v, h, e in zip(s["dist_m"], s["speed"], s["hr"], s["elev"], strict=True)
           if d is not None]
    handles = []
    if running:
        main = [(d, 1000 / v / 60) for d, v, _h, _e in pts if v is not None and v >= MIN_SPEED_MPS]
        label, ylabel = "pace", "min/km (faster is up)"
    else:
        main = [(d, v * 3.6) for d, v, _h, _e in pts if v is not None]
        label, ylabel = "speed", "km/h"
    if main:
        handles += top.plot(*zip(*main, strict=True), color=style.COLORS["actual"], label=label)
    if running:
        top.invert_yaxis()
        top.yaxis.set_major_formatter(FuncFormatter(_pace_label))
    top.set_ylabel(ylabel)
    hr = [(d, h) for d, _v, h, _e in pts if h is not None]
    if hr:
        ax2 = top.twinx()
        ax2.grid(False)
        ax2.spines[["top", "left", "right"]].set_visible(False)
        ax2.tick_params(axis="y", right=False, colors=style.COLORS["line"])
        handles += ax2.plot(*zip(*hr, strict=True), color=style.COLORS["line"], label="heart rate", linewidth=1.5)
        ax2.set_ylabel("bpm", color=style.COLORS["line"])
    elev = [(d, e) for d, _v, _h, e in pts if e is not None]
    if elev:
        xs, ys = zip(*elev, strict=True)
        bottom.fill_between(xs, ys, min(ys), color=style.COLORS["rest"], label="elevation")
        bottom.set_ylim(min(ys), max(ys) + max((max(ys) - min(ys)) * 0.1, 1))
    bottom.set_ylabel("elevation (m)")
    bottom.set_xlabel("distance (km)")
    bottom.set_xlim(0, max(d for d, *_ in pts))
    fig.legend(handles=handles, loc="outside upper right", ncols=len(handles))
    style.save(fig, out)
    return summary
