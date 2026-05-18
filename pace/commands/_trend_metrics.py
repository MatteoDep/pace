"""Per-metric definitions for `pace wellness trend`."""

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class TrendMetric:
    name: str
    method: str  # Garmin API method name (single-date)
    extract: Callable[[Any], float | None]


def _user_summary_steps(data: Any) -> float | None:
    if isinstance(data, dict):
        v = data.get("totalSteps")
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _resting_hr(data: Any) -> float | None:
    if isinstance(data, dict):
        v = data.get("restingHeartRate")
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _hrv_last_night(data: Any) -> float | None:
    if isinstance(data, dict):
        summary = data.get("hrvSummary") or {}
        v = summary.get("lastNightAvg")
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _sleep_hours(data: Any) -> float | None:
    if isinstance(data, dict):
        daily = data.get("dailySleepDTO") or {}
        seconds = daily.get("sleepTimeSeconds")
        if isinstance(seconds, (int, float)):
            return float(seconds) / 3600.0
    return None


def _body_battery_high(data: Any) -> float | None:
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        v = data.get("max")
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _readiness_score(data: Any) -> float | None:
    if isinstance(data, list):
        data = data[0] if data else None
    if isinstance(data, dict):
        v = data.get("score")
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _stress_avg(data: Any) -> float | None:
    if isinstance(data, dict):
        v = data.get("avgStressLevel") or data.get("overallStressLevel")
        if isinstance(v, (int, float)) and v >= 0:
            return float(v)
    return None


METRICS: dict[str, TrendMetric] = {
    "steps": TrendMetric("steps", "get_user_summary", _user_summary_steps),
    "rhr": TrendMetric("rhr", "get_user_summary", _resting_hr),
    "hrv": TrendMetric("hrv", "get_hrv_data", _hrv_last_night),
    "sleep_hours": TrendMetric("sleep_hours", "get_sleep_data", _sleep_hours),
    "body_battery": TrendMetric("body_battery", "get_body_battery", _body_battery_high),
    "readiness": TrendMetric("readiness", "get_training_readiness", _readiness_score),
    "stress": TrendMetric("stress", "get_stress_data", _stress_avg),
}
