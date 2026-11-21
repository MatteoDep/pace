"""Intensity chart: per ISO week, actual time in HR zones 1-5 next to the planned time by session intensity."""

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pace.charts import style
from pace.load import fetch_inputs, week_key
from pace.sports import family

ZONES = (1, 2, 3, 4, 5)
LEVELS = ("easy", "moderate", "hard")


async def fetch(start: date, end: date, *, today: date, cache: Any, tracker: Any, refresh: bool,
                sport: str = "running", **_: Any) -> dict:
    activities, sessions = await fetch_inputs(start, end, today=today, cache=cache, tracker=tracker, refresh=refresh)
    return {"activities": activities, "sessions": sessions, "sport": sport,
            "range": {"from": start.isoformat(), "to": end.isoformat()}, "today": today.isoformat()}


def _weeks(start: date, end: date) -> list[tuple[str, date]]:
    out: dict[str, date] = {}
    d = start
    while d <= end:
        out.setdefault(week_key(d), d - timedelta(days=d.weekday()))
        d += timedelta(days=1)
    return list(out.items())


def _summary(data: dict) -> dict:
    lo, hi, sport = data["range"]["from"], data["range"]["to"], data["sport"]
    weeks = {key: {"week": key, "start": mon.isoformat(), "zones_s": dict.fromkeys(ZONES, 0.0),
                   "planned_s": dict.fromkeys(LEVELS, 0.0), "planned_missing": 0}
             for key, mon in _weeks(date.fromisoformat(lo), date.fromisoformat(hi))}
    for a in data["activities"]:
        day = (a.get("startTimeLocal") or "")[:10]
        if not lo <= day <= hi or (sport != "all" and family(a.get("activityType")) != sport):
            continue
        for z in ZONES:
            weeks[week_key(date.fromisoformat(day))]["zones_s"][z] += a.get(f"hrTimeInZone_{z}") or 0
    for s in data["sessions"]:
        if not lo <= s["date"] <= hi or s.get("status") == "skipped" or s.get("intensity") not in LEVELS:
            continue
        if sport != "all" and s.get("sport") != sport:
            continue
        w = weeks[week_key(date.fromisoformat(s["date"]))]
        duration = (s.get("target") or {}).get("duration_s")
        if duration is None:
            w["planned_missing"] += 1
        else:
            w["planned_s"][s["intensity"]] += duration
    rows = []
    for w in weeks.values():
        total = sum(w["zones_s"].values())
        rows.append({"week": w["week"], "start": w["start"], "zones_s": [round(w["zones_s"][z]) for z in ZONES],
                     "planned_s": {k: round(v) for k, v in w["planned_s"].items()},
                     "planned_missing": w["planned_missing"],
                     "low_share": round((w["zones_s"][1] + w["zones_s"][2]) / total, 2) if total else None})
    empty = not any(sum(r["zones_s"]) or sum(r["planned_s"].values()) or r["planned_missing"] for r in rows)
    return {"sport": sport, "weeks": rows, "empty": empty}


def render(data: dict, out: Path, **_: Any) -> dict:
    """data = {activities, sessions (both from fetch_inputs), sport, range, today}."""
    summary = _summary(data)
    fig, ax = style.figure()
    ax.set_title("Weekly intensity: HR zones and plan (hours)")
    if summary["empty"]:
        ax.set_yticks([])
        ax.set_xticks([])
        style.empty_note(ax)
        style.save(fig, out)
        return summary

    weeks = summary["weeks"]
    xs = list(range(len(weeks)))
    width = 0.38
    handles = []
    bottoms = [0.0] * len(weeks)
    for i, z in enumerate(ZONES):
        heights = [w["zones_s"][i] / 3600 for w in weeks]
        handles.append(ax.bar([x - width / 2 for x in xs], heights, width, bottom=bottoms,
                              color=style.COLORS[f"zone{z}"], label=f"zone {z}", edgecolor=style.COLORS["surface"],
                              linewidth=0.5))
        bottoms = [b + h for b, h in zip(bottoms, heights, strict=True)]
    top = max(bottoms)
    pbottoms = [0.0] * len(weeks)
    for level in LEVELS:
        heights = [w["planned_s"][level] / 3600 for w in weeks]
        handles.append(ax.bar([x + width / 2 for x in xs], heights, width, bottom=pbottoms,
                              color=style.COLORS[level], label=f"plan {level}", edgecolor=style.COLORS["surface"],
                              linewidth=0.5))
        pbottoms = [b + h for b, h in zip(pbottoms, heights, strict=True)]
    top = max(top, max(pbottoms))
    for x, w, b in zip(xs, weeks, pbottoms, strict=True):
        if w["planned_missing"]:
            ax.annotate(f"+{w['planned_missing']} no duration", (x + width / 2, b), xytext=(0, 3),
                        textcoords="offset points", ha="center", fontsize=8, color=style.COLORS["muted"])
    ax.set_ylim(0, (top or 1) * 1.2)
    ax.set_xticks(xs, [w["start"][5:] for w in weeks])
    ax.tick_params(axis="x", length=0)
    ax.set_xlabel("week starting (left: actual, right: planned)")
    ax.set_ylabel("hours")
    fig.legend(handles=handles, loc="outside upper right", ncols=4, fontsize=8)
    style.save(fig, out)
    return summary
