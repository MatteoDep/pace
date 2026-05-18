"""Trend-series renderer: full-width sparkline + min/avg/max summary."""

from typing import IO

from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from pace.models.trend import TrendSeries
from pace.render.chart import sparkline


def render_trend(model: BaseModel, stream: IO) -> None:
    """Panel + sparkline for a TrendSeries. Used by the trend command's TTY path."""
    if not isinstance(model, TrendSeries):
        raise TypeError("render_trend only handles TrendSeries.")

    values = model.values()
    numeric = [v for v in values if v is not None]
    summary_text = (
        f"min {min(numeric):.1f}  avg {sum(numeric) / len(numeric):.1f}  max {max(numeric):.1f}"
        if numeric
        else "no data"
    )
    chart = sparkline(values, width=80)

    console = Console(file=stream, force_terminal=stream.isatty())
    body = Text.from_markup(
        f"[cyan]{chart}[/cyan]\n"
        f"[dim]{model.points[0].date} -> {model.points[-1].date}  ({len(values)} pts)[/dim]\n"
        f"{summary_text}"
    )
    console.print(Panel(body, title=f"trend: {model.metric}", border_style="cyan"))
