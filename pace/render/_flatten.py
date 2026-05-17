"""Flatten nested dicts so scalar renderers can show one level deep."""

from typing import Any


def flatten_dict(
    data: dict[str, Any], sep: str = ".", max_depth: int = 2
) -> dict[str, Any]:
    """Return a one-level dict where nested keys are joined with `sep`.

    Lists of scalars are joined as `"a, b, c"`.
    Lists of dicts are kept as `<list[N]>` placeholders.
    Walks up to `max_depth` levels deep.
    """
    out: dict[str, Any] = {}
    _walk(data, prefix="", out=out, sep=sep, depth=0, max_depth=max_depth)
    return out


def _walk(
    value: Any,
    prefix: str,
    out: dict[str, Any],
    sep: str,
    depth: int,
    max_depth: int,
) -> None:
    if isinstance(value, dict):
        if depth >= max_depth:
            out[prefix] = f"<dict[{len(value)}]>"
            return
        for key, child in value.items():
            new_prefix = f"{prefix}{sep}{key}" if prefix else key
            _walk(child, new_prefix, out, sep, depth + 1, max_depth)
        return
    if isinstance(value, list):
        if not value:
            out[prefix] = "[]"
            return
        if all(_is_scalar(v) for v in value):
            out[prefix] = ", ".join("" if v is None else str(v) for v in value)
            return
        out[prefix] = f"<list[{len(value)}]>"
        return
    out[prefix] = value


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))
