"""CSV renderer."""

import csv
from typing import IO

from pydantic import BaseModel

from pace.models.list import ListModel
from pace.render import register
from pace.render._columns import infer_columns, to_dict


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
    columns = model.columns or infer_columns(items, cap=64)
    writer = csv.writer(stream)
    writer.writerow(columns)
    for row in items:
        rec = to_dict(row)
        writer.writerow([_fmt(rec.get(c)) for c in columns])


def _fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
