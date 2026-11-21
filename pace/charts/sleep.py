"""Sleep chart: stacked deep, light, REM and awake hours per night; the sleep score as a line when recorded."""

from datetime import date, timedelta
from pathlib import Path
from statistics import fmean
from typing import Any

from pace.charts import style
from pace.charts.sources import sleep_stats

STAGES = (("deep_s", "deep"), ("light_s", "light"), ("rem_s", "REM"), ("awake_s", "awake"))
_COLOR = {"deep_s": "deep", "light_s": "light", "rem_s": "rem", "awake_s": "awake"}


async def fetch(start: date, end: date, *, today: date, cache: Any, tracker: Any, refresh: bool, **_: Any) -> dict:
    rows = await sleep_stats(start, end, today=today, cache=cache, tracker=tracker, refresh=refresh)
    return {"rows": rows, "range": {"from": start.isoformat(), "to": end.isoformat()}, "today": today.isoformat()}


def _h(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds / 3600, 2)


def _summary(data: dict) -> dict:
    start, end = data["range"]["from"], data["range"]["to"]
    shown = [r for r in data["rows"] if start <= r["date"] <= end]
    nights = [{"date": r["date"], "sleep_h": _h(r["sleep_s"]), "deep_h": _h(r["deep_s"]), "light_h": _h(r["light_s"]),
               "rem_h": _h(r["rem_s"]), "awake_h": _h(r["awake_s"]), "score": r["score"]} for r in shown]
    if shown:
        lo = (date.fromisoformat(shown[-1]["date"]) - timedelta(days=6)).isoformat()
        recent = [r["sleep_s"] for r in shown if r["sleep_s"] is not None and r["date"] >= lo]
    else:
        recent = []
    return {"nights": nights, "sleep_7d_mean_h": _h(fmean(recent)) if recent else None,
            "score_available": any(n["score"] is not None for n in nights),
            "empty": not any(r["sleep_s"] is not None for r in shown)}


def render(data: dict, out: Path, **_: Any) -> dict:
    """data = {rows: sleep_stats rows, range, today}."""
    summary = _summary(data)
    start = date.fromisoformat(data["range"]["from"])
    end = date.fromisoformat(data["range"]["to"])
    fig, ax = style.figure()
    ax.set_title("Sleep per night (hours)")
    ax.set_xlim(start, end + timedelta(days=1))
    style.date_axis(ax, start, end)
    if summary["empty"]:
        ax.set_yticks([])
        style.empty_note(ax)
        style.save(fig, out)
        return summary

    rows = [r for r in data["rows"] if data["range"]["from"] <= r["date"] <= data["range"]["to"]
            and r["sleep_s"] is not None]
    xs = [style.noon(date.fromisoformat(r["date"])) for r in rows]
    width = timedelta(days=0.8)
    bottoms = [0.0] * len(rows)
    handles = []
    for key, label in STAGES:
        heights = [(r[key] or 0) / 3600 for r in rows]
        handles.append(ax.bar(xs, heights, width, bottom=bottoms, color=style.COLORS[_COLOR[key]], label=label,
                              edgecolor=style.COLORS["surface"], linewidth=0.5))
        bottoms = [b + h for b, h in zip(bottoms, heights, strict=True)]
    ax.set_ylim(0, max(bottoms) * 1.15)
    ax.set_ylabel("hours")
    if summary["score_available"]:
        ax2 = ax.twinx()
        ax2.grid(False)
        ax2.spines["right"].set_visible(False)
        ax2.tick_params(axis="y", right=False, colors=style.COLORS["line"])
        pts = [(x, r["score"]) for x, r in zip(xs, rows, strict=True) if r["score"] is not None]
        handles += ax2.plot(*zip(*pts, strict=True), color=style.COLORS["line"], marker="o", markersize=4,
                            label="score")
        ax2.set_ylim(0, 100)
        ax2.set_ylabel("score", color=style.COLORS["line"])
    fig.legend(handles=handles, loc="outside upper right", ncols=len(handles))
    style.save(fig, out)
    return summary
