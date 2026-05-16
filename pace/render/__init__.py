"""Output formatter registry and dispatcher."""

import sys
from enum import Enum
from typing import IO, Callable

from pydantic import BaseModel

Renderer = Callable[[BaseModel, IO], None]


class Format(str, Enum):
    TABLE = "table"
    JSON = "json"
    CSV = "csv"
    MD = "md"


_REGISTRY: dict[str, Renderer] = {}


def register(name: str) -> Callable[[Renderer], Renderer]:
    def deco(fn: Renderer) -> Renderer:
        _REGISTRY[name] = fn
        return fn
    return deco


def default_format() -> Format:
    return Format.TABLE if sys.stdout.isatty() else Format.JSON


def render(model: BaseModel, fmt: Format | None = None, stream: IO | None = None) -> None:
    fmt = fmt or default_format()
    stream = stream or sys.stdout
    renderer = _REGISTRY.get(fmt.value)
    if renderer is None:
        raise ValueError(f"No renderer registered for format {fmt.value!r}.")
    renderer(model, stream)


# Trigger registration of built-in renderers.
from pace.render import json_, table  # noqa: E402, F401
