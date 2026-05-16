"""Shared column-inference and row-coercion helpers for list renderers."""

from typing import Any

from pydantic import BaseModel

_MAX_AUTO_COLUMNS = 8


def to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, BaseModel):
        return row.model_dump()
    if isinstance(row, dict):
        return row
    return {"value": row}


def is_scalar(value: Any) -> bool:
    """True for values that fit naturally in a single table cell."""
    if value is None:
        return True
    return isinstance(value, (str, int, float, bool))


def infer_columns(items: list[Any], cap: int = _MAX_AUTO_COLUMNS) -> list[str]:
    """Pick up to `cap` scalar columns, in order of first appearance across rows.

    Skips fields whose values are always dicts/lists (they don't render in a table cell).
    Skips fields whose values are always None.
    """
    counts: dict[str, int] = {}
    has_scalar: dict[str, bool] = {}
    order: list[str] = []

    for row in items[:50]:
        rec = to_dict(row)
        for key, value in rec.items():
            if key not in counts:
                counts[key] = 0
                order.append(key)
            counts[key] += 1
            if is_scalar(value) and value is not None:
                has_scalar[key] = True

    return [k for k in order if has_scalar.get(k)][:cap]
