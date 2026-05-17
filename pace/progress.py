"""Live progress UI driven by aionode's DAG."""

import asyncio
import contextlib
import functools
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from typing import Any, TypeVar

import aionode
from rich.console import Console
from rich.live import Live
from rich.table import Table

T = TypeVar("T")

_STATUS_ICON = {
    "WAITING": ("⏳", "dim"),
    "RUNNING": ("▶", "yellow"),
    "DONE": ("✓", "green"),
    "FAILED": ("✗", "red"),
    "CANCELLED": ("⊘", "dim"),
}


def named_node(
    name: str, fn: Callable[..., Awaitable[T]]
) -> Callable[..., Coroutine[Any, Any, T]]:
    """Wrap an async callable so it appears in the DAG with a custom name."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        return await fn(*args, **kwargs)

    wrapper.__name__ = name
    return aionode.node(wrapper)


def _build_table() -> Table:
    table = Table.grid(padding=(0, 1))
    table.add_column(no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column(justify="right")
    for info in aionode.walk_dag():
        status_name = info.status.name if hasattr(info.status, "name") else str(info.status)
        icon, color = _STATUS_ICON.get(status_name, (status_name, "white"))
        duration = info.duration()
        dur_str = f"{duration:.2f}s" if duration is not None else ""
        table.add_row(f"[{color}]{icon}[/{color}]", info.name, f"[dim]{dur_str}[/dim]")
    return table


def progress_enabled() -> bool:
    """Show progress only when stderr is a TTY."""
    return sys.stderr.isatty()


@contextlib.asynccontextmanager
async def progress_ui(refresh_hz: int = 10) -> AsyncIterator[None]:
    """Render aionode.walk_dag() to a Rich Live on stderr while active."""
    if not progress_enabled():
        yield
        return

    console = Console(file=sys.stderr)
    stop = asyncio.Event()

    async def loop() -> None:
        with Live(_build_table(), console=console, refresh_per_second=refresh_hz) as live:
            while not stop.is_set():
                live.update(_build_table())
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1 / refresh_hz)
                except asyncio.TimeoutError:
                    pass
            live.update(_build_table())

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        stop.set()
        await task
