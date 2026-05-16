"""Generic passthrough models for raw Garmin responses."""

from typing import Any

from pydantic import BaseModel, ConfigDict


class RawData(BaseModel):
    """Wraps an arbitrary Garmin response dict.

    Used when we don't yet have a typed schema for a specific endpoint.
    """

    model_config = ConfigDict(extra="allow")

    @classmethod
    def from_response(cls, data: Any) -> "RawData":
        if data is None:
            return cls()
        if isinstance(data, dict):
            return cls(**data)
        return cls.model_validate({"value": data})
