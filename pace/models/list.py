"""Collection wrapper model."""

from typing import Any

from pydantic import BaseModel, ConfigDict


class ListModel(BaseModel):
    """Wraps a list of records.

    Renderers detect the `items` field and switch to multi-column layout.
    `columns` (optional) constrains which fields to display in the order given.
    """

    model_config = ConfigDict(extra="allow")

    items: list[Any]
    columns: list[str] | None = None
    title: str | None = None
