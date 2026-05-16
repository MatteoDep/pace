"""Cache info models."""

from pydantic import BaseModel


class CacheStats(BaseModel):
    path: str
    entries: int
    size_bytes: int
