"""Rich table renderer -- generic two-column field/value layout."""

from typing import IO

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from pace.render import register


@register("table")
def render_table(model: BaseModel, stream: IO) -> None:
    console = Console(file=stream, force_terminal=stream.isatty())
    table = Table(title=type(model).__name__, show_header=True, header_style="bold")
    table.add_column("Field", style="cyan")
    table.add_column("Value")

    for name, value in model.model_dump().items():
        table.add_row(name, _fmt(value))

    console.print(table)


def _fmt(value: object) -> str:
    if value is None:
        return "[dim]-[/dim]"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)
