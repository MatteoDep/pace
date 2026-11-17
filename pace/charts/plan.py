"""Plan chart: one mark per session on a date axis, colored and shaped by status; unplanned activities hollow."""

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pace import matching
from pace.charts import style
from pace.commands.plan import fetch_status_inputs
from pace.sports import SESSION_SPORTS

#: Legend order: what happened first, then what is still ahead.
STATUS_ORDER = ("done", "modified", "missed", "unconfirmed", "skipped", "rest", "pushed", "planned")
_ROW_STEP = 0.22  # vertical offset between sessions of one sport on one day


async def fetch(start: date, end: date, *, today: date, cache: Any, tracker: Any, refresh: bool, **_: Any) -> dict:
    """`plan status` data without effort (no per-activity calls), plus the range and today."""
    sessions, rows = await fetch_status_inputs(start, end, today=today, cache=cache, tracker=tracker,
                                               refresh=refresh)
    data = matching.match(sessions, rows, today=today, effort=None)
    return {**data, "range": {"from": start.isoformat(), "to": end.isoformat()}, "today": today.isoformat()}


def _summary(data: dict) -> dict:
    counts: dict[str, int] = {}
    unplanned = 0
    for d in data["days"]:
        for s in d["sessions"]:
            counts[s["status"]] = counts.get(s["status"], 0) + 1
        unplanned += len(d["unplanned"])
    return {"counts": counts, "unplanned": unplanned, "empty": not counts and not unplanned}


def render(data: dict, out: Path, **_: Any) -> dict:
    """data = `pace.matching.match(...)` plus `range` and `today`."""
    summary = _summary(data)
    start = date.fromisoformat(data["range"]["from"])
    end = date.fromisoformat(data["range"]["to"])
    today = date.fromisoformat(data["today"])
    fig, ax = style.figure()
    ax.set_title(f"Plan vs done, {start:%b} {start.day} to {end:%b} {end.day}")
    ax.set_xlim(start, end + timedelta(days=1))
    _date_axis(ax, start, end)
    if summary["empty"]:
        ax.set_yticks([])
        style.empty_note(ax, "No sessions or activities in range")
        style.save(fig, out)
        return summary

    marks: list[tuple[date, str, str]] = []  # (day, sport, status or "unplanned")
    for d in data["days"]:
        day = date.fromisoformat(d["date"])
        marks += [(day, s["sport"], s["status"]) for s in d["sessions"]]
        marks += [(day, a["sport"], "unplanned") for a in d["unplanned"]]
    sports = [s for s in SESSION_SPORTS if any(m[1] == s for m in marks)]
    rows = {s: i for i, s in enumerate(reversed(sports))}
    stacks: dict[tuple[date, str], list[str]] = {}
    for day, sport, status in marks:
        stacks.setdefault((day, sport), []).append(status)

    points: dict[str, tuple[list, list]] = {}
    for (day, sport), statuses in stacks.items():
        for k, status in enumerate(statuses):
            y = rows[sport] + (k - (len(statuses) - 1) / 2) * _ROW_STEP
            xs, ys = points.setdefault(status, ([], []))
            xs.append(style.noon(day))
            ys.append(y)
    for status in (*STATUS_ORDER, "unplanned"):
        if status not in points:
            continue
        xs, ys = points[status]
        n = len(xs)
        label = f"{status} ({n})"
        if status == "unplanned":
            ax.scatter(xs, ys, s=110, marker="o", facecolors="none", edgecolors=style.COLORS["unplanned"],
                       linewidths=1.8, label=label, zorder=3)
            continue
        color = style.COLORS["planned"] if status in ("planned", "pushed") else style.COLORS[status]
        size = 50 if status == "rest" else 120
        # Filled marks get a surface ring so neighbours stay apart; the line-only `x` gets a heavier stroke.
        edge = {"linewidths": 2.2} if status == "skipped" else {"edgecolors": style.COLORS["surface"], "linewidths": 1.5}
        ax.scatter(xs, ys, s=size, marker=style.MARKERS[status], color=color, label=label, zorder=3, **edge)

    if start <= today <= end:
        style.today_line(ax, today)
    ax.set_yticks(list(rows.values()), list(rows))
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.grid(axis="x", color=style.COLORS["grid"], linewidth=0.8)
    ax.grid(axis="y", visible=False)
    fig.legend(loc="outside right upper", handletextpad=0.3)
    style.save(fig, out)
    return summary


def _date_axis(ax: Any, start: date, end: date) -> None:
    import matplotlib.dates as mdates

    step = max(1, ((end - start).days + 1) // 10 // 7) * 7 if (end - start).days > 14 else 1
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=1) if step == 1 else
                               mdates.WeekdayLocator(byweekday=mdates.MO, interval=step // 7))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%a\n%b %d" if step == 1 else "%b %d"))
