"""Declarative endpoint registry: one EndpointSpec per simple Garmin read."""

from dataclasses import dataclass
from typing import Literal

from pace.shape import SeriesSpec

__all__ = ["EndpointSpec", "SeriesSpec", "REGISTRY", "GROUPS", "specs_for"]

TTL_LIVE = 300        # today's wellness
TTL_TRAINING = 3600
TTL_STATIC = 86400
TTL_PROFILE = 604800


@dataclass(frozen=True)
class EndpointSpec:
    group: str
    name: str
    help: str
    method: str
    ttl: int
    dated: Literal[None, "single", "range"] = None
    id_arg: str | None = None
    id_type: type = int
    resolver: Literal[None, "upn"] = None
    default_fields: tuple[str, ...] = ()
    series: tuple[SeriesSpec, ...] = ()
    unwrap: Literal[None, "first"] = None

    @property
    def command(self) -> str:
        return f"{self.group} {self.name}"


GROUPS: dict[str, str] = {
    "wellness": "Daily health metrics.",
    "training": "Training load and performance metrics.",
    "device": "Registered Garmin devices.",
    "gear": "Gear inventory and usage.",
    "goal": "User goals and personal records.",
    "profile": "User profile information.",
}

_pairs = lambda path: SeriesSpec(path, "pairs")  # noqa: E731

REGISTRY: tuple[EndpointSpec, ...] = (
    # wellness
    EndpointSpec("wellness", "summary", "Daily summary: steps, calories, distance, floors, resting HR.",
                 "get_user_summary", TTL_LIVE, dated="single"),
    EndpointSpec("wellness", "sleep", "Sleep duration, stages, overnight HR/respiration.",
                 "get_sleep_data", TTL_LIVE, dated="single",
                 series=(SeriesSpec("sleepHeartRate", "records", "startGMT", "value"),
                         SeriesSpec("wellnessEpochRespirationDataDTOList", "records", "startTimeGMT", "respirationValue"),
                         SeriesSpec("sleepStress", "records", "startGMT", "value"),
                         SeriesSpec("sleepBodyBattery", "records", "startGMT", "value"))),
    EndpointSpec("wellness", "hr", "Heart rate through the day.", "get_heart_rates", TTL_LIVE,
                 dated="single", series=(_pairs("heartRateValues"),)),
    EndpointSpec("wellness", "stress", "Stress level and body battery through the day.", "get_stress_data",
                 TTL_LIVE, dated="single", series=(_pairs("stressValuesArray"), _pairs("bodyBatteryValuesArray"))),
    EndpointSpec("wellness", "body-battery", "Body battery (energy) through the day.", "get_body_battery",
                 TTL_LIVE, dated="range", unwrap="first", series=(_pairs("bodyBatteryValuesArray"),)),
    EndpointSpec("wellness", "hrv", "Overnight heart rate variability.", "get_hrv_data", TTL_LIVE,
                 dated="single", series=(SeriesSpec("hrvReadings", "records", "readingTimeGMT", "hrvValue"),)),
    EndpointSpec("wellness", "hydration", "Water intake for the day.", "get_hydration_data", TTL_LIVE, dated="single"),
    EndpointSpec("wellness", "spo2", "Blood oxygen saturation.", "get_spo2_data", TTL_LIVE, dated="single",
                 series=(_pairs("spO2HourlyAverages"),)),
    # training
    EndpointSpec("training", "status", "Training status, load, VO2max.", "get_training_status", TTL_TRAINING, dated="single"),
    EndpointSpec("training", "readiness", "Training readiness score and contributors.", "get_training_readiness",
                 TTL_TRAINING, dated="single", unwrap="first"),
    EndpointSpec("training", "endurance", "Endurance score.", "get_endurance_score", TTL_TRAINING, dated="range"),
    EndpointSpec("training", "fitness-age", "Fitness age.", "get_fitnessage_data", TTL_TRAINING, dated="single"),
    EndpointSpec("training", "hill-score", "Hill score.", "get_hill_score", TTL_TRAINING, dated="range"),
    EndpointSpec("training", "race-predictions", "Predicted race times (5k, 10k, half, marathon).",
                 "get_race_predictions", TTL_TRAINING),
    # device
    EndpointSpec("device", "list", "Registered devices.", "get_devices", TTL_STATIC),
    EndpointSpec("device", "last-used", "Most recently synced device.", "get_device_last_used", TTL_STATIC),
    EndpointSpec("device", "settings", "Settings for one device.", "get_device_settings", TTL_STATIC,
                 id_arg="DEVICE_ID", id_type=str),
    EndpointSpec("device", "alarms", "Configured alarms.", "get_device_alarms", TTL_STATIC),
    EndpointSpec("device", "solar", "Solar charging data for one device.", "get_device_solar_data", TTL_STATIC,
                 dated="range", id_arg="DEVICE_ID", id_type=str),
    # gear
    EndpointSpec("gear", "list", "All gear items.", "get_gear", TTL_STATIC, resolver="upn"),
    EndpointSpec("gear", "stats", "Usage stats for one gear item.", "get_gear_stats", TTL_STATIC,
                 id_arg="GEAR_UUID", id_type=str),
    EndpointSpec("gear", "defaults", "Default gear per activity type.", "get_gear_defaults", TTL_STATIC, resolver="upn"),
    # goal
    EndpointSpec("goal", "list", "Active goals.", "get_goals", TTL_STATIC),
    EndpointSpec("goal", "personal-records", "Personal records.", "get_personal_record", TTL_STATIC),
    # profile
    EndpointSpec("profile", "settings", "User profile settings incl. unit system.", "get_userprofile_settings", TTL_PROFILE),
)


def specs_for(group: str) -> list[EndpointSpec]:
    return [s for s in REGISTRY if s.group == group]
