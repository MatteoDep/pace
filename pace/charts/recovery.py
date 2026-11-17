"""Recovery chart: daily resting heart rate with its 7-day trailing mean; HRV on a second axis when recorded."""

from datetime import date, timedelta
from pathlib import Path
from statistics import fmean
from typing import Any

from pace.charts import style
from pace.charts.sources import sleep_stats

LEAD_DAYS = 6  # rows before the first shown day, so its 7-day mean is complete


async def fetch(start: date, end: date, *, today: date, cache: Any, tracker: Any, refresh: bool, **_: Any) -> dict:
    rows = await sleep_stats(start - timedelta(days=LEAD_DAYS), end, today=today, cache=cache, tracker=tracker,
                             refresh=refresh)
    return {"rows": rows, "range": {"from": start.isoformat(), "to": end.isoformat()}, "today": today.isoformat()}


def trailing_mean(rows: list[dict], key: str, day: date) -> float | None:
    """Mean of the non-null `key` values over the 7 calendar days ending at `day`; None when there are none."""
    lo = (day - timedelta(days=6)).isoformat()
    values = [r[key] for r in rows if r[key] is not None and lo <= r["date"] <= day.isoformat()]
    return round(fmean(values), 2) if values else None


def _summary(data: dict) -> dict:
    start, end = data["range"]["from"], data["range"]["to"]
    shown = [r for r in data["rows"] if start <= r["date"] <= end]
    hrv_available = any(r["hrv"] is not None or r["hrv_7d"] is not None for r in shown)
    days = []
    for r in shown:
        row: dict[str, Any] = {"date": r["date"], "rhr": r["rhr"],
                               "rhr_7d": trailing_mean(data["rows"], "rhr", date.fromisoformat(r["date"]))}
        if hrv_available:
            row.update(hrv=r["hrv"], hrv_7d=r["hrv_7d"])
        days.append(row)
    empty = not any(r["rhr"] is not None for r in shown) and not hrv_available
    return {"days": days, "rhr_7d_latest": next((d["rhr_7d"] for d in reversed(days) if d["rhr_7d"] is not None), None),
            "hrv_available": hrv_available, "empty": empty}


def render(data: dict, out: Path, **_: Any) -> dict:
    """data = {rows: sleep_stats rows (may start before the range), range, today}."""
    summary = _summary(data)
    start = date.fromisoformat(data["range"]["from"])
    end = date.fromisoformat(data["range"]["to"])
    fig, ax = style.figure()
    ax.set_title("Recovery: resting HR and HRV" if summary["hrv_available"] else "Recovery: resting heart rate")
    ax.set_xlim(start, end + timedelta(days=1))
    style.date_axis(ax, start, end)
    if summary["empty"]:
        ax.set_yticks([])
        style.empty_note(ax)
        style.save(fig, out)
        return summary

    days = summary["days"]
    rhr = [(style.noon(date.fromisoformat(d["date"])), d["rhr"]) for d in days if d["rhr"] is not None]
    mean = [(style.noon(date.fromisoformat(d["date"])), d["rhr_7d"]) for d in days if d["rhr_7d"] is not None]
    handles = []
    if rhr:
        xs, ys = zip(*rhr, strict=True)
        handles.append(ax.scatter(xs, ys, s=28, color=style.COLORS["actual"], label="resting HR", zorder=3))
        lo, hi = min(ys), max(ys)
        pad = max((hi - lo) * 0.25, 2)
        ax.set_ylim(lo - pad, hi + pad)
    if mean:
        handles += ax.plot(*zip(*mean, strict=True), color=style.COLORS["line"], label="7-day mean")
    ax.set_ylabel("bpm")
    if summary["hrv_available"]:
        ax2 = ax.twinx()
        ax2.grid(False)
        ax2.spines[["top", "left"]].set_visible(False)
        ax2.spines["right"].set_visible(False)
        ax2.tick_params(axis="y", left=False, right=False, colors=style.COLORS["hrv"])
        for key, label, kw in (("hrv", "HRV", {"marker": "o", "markersize": 4, "linewidth": 1}),
                               ("hrv_7d", "HRV 7-day", {"linestyle": (0, (3, 2))})):
            pts = [(style.noon(date.fromisoformat(d["date"])), d[key]) for d in days if d.get(key) is not None]
            if pts:
                handles += ax2.plot(*zip(*pts, strict=True), color=style.COLORS["hrv"], label=label, **kw)
        ax2.set_ylabel("HRV (ms)", color=style.COLORS["hrv"])
    fig.legend(handles=handles, loc="outside upper right", ncols=len(handles))
    style.save(fig, out)
    return summary
