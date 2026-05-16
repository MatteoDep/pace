"""Markdown table renderer."""

from typing import IO

from pydantic import BaseModel

from pace.models.list import ListModel
from pace.render import register
from pace.render._columns import infer_columns, to_dict


@register("md")
def render_md(model: BaseModel, stream: IO) -> None:
    if isinstance(model, ListModel):
        _list_md(model, stream)
    else:
        _scalar_md(model, stream)


def _scalar_md(model: BaseModel, stream: IO) -> None:
    stream.write(f"## {type(model).__name__}\n\n")
    stream.write("| Field | Value |\n|---|---|\n")
    for name, value in model.model_dump().items():
        stream.write(f"| {_esc(name)} | {_esc(_fmt(value))} |\n")


def _list_md(model: ListModel, stream: IO) -> None:
    items = model.items
    title = model.title or "items"
    stream.write(f"## {title}\n\n")
    if not items:
        stream.write("_no rows_\n")
        return
    columns = model.columns or infer_columns(items)
    stream.write("| " + " | ".join(_esc(c) for c in columns) + " |\n")
    stream.write("|" + "|".join("---" for _ in columns) + "|\n")
    for row in items:
        rec = to_dict(row)
        stream.write("| " + " | ".join(_esc(_fmt(rec.get(c))) for c in columns) + " |\n")


def _fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _esc(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")
