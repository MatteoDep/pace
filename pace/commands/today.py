"""Daily dashboard: parallel fetch of key wellness metrics."""

import asyncio
import json
import sys
from datetime import date
from typing import Any

import typer
from rich.console import Console, Group
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text

from pace import metrics
from pace.api import safe_call
from pace.cache import Cache
from pace.progress import named_node, progress_ui
from pace.render import Format
from pace.render.chart import sparkline
from pace.render.colors import colored, threshold_color


def register(app: typer.Typer) -> None:
    """Wire the top-level `pace today` command onto the root app."""
    app.command("today", help="Daily dashboard: steps, sleep, HR, body battery, readiness, HRV.")(_today_cmd)


def _today_cmd(
    fmt: Format = typer.Option(None, "--format", "-f", help="table or json."),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Daily dashboard combining six wellness metrics."""
    if fmt is Format.CSV or fmt is Format.MD:
        typer.secho(
            f"Format {fmt.value!r} is not supported for `today`. Use a single subcommand.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    try:
        payload = asyncio.run(_collect(no_cache))
    except RuntimeError as err:
        typer.secho(f"Error: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None

    use_json = fmt is Format.JSON or (fmt is None and not sys.stdout.isatty())
    if use_json:
        print(json.dumps(payload, default=str, indent=2))
    else:
        _render_dashboard(payload)


async def _collect(no_cache: bool) -> dict[str, Any]:
    today = date.today().isoformat()
    cache = None if no_cache else Cache()

    async def fetch(method: str, *args: Any, ttl: int) -> Any:
        async def call():
            ok, data, err = await safe_call(method, *args)
            if not ok:
                raise RuntimeError(err)
            return data

        if cache is None:
            return await call()
        key = ":".join([method, *(str(a) for a in args)])
        return await cache.get_or_fetch(key, call, ttl=ttl)

    try:
        async with progress_ui():
            tasks = {
                "summary": asyncio.create_task(
                    named_node("summary", fetch)("get_user_summary", today, ttl=300)
                ),
                "sleep": asyncio.create_task(
                    named_node("sleep", fetch)("get_sleep_data", today, ttl=600)
                ),
                "body_battery": asyncio.create_task(
                    named_node("body-battery", fetch)("get_body_battery", today, ttl=300)
                ),
                "readiness": asyncio.create_task(
                    named_node("readiness", fetch)("get_training_readiness", today, ttl=600)
                ),
                "hrv": asyncio.create_task(
                    named_node("hrv", fetch)("get_hrv_data", today, ttl=600)
                ),
                "hr": asyncio.create_task(
                    named_node("heart-rate", fetch)("get_heart_rates", today, ttl=300)
                ),
            }
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    finally:
        if cache is not None:
            cache.close()

    out: dict[str, Any] = {}
    for key, value in zip(tasks.keys(), results):
        out[key] = None if isinstance(value, BaseException) else value
    return out


# --- Rendering ---------------------------------------------------------------


def _render_dashboard(payload: dict[str, Any]) -> None:
    console = Console()
    layout = Layout()
    layout.split_column(
        Layout(name="top"),
        Layout(name="mid"),
        Layout(name="bot"),
    )
    for row in ("top", "mid", "bot"):
        layout[row].split_row(Layout(name=f"{row}.l"), Layout(name=f"{row}.r"))

    layout["top.l"].update(_steps_panel(payload.get("summary")))
    layout["top.r"].update(_sleep_panel(payload.get("sleep")))
    layout["mid.l"].update(_body_battery_panel(payload.get("body_battery")))
    layout["mid.r"].update(_readiness_panel(payload.get("readiness")))
    layout["bot.l"].update(_hrv_panel(payload.get("hrv")))
    layout["bot.r"].update(_hr_panel(payload.get("hr")))

    console.print(layout, height=24)


def _panel(title: str, *lines: str | Text) -> Panel:
    body = Group(*(line if isinstance(line, Text) else Text.from_markup(line) for line in lines))
    return Panel(body, title=title, border_style="cyan", padding=(0, 1))


def _steps_panel(data: Any) -> Panel:
    if not isinstance(data, dict):
        return _panel("Steps", "[dim]no data[/dim]")
    steps = data.get("totalSteps") or 0
    goal = data.get("dailyStepGoal") or 0
    distance_km = (data.get("totalDistanceMeters") or 0) / 1000
    calories = data.get("totalKilocalories") or 0
    floors = data.get("floorsAscended") or 0
    color = threshold_color(steps, metrics.STEPS)
    pct = f"{(steps / goal * 100):.0f}%" if goal else ""
    return _panel(
        "Steps",
        colored(f"{steps:,}", color) + f"  [dim]/ {goal:,} {pct}[/dim]",
        f"[dim]distance:[/dim] {distance_km:.2f} km",
        f"[dim]calories:[/dim] {int(calories)}",
        f"[dim]floors:[/dim] {floors}",
    )


def _sleep_panel(data: Any) -> Panel:
    if not isinstance(data, dict):
        return _panel("Sleep", "[dim]no data[/dim]")
    daily = data.get("dailySleepDTO") or {}
    seconds = daily.get("sleepTimeSeconds") or 0
    h, m = divmod(int(seconds) // 60, 60)
    score = ((daily.get("sleepScores") or {}).get("overall") or {}).get("value")
    color = threshold_color(score, metrics.SLEEP_SCORE) if isinstance(score, (int, float)) else "dim"
    deep = (daily.get("deepSleepSeconds") or 0) // 60
    light = (daily.get("lightSleepSeconds") or 0) // 60
    rem = (daily.get("remSleepSeconds") or 0) // 60
    awake = (daily.get("awakeSleepSeconds") or 0) // 60
    return _panel(
        "Sleep",
        f"{colored(f'{h}h {m}m', color)}  [dim]score:[/dim] {colored(score or '-', color)}",
        f"[dim]deep:[/dim] {deep}m  [dim]light:[/dim] {light}m",
        f"[dim]rem:[/dim] {rem}m  [dim]awake:[/dim] {awake}m",
    )


def _body_battery_panel(data: Any) -> Panel:
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        return _panel("Body Battery", "[dim]no data[/dim]")
    series_vals = _battery_series(data.get("bodyBatteryValuesArray"))
    current = series_vals[-1] if series_vals else None
    high = data.get("max") or (max(series_vals) if series_vals else None)
    low = data.get("min") or (min(series_vals) if series_vals else None)
    color = threshold_color(current, metrics.BODY_BATTERY) if isinstance(current, (int, float)) else "dim"
    spark = sparkline(series_vals, width=24) if series_vals else ""
    return _panel(
        "Body Battery",
        f"{colored(current if current is not None else '-', color)}  [dim]high:[/dim] {high}  [dim]low:[/dim] {low}",
        f"[cyan]{spark}[/cyan]" if spark else "",
    )


def _readiness_panel(data: Any) -> Panel:
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return _panel("Training Readiness", "[dim]no data[/dim]")
    score = data.get("score")
    level = data.get("level") or ""
    color = threshold_color(score, metrics.TRAINING_READINESS) if isinstance(score, (int, float)) else "dim"
    feedback = data.get("feedbackLong") or data.get("feedbackShort") or ""
    return _panel(
        "Training Readiness",
        f"{colored(score or '-', color)}  [dim]{level}[/dim]",
        f"[dim]{feedback}[/dim]" if feedback else "",
    )


def _hrv_panel(data: Any) -> Panel:
    if not isinstance(data, dict):
        return _panel("HRV", "[dim]no data[/dim]")
    summary = data.get("hrvSummary") or {}
    last_night = summary.get("lastNightAvg")
    status = summary.get("status") or ""
    seven_day = summary.get("weeklyAvg")
    color = threshold_color(last_night, metrics.HRV_NIGHT_AVG) if isinstance(last_night, (int, float)) else "dim"
    return _panel(
        "HRV",
        f"{colored(last_night or '-', color)}  [dim]{status}[/dim]",
        f"[dim]7-day avg:[/dim] {seven_day or '-'}",
    )


def _hr_panel(data: Any) -> Panel:
    if not isinstance(data, dict):
        return _panel("Heart Rate", "[dim]no data[/dim]")
    resting = data.get("restingHeartRate")
    max_hr = data.get("maxHeartRate")
    min_hr = data.get("minHeartRate")
    values = _hr_series(data.get("heartRateValues"))
    color = threshold_color(resting, metrics.RESTING_HR) if isinstance(resting, (int, float)) else "dim"
    spark = sparkline(values, width=24) if values else ""
    return _panel(
        "Heart Rate",
        f"[dim]resting:[/dim] {colored(resting or '-', color)}  [dim]min/max:[/dim] {min_hr or '-'}/{max_hr or '-'}",
        f"[cyan]{spark}[/cyan]" if spark else "",
    )


# --- Series extractors -------------------------------------------------------


def _battery_series(raw: Any) -> list[float | None]:
    """Body battery returns `[[timestamp, status, level, ...], ...]`."""
    if not isinstance(raw, list):
        return []
    out: list[float | None] = []
    for entry in raw:
        if isinstance(entry, list) and len(entry) >= 3 and isinstance(entry[2], (int, float)):
            out.append(float(entry[2]))
    return out


def _hr_series(raw: Any) -> list[float | None]:
    """Heart rate values are `[[timestamp, bpm], ...]` with None bpms."""
    if not isinstance(raw, list):
        return []
    out: list[float | None] = []
    for entry in raw:
        if isinstance(entry, list) and len(entry) >= 2:
            bpm = entry[1]
            out.append(float(bpm) if isinstance(bpm, (int, float)) else None)
    return out
