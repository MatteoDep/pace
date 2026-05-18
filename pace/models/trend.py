"""Trend / time-series models."""

from datetime import date

from pydantic import BaseModel


class TrendPoint(BaseModel):
    date: date
    value: float | None = None


class TrendSeries(BaseModel):
    metric: str
    points: list[TrendPoint]

    def values(self) -> list[float | None]:
        return [p.value for p in self.points]
