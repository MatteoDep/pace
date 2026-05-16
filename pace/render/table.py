"""Rich table renderer -- scalar (field/value) or list (multi-column)."""

from typing import IO

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from pace.models.list import ListModel
from pace.render import register
from pace.render._columns import infer_columns, to_dict


@register("table")
def render_table(model: BaseModel, stream: IO) -> None:
    console = Console(file=stream, force_terminal=stream.isatty())
    if isinstance(model, ListModel):
        console.print(_list_table(model))
    else:
        console.print(_scalar_table(model))


def _scalar_table(model: BaseModel) -> Table:
    table = Table(title=type(model).__name__, show_header=True, header_style="bold")
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    for name, value in model.model_dump().items():
        table.add_row(name, _fmt(value))
    return table


def _list_table(model: ListModel) -> Table:
    items = model.items
    title = model.title or "items"
    if not items:
        table = Table(title=f"{title} (empty)", show_header=False)
        table.add_column("")
        table.add_row("[dim]no rows[/dim]")
        return table

    columns = model.columns or infer_columns(items)
    table = Table(title=title, show_header=True, header_style="bold")
    for col in columns:
        table.add_column(col)
    for row in items:
        rec = to_dict(row)
        table.add_row(*(_fmt(rec.get(c)) for c in columns))
    return table


def _fmt(value: object) -> str:
    if value is None:
        return "[dim]-[/dim]"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        return f"[dim]<{type(value).__name__}>[/dim]"
    return str(value)
