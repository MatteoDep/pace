"""Sport families: Garmin activity type keys grouped into the sports a plan session can have."""

SESSION_SPORTS = ("running", "cycling", "swimming", "walking", "hiking", "strength", "mobility", "other")
PUSHABLE = ("running", "cycling", "swimming", "walking", "hiking")
UNTRACKED = ("strength", "mobility", "other")  # past unmatched -> "unconfirmed", not "missed"

_FAMILIES = {
    "running": ("running", "treadmill_running", "trail_running", "track_running", "indoor_running",
                "virtual_run", "street_running", "ultra_run"),
    "cycling": ("cycling", "indoor_cycling", "road_biking", "mountain_biking", "gravel_cycling", "virtual_ride"),
    "swimming": ("lap_swimming", "open_water_swimming"),
    "walking": ("walking",),
    "hiking": ("hiking",),
    "strength": ("strength_training",),
    "mobility": ("yoga", "pilates", "breathwork"),
}
_BY_KEY = {key: fam for fam, keys in _FAMILIES.items() for key in keys}


def family(type_key: str | None) -> str:
    """Garmin activityType.typeKey -> one of SESSION_SPORTS (`other` for unknown or missing)."""
    if type_key is None:
        return "other"
    if type_key.startswith("e_bike_"):
        return "cycling"
    return _BY_KEY.get(type_key, "other")
