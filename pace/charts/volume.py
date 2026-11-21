"""Volume chart: weekly bars (actual, projected) and the rolling 7-day sum at daily resolution."""

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pace import load as load_mod
from pace.charts import style

METRICS = {"km": "km", "elev": "elev_m", "hours": "hours"}
UNITS = {"km": "km", "elev": "m", "hours": "h"}
LABELS = {"km": "Distance", "elev": "Elevation gain", "hours": "Time"}


async def fetch(start: date, end: date, *, today: date, cache: Any, tracker: Any, refresh: bool,
                sport: str = "running", **_: Any) -> dict:
    acts, sessions = await load_mod.fetch_inputs(start, end, today=today, cache=cache, tracker=tracker,
                                                 refresh=refresh)
    return load_mod.aggregate(acts, sessions, start=start, end=end, today=today, sport=sport)


def _summary(data: dict, metric: str) -> dict:
    key = METRICS[metric]
    weeks = [{"week": w["week"], "actual": w["actual"][key] if w["actual"] is not None else None,
              "projected": w["projected"][key]} for w in data["weeks"]]
    today_row = next((d for d in data["days"] if d["date"] == data["today"]), None)
    empty = not any(w["projected"] or w["actual"] for w in weeks)
    missing = sum(w["planned"]["missing"][key] for w in data["weeks"] if w["planned"] is not None)
    plan_ahead = any(d["planned"] is not None for d in data["days"] if d["date"] >= data["today"])
    return {"metric": metric, "sport": data["sport"], "weeks": weeks,
            "rolling7_today": today_row["rolling7"][key] if today_row else None, "plan_ahead": plan_ahead,
            "missing": missing, "empty": empty}


def render(data: dict, out: Path, *, metric: str = "km", **_: Any) -> dict:
    """data = `pace.load.aggregate(...)`; its `today` splits actual from projected."""
    summary = _summary(data, metric)
    key, unit = METRICS[metric], UNITS[metric]
    today = date.fromisoformat(data["today"])
    fig, ax = style.figure()
    sport = "Running" if data["sport"] == "running" else "All sports"
    ax.set_title(f"{sport}: {LABELS[metric].lower()} per week ({unit})")
    start = date.fromisoformat(data["range"]["from"])
    end = date.fromisoformat(data["range"]["to"])
    ax.set_xlim(start, end + timedelta(days=1))
    _date_axis(ax)
    if summary["empty"]:
        ax.set_yticks([])
        style.empty_note(ax)
        style.save(fig, out)
        return summary

    for week, row in zip(data["weeks"], summary["weeks"], strict=True):
        mon = date.fromisoformat(week["start"])
        x, width = style.noon(mon), timedelta(days=6)
        actual = row["actual"] or 0
        if mon + timedelta(days=6) < today:
            ax.bar(x, actual, width, align="edge", color=style.COLORS["actual"])
        elif mon <= today:
            ax.bar(x, actual, width, align="edge", color=style.COLORS["actual"])
            ahead = max(row["projected"] - actual, 0)
            ax.bar(x, ahead, width, bottom=actual, align="edge", color=style.COLORS["projected"],
                   edgecolor=style.COLORS["surface"], linewidth=1)
            digits = 1 if metric == "hours" else 0
            if actual or row["projected"]:
                ax.annotate(f"{actual:.{digits}f} / {row['projected']:.{digits}f}",
                            (style.noon(mon) + timedelta(days=3), row["projected"]), xytext=(0, 4),
                            textcoords="offset points", ha="center", va="bottom", fontsize=10,
                            color=style.COLORS["text"], zorder=4,
                            bbox={"boxstyle": "round,pad=0.2", "facecolor": style.COLORS["surface"],
                                  "edgecolor": "none"})
        else:
            ax.bar(x, row["projected"], width, align="edge", color=style.COLORS["projected"])

    days = [(style.noon(date.fromisoformat(d["date"])), d["rolling7"][key], d["date"]) for d in data["days"]]
    past = [(x, y) for x, y, d in days if d <= data["today"]]
    future = [(x, y) for x, y, d in days if d >= data["today"]]
    if past:
        ax.plot(*zip(*past, strict=True), color=style.COLORS["line"])
    if future and summary["plan_ahead"]:
        ax.plot(*zip(*future, strict=True), color=style.COLORS["line"], linestyle=(0, (3, 2)))
    if start <= today <= end:
        style.today_line(ax, today)
    ax.set_ylim(bottom=0)
    ax.set_ylabel(unit)
    _legend(ax)
    style.save(fig, out)
    return summary


def _date_axis(ax: Any) -> None:
    import matplotlib.dates as mdates

    locator = mdates.AutoDateLocator(minticks=4, maxticks=9)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))


def _legend(ax: Any) -> None:
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    handles = [Patch(color=style.COLORS["actual"], label="actual"),
               Patch(color=style.COLORS["projected"], label="planned (projected)"),
               Line2D([], [], color=style.COLORS["line"], label="rolling 7 days")]
    ax.figure.legend(handles=handles, loc="outside upper right", ncols=3)
