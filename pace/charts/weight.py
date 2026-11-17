"""Weight chart: one dot per weigh-in and its 7-day trailing calendar mean."""

from datetime import date, timedelta
from pathlib import Path
from statistics import fmean
from typing import Any

from pace.charts import style
from pace.charts.sources import weigh_ins

LEAD_DAYS = 6  # rows before the first shown day, so its 7-day mean is complete


async def fetch(start: date, end: date, *, today: date, cache: Any, tracker: Any, refresh: bool, **_: Any) -> dict:
    rows = await weigh_ins(start - timedelta(days=LEAD_DAYS), end, today=today, cache=cache, tracker=tracker,
                           refresh=refresh)
    return {"rows": rows, "range": {"from": start.isoformat(), "to": end.isoformat()}, "today": today.isoformat()}


def _mean(rows: list[dict], day: date) -> float:
    """Mean of the weigh-ins within day-6 .. day (the row of `day` itself is always among them)."""
    lo = (day - timedelta(days=6)).isoformat()
    return round(fmean(r["kg"] for r in rows if lo <= r["date"] <= day.isoformat()), 2)


def _summary(data: dict) -> dict:
    start, end = data["range"]["from"], data["range"]["to"]
    shown = [r for r in data["rows"] if start <= r["date"] <= end]
    points = [{"date": r["date"], "kg": round(r["kg"], 2), "kg_7d": _mean(data["rows"], date.fromisoformat(r["date"]))}
              for r in shown]
    return {"points": points, "first": points[0]["kg"] if points else None,
            "last": points[-1]["kg"] if points else None,
            "mean": round(fmean(p["kg"] for p in points), 2) if points else None, "count": len(points),
            "empty": not points}


def render(data: dict, out: Path, **_: Any) -> dict:
    """data = {rows: weigh_ins rows (may start before the range), range, today}."""
    summary = _summary(data)
    start = date.fromisoformat(data["range"]["from"])
    end = date.fromisoformat(data["range"]["to"])
    fig, ax = style.figure()
    ax.set_title("Weight (kg)")
    ax.set_xlim(start, end + timedelta(days=1))
    style.date_axis(ax, start, end)
    if summary["empty"]:
        ax.set_yticks([])
        style.empty_note(ax)
        style.save(fig, out)
        return summary

    pts = summary["points"]
    xs = [style.noon(date.fromisoformat(p["date"])) for p in pts]
    kgs = [p["kg"] for p in pts]
    handles = [ax.scatter(xs, kgs, s=28, color=style.COLORS["actual"], label="weigh-in", zorder=3)]
    handles += ax.plot(xs, [p["kg_7d"] for p in pts], color=style.COLORS["line"], label="7-day mean")
    pad = max((max(kgs) - min(kgs)) * 0.25, 0.5)
    ax.set_ylim(min(kgs) - pad, max(kgs) + pad)
    ax.set_ylabel("kg")
    fig.legend(handles=handles, loc="outside upper right", ncols=len(handles))
    style.save(fig, out)
    return summary
