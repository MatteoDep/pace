"""CSV renderer."""

import csv
from typing import IO, Any

from pydantic import BaseModel

from pace.models.list import ListModel
from pace.render import register


@register("csv")
def render_csv(model: BaseModel, stream: IO) -> None:
    if isinstance(model, ListModel):
        _list_csv(model, stream)
    else:
        _scalar_csv(model, stream)


def _scalar_csv(model: BaseModel, stream: IO) -> None:
    writer = csv.writer(stream)
    writer.writerow(["field", "value"])
    for name, value in model.model_dump().items():
        writer.writerow([name, _fmt(value)])


def _list_csv(model: ListModel, stream: IO) -> None:
    items = model.items
    if not items:
        return
    columns = model.columns or _infer_columns(items)
    writer = csv.writer(stream)
    writer.writerow(columns)
    for row in items:
        rec = _to_dict(row)
        writer.writerow([_fmt(rec.get(c)) for c in columns])


def _infer_columns(items: list[Any]) -> list[str]:
    seen: list[str] = []
    for row in items[:20]:
        rec = _to_dict(row)
        for key in rec:
            if key not in seen:
                seen.append(key)
    return seen


def _to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, BaseModel):
        return row.model_dump()
    if isinstance(row, dict):
        return row
    return {"value": row}


def _fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
