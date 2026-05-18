"""Watch a metric and trigger a shell hook on threshold crossings."""

import asyncio
import operator
import shlex
import subprocess
import sys
from collections.abc import Callable
from datetime import date

import typer

from pace.api import safe_call
from pace.commands._trend_metrics import METRICS, TrendMetric

_OPS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}


def _parse_predicate(expr: str) -> Callable[[float], bool]:
    """Parse e.g. `value < 20` into a callable taking the metric value."""
    expr = expr.strip()
    op_texts = sorted(_OPS, key=lambda s: len(s), reverse=True)
    for op_text in op_texts:
        idx = expr.find(op_text)
        if idx <= 0:
            continue
        lhs = expr[:idx].strip()
        rhs = expr[idx + len(op_text):].strip()
        if lhs != "value":
            continue
        try:
            threshold = float(rhs)
        except ValueError as err:
            raise typer.BadParameter(f"Right-hand side must be numeric: {rhs!r}") from err
        op_fn = _OPS[op_text]
        return lambda v, op_fn=op_fn, threshold=threshold: op_fn(v, threshold)
    raise typer.BadParameter(
        f"Expected `value <op> <number>`, e.g. 'value < 20'. Got: {expr!r}"
    )


async def _poll_once(spec: TrendMetric) -> float | None:
    ok, data, err = await safe_call(spec.method, date.today().isoformat())
    if not ok:
        print(f"pace watch: fetch failed: {err}", file=sys.stderr)
        return None
    return spec.extract(data)


def _run_hook(cmd: str, env_extra: dict[str, str]) -> None:
    import os
    env = os.environ.copy()
    env.update(env_extra)
    subprocess.run(shlex.split(cmd), env=env, check=False)


def register(app: typer.Typer) -> None:
    """Attach `pace watch` to the root app."""
    app.command("watch", help="Poll a metric; trigger a hook when it crosses a threshold.")(_watch_cmd)


def _watch_cmd(
    metric: str = typer.Argument(..., help=f"Metric: one of {', '.join(sorted(METRICS))}."),
    threshold: str = typer.Option(..., "--threshold", help="Predicate like 'value < 20'."),
    on_cross: str = typer.Option(..., "--on-cross", help="Shell command to run on threshold crossing."),
    interval: int = typer.Option(300, "--interval", "-i", help="Poll interval in seconds (>=60)."),
    once: bool = typer.Option(False, "--once", help="Poll once and exit (test mode)."),
) -> None:
    """Poll METRIC every --interval seconds; run --on-cross when the predicate becomes true."""
    if metric not in METRICS:
        typer.secho(
            f"Unknown metric {metric!r}. Choices: {', '.join(sorted(METRICS))}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    if interval < 60 and not once:
        typer.secho("Interval must be >= 60s (avoid hammering the API).", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)

    predicate = _parse_predicate(threshold)
    spec = METRICS[metric]

    async def loop() -> None:
        last_state: bool | None = None
        while True:
            value = await _poll_once(spec)
            if value is not None:
                state = predicate(value)
                typer.echo(f"[{metric}] value={value:.2f} cond={state}")
                if state and last_state is False:
                    typer.secho("-> threshold crossed; running hook", fg=typer.colors.YELLOW)
                    _run_hook(on_cross, {"PACE_METRIC": metric, "PACE_VALUE": str(value)})
                last_state = state
            if once:
                return
            await asyncio.sleep(interval)

    try:
        asyncio.run(loop())
    except KeyboardInterrupt:
        typer.echo("Stopped.")
