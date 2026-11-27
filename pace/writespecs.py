"""Write specs shared by the CLI write commands and `requests apply`, so both send the same payloads
and drop the same cache keys. Callers check their input first; these builders only assemble."""

from typing import Any

from pace.effort import FEEL_LEVELS
from pace.writes import WriteSpec

_GEAR_METHODS = {"link": "add_gear_to_activity", "unlink": "remove_gear_from_activity"}

#: A day-view weigh-in within this many grams of the weight written is taken as that weigh-in.
WEIGHT_MATCH_G = 100


def gear_link_spec(verb: str, activity_id: int, uuid: str) -> WriteSpec:
    """Link (`verb` "link") or unlink ("unlink") one gear item; `uuid` is already normalized."""
    method = _GEAR_METHODS[verb]
    # Gear-side reads are keyed by the uuid as typed (any case), and Garmin may touch another
    # shoe on link: drop the whole gear-side prefixes, they are a handful of rows.
    return WriteSpec(resource="activity_gear", action=verb, method=method, args=(uuid, activity_id),
                     payload={"method": method, "gearUUID": uuid, "activity_id": activity_id},
                     echo={"activity_id": activity_id, "gear_uuid": uuid},
                     invalidate=(f"get_activity_gear:{activity_id}", "get_gear_activities:", "get_gear_stats:"))


def effort_spec(activity_id: int, rpe: int | None, feel: str | None) -> WriteSpec:
    """Set RPE (1-10) and/or feel (a canonical label) on one activity; only the given fields are sent."""
    summary: dict[str, int] = {}
    echo: dict[str, Any] = {"activity_id": activity_id}
    if rpe is not None:
        summary["directWorkoutRpe"] = rpe * 10
        echo["rpe"] = rpe
    if feel is not None:
        summary["directWorkoutFeel"] = FEEL_LEVELS[feel]
        echo["feel"] = feel
    path = f"/activity-service/activity/{activity_id}"
    body = {"activityId": activity_id, "summaryDTO": summary}
    # Prefix invalidation also drops ids that extend this one; harmless over-invalidation.
    return WriteSpec(resource="activity_effort", action="update", method="connectapi", args=(path,),
                     kwargs={"method": "PUT", "json": body}, payload={"path": path, "json": body}, echo=echo,
                     invalidate=(f"get_activity:{activity_id}", f"zones:get_activity:{activity_id}"))


def weigh_in_spec(kg: float, iso_date: str, extras: dict[str, float] | None = None,
                  echo: dict[str, Any] | None = None) -> WriteSpec:
    """A weigh-in at 12:00 local time of `iso_date`, with body composition when `extras` has any."""
    ts = f"{iso_date}T12:00:00"
    if extras:
        method, kwargs = "add_body_composition", {"timestamp": ts, "weight": kg, **extras}
    else:
        method, kwargs = "add_weigh_in", {"weight": kg, "unitKey": "kg", "timestamp": ts}
    return WriteSpec(resource="weight", action="create", method=method, kwargs=kwargs,
                     payload={"method": method, **kwargs},
                     echo=echo if echo is not None else {"weight_kg": kg, "date": iso_date},
                     invalidate=("get_weigh_ins:", f"get_daily_weigh_ins:{iso_date}"),
                     id_of=lambda r: r.get("samplePk") if isinstance(r, dict) else None)


def manual_weigh_in_ids(day_view: Any, kg: float) -> list[Any]:
    """samplePks of the day view's (`get_daily_weigh_ins`) entries that match `kg`.

    Garmin stores grams, so a match is within WEIGHT_MATCH_G; only entries Garmin attributes to a
    manual entry (or to no source) count, so a scale sync on the same day never matches."""
    entries = day_view.get("dateWeightList") if isinstance(day_view, dict) else None
    if not isinstance(entries, list):
        return []
    pks = []
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("samplePk"), (int, float)):
            continue
        grams = e.get("weight")
        if not isinstance(grams, (int, float)) or abs(float(grams) - kg * 1000) > WEIGHT_MATCH_G:
            continue
        source = e.get("sourceType")
        if source is not None and source != "MANUAL":
            continue
        pks.append(e["samplePk"])
    return pks
