"""One shared chart style: color tokens, figure size and the PNG writer. matplotlib is imported lazily.

Tokens follow the dataviz reference palette (light surface, since PNGs are viewed in chat apps):
- `actual` is categorical slot 1 (blue `#2a78d6`); `planned` and `projected` are its lighter ordinal step 250
  (`#86b6ef`, the lightest step that still clears 2:1 on the surface), so "not done yet" reads as the same hue, lighter.
- `line` (the rolling 7-day sum) is categorical slot 2 (orange); blue vs orange passes every validator check.
- Session statuses use the reserved status palette (good, warning, critical) plus grays.
  Green vs red fails the CVD check and warning yellow is under 3:1, so every status also gets its own
  marker shape (`MARKERS`) and a legend label with its count: color never carries status alone.
- Text uses ink tokens (`text`, `muted`), never a series color; gridlines are hairlines one step off the surface.
"""

from datetime import date, datetime, time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

COLORS: dict[str, str] = {
    "actual": "#2a78d6",
    "planned": "#86b6ef",
    "projected": "#86b6ef",
    "line": "#eb6834",
    "done": "#0ca30c",
    "modified": "#fab219",
    "missed": "#d03b3b",
    "unconfirmed": "#898781",
    "skipped": "#898781",
    "rest": "#c3c2b7",
    "unplanned": "#52514e",
    "hrv": "#7a52b8",
    "deep": "#1d4f91",
    "light": "#86b6ef",
    "rem": "#7a52b8",
    "awake": "#d9d7cc",
    "zone1": "#cfe2f8",
    "zone2": "#86b6ef",
    "zone3": "#2a78d6",
    "zone4": "#1d4f91",
    "zone5": "#0f2c57",
    "easy": "#f7c4ae",
    "moderate": "#eb6834",
    "hard": "#9c3412",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    "text": "#0b0b0b",
    "muted": "#52514e",
    "surface": "#fcfcfb",
}

#: Marker per session status (and `unplanned`, drawn hollow): the shape is the non-color channel.
MARKERS: dict[str, str] = {
    "done": "o", "modified": "D", "missed": "X", "unconfirmed": "s", "skipped": "x", "rest": "o",
    "planned": "o", "pushed": "^", "unplanned": "o",
}

WIDTH_IN, HEIGHT_IN, DPI = 8, 4.5, 150  # 1200 x 675 px

#: Sizes chosen so text stays legible when a phone shows the PNG about 400 px wide.
RC: dict[str, object] = {
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.titlelocation": "left",
    "axes.labelsize": 11,
    "axes.labelcolor": COLORS["muted"],
    "axes.edgecolor": COLORS["axis"],
    "axes.linewidth": 0.8,
    "axes.facecolor": COLORS["surface"],
    "axes.grid": True,
    "axes.grid.axis": "y",
    "axes.axisbelow": True,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.spines.left": False,
    "grid.color": COLORS["grid"],
    "grid.linewidth": 0.8,
    "grid.linestyle": "-",
    "xtick.color": COLORS["muted"],
    "ytick.color": COLORS["muted"],
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "ytick.left": False,
    "text.color": COLORS["text"],
    "legend.frameon": False,
    "legend.fontsize": 10,
    "lines.linewidth": 2,
    "lines.solid_capstyle": "round",
    "lines.solid_joinstyle": "round",
    "figure.facecolor": COLORS["surface"],
    "savefig.facecolor": COLORS["surface"],
}


def figure() -> tuple["Figure", "Axes"]:
    """A 1200 x 675 px figure with one axes and the shared style.

    The rc is applied to matplotlib's process-wide defaults (pace draws one chart per call), so every
    artist the renderer adds after this call picks it up.
    """
    import matplotlib
    from matplotlib.figure import Figure

    matplotlib.rcParams.update(RC)
    fig = Figure(figsize=(WIDTH_IN, HEIGHT_IN), dpi=DPI, layout="constrained")
    return fig, fig.add_subplot()


def save(fig: "Figure", path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="png", dpi=DPI)
    return path


def noon(d: date) -> datetime:
    """The middle of a day on a date axis (`date + timedelta(hours=12)` would drop the hours)."""
    return datetime.combine(d, time(12))


def today_line(ax: "Axes", today: date) -> None:
    """A dotted hairline through the middle of today, labeled at the top of the plot."""
    ax.axvline(noon(today), color=COLORS["muted"], linewidth=0.8, linestyle=":", zorder=1)
    ax.annotate("today", (noon(today), 1), xycoords=("data", "axes fraction"), xytext=(3, -2),
                textcoords="offset points", ha="left", va="top", fontsize=9, color=COLORS["muted"])


def date_axis(ax: "Axes", start: date, end: date) -> None:
    """Day ticks for ranges up to two weeks, Monday ticks (every few weeks when long) beyond."""
    import matplotlib.dates as mdates

    span = (end - start).days
    if span <= 14:
        ax.xaxis.set_major_locator(mdates.DayLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%a\n%b %d" if span <= 10 else "%b %d"))
    else:
        interval = max(1, -(-(span // 7) // 8))
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO, interval=interval))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))


def empty_note(ax: "Axes", text: str = "No data in range") -> None:
    ax.text(0.5, 0.5, text, transform=ax.transAxes, ha="center", va="center", fontsize=14, color=COLORS["muted"])
