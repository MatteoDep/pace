"""JSON renderer."""

from typing import IO

from pydantic import BaseModel

from pace.render import register


@register("json")
def render_json(model: BaseModel, stream: IO) -> None:
    stream.write(model.model_dump_json(indent=2))
    stream.write("\n")
