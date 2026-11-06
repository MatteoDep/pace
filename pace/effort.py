"""Perceived exertion (RPE) and feel: the user's scale vs the value Garmin stores.

Garmin stores RPE x10 (the app's 1-10 is 10..100) and feel as five steps of 25.
Both live in an activity's `summaryDTO` as `directWorkoutRpe` / `directWorkoutFeel`.
"""

from typing import Any

from pace.envelope import PaceError

#: Feel label -> stored value, weakest first.
FEEL_LEVELS: dict[str, int] = {"very-weak": 0, "weak": 25, "normal": 50, "strong": 75, "very-strong": 100}
_FEEL_BY_STORED = {v: k for k, v in FEEL_LEVELS.items()}


def _number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def parse_rpe(value: int) -> int:
    if not 1 <= value <= 10:
        raise PaceError("bad_args", f"--rpe must be 1-10: {value}")
    return value * 10


def parse_feel(label: str) -> tuple[str, int]:
    key = label.strip().lower().replace("_", "-")
    if key not in FEEL_LEVELS:
        raise PaceError("bad_args", f"--feel must be one of {', '.join(FEEL_LEVELS)}: {label!r}")
    return key, FEEL_LEVELS[key]


def rpe_from_stored(v: Any) -> int | float | None:
    if not _number(v):
        return None
    return int(v) // 10 if v % 10 == 0 else v / 10


def feel_from_stored(v: Any) -> str | None:
    return _FEEL_BY_STORED.get(v) if _number(v) else None


def with_effort(activity: Any) -> Any:
    """Copy of an activity payload with top-level `rpe` (1-10) and `feel` (label) added."""
    if not isinstance(activity, dict):
        return activity
    summary = activity.get("summaryDTO")
    summary = summary if isinstance(summary, dict) else {}
    return {**activity, "rpe": rpe_from_stored(summary.get("directWorkoutRpe")),
            "feel": feel_from_stored(summary.get("directWorkoutFeel"))}
