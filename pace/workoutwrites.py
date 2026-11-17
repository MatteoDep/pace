"""Garmin workout write specs, shared by `workout push/schedule/delete` and `plan push/unpush`."""

from typing import Any

from pace.workout import build_workout
from pace.writes import WriteSpec

#: Cache key prefix of every calendar read (`cached_call("connectapi", "/calendar-service/...")`).
CALENDAR_PREFIX = "connectapi:/calendar-service/"


def _id(key: str) -> Any:
    return lambda r: r.get(key) if isinstance(r, dict) else None


def upload_spec(dsl: dict[str, Any]) -> WriteSpec:
    """Create a workout from the DSL; raises bad_args for an invalid DSL."""
    payload = build_workout(dsl)
    return WriteSpec(resource="workout", action="create", method="upload_workout", args=(payload,),
                     payload=payload, echo=dsl, invalidate=("get_workouts:",), id_of=_id("workoutId"))


def schedule_spec(workout_id: int | str, iso: str) -> WriteSpec:
    """Put a workout on the calendar on `iso` (YYYY-MM-DD); a str id is a dry-run placeholder."""
    path = f"/workout-service/schedule/{workout_id}"
    body = {"date": iso}
    return WriteSpec(resource="workout", action="create", method="connectapi", args=(path,),
                     kwargs={"method": "POST", "json": body}, payload={"path": path, "json": body},
                     echo={"workout_id": workout_id, "date": iso}, invalidate=(CALENDAR_PREFIX,),
                     id_of=_id("workoutScheduleId"))


def delete_spec(workout_id: int) -> WriteSpec:
    """Delete a workout; Garmin drops its calendar schedules with it."""
    path = f"/workout-service/workout/{workout_id}"
    return WriteSpec(resource="workout", action="delete", method="connectapi", args=(path,),
                     kwargs={"method": "DELETE"}, payload={"path": path}, echo={"workout_id": workout_id},
                     invalidate=("get_workouts:", f"get_workout_by_id:{workout_id}", CALENDAR_PREFIX))
