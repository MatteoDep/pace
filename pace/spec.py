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
                 "get_user_summary", TTL_LIVE, dated="single",
                 default_fields=(
                     "totalSteps",
                     "dailyStepGoal",
                     "totalDistanceMeters",
                     "totalKilocalories",
                     "activeKilocalories",
                     "bmrKilocalories",
                     "activeSeconds",
                     "highlyActiveSeconds",
                     "sedentarySeconds",
                     "sleepingSeconds",
                     "moderateIntensityMinutes",
                     "vigorousIntensityMinutes",
                     "intensityMinutesGoal",
                     "floorsAscended",
                     "userFloorsAscendedGoal",
                     "restingHeartRate",
                     "lastSevenDaysAvgRestingHeartRate",
                     "minHeartRate",
                     "maxHeartRate",
                     "averageStressLevel",
                     "maxStressLevel",
                     "stressQualifier",
                     "restStressDuration",
                     "lowStressDuration",
                     "mediumStressDuration",
                     "highStressDuration",
                     "bodyBatteryAtWakeTime",
                     "bodyBatteryHighestValue",
                     "bodyBatteryLowestValue",
                     "bodyBatteryMostRecentValue",
                     "bodyBatteryChargedValue",
                     "bodyBatteryDrainedValue",
                     "avgWakingRespirationValue",
                     "lowestRespirationValue",
                     "highestRespirationValue",
                     "averageSpo2",
                     "lowestSpo2",
                 )),
    EndpointSpec("wellness", "sleep", "Sleep duration, stages, overnight HR/respiration.",
                 "get_sleep_data", TTL_LIVE, dated="single",
                 default_fields=(
                     "dailySleepDTO.sleepTimeSeconds",
                     "dailySleepDTO.deepSleepSeconds",
                     "dailySleepDTO.lightSleepSeconds",
                     "dailySleepDTO.remSleepSeconds",
                     "dailySleepDTO.awakeSleepSeconds",
                     "dailySleepDTO.unmeasurableSleepSeconds",
                     "dailySleepDTO.napTimeSeconds",
                     "dailySleepDTO.sleepStartTimestampLocal",
                     "dailySleepDTO.sleepEndTimestampLocal",
                     "dailySleepDTO.averageRespirationValue",
                     "dailySleepDTO.lowestRespirationValue",
                     "dailySleepDTO.highestRespirationValue",
                     # Unverified: this device reports no sleep score or sleep stress;
                     # field names from garminconnect docs.
                     "dailySleepDTO.avgSleepStress",
                     "dailySleepDTO.sleepScores",
                     "restingHeartRate",
                     "bodyBatteryChange",
                     "sleepHeartRate",
                     "wellnessEpochRespirationDataDTOList",
                     "sleepStress",
                     "sleepBodyBattery",
                 ),
                 series=(SeriesSpec("sleepHeartRate", "records", "startGMT", "value"),
                         SeriesSpec("wellnessEpochRespirationDataDTOList", "records", "startTimeGMT", "respirationValue"),
                         SeriesSpec("sleepStress", "records", "startGMT", "value"),
                         SeriesSpec("sleepBodyBattery", "records", "startGMT", "value"))),
    EndpointSpec("wellness", "hr", "Heart rate through the day.", "get_heart_rates", TTL_LIVE,
                 dated="single",
                 default_fields=(
                     "restingHeartRate",
                     "lastSevenDaysAvgRestingHeartRate",
                     "minHeartRate",
                     "maxHeartRate",
                     "heartRateValues",
                 ),
                 series=(_pairs("heartRateValues"),)),
    EndpointSpec("wellness", "stress", "Stress level and body battery through the day.", "get_stress_data",
                 TTL_LIVE, dated="single",
                 default_fields=(
                     "avgStressLevel",
                     "maxStressLevel",
                     "stressValuesArray",
                     "bodyBatteryValuesArray",
                 ),
                 series=(_pairs("stressValuesArray"),
                         # [timestamp, status, level, version] -- the level is at index 2.
                         SeriesSpec("bodyBatteryValuesArray", "pairs", "0", "2"))),
    EndpointSpec("wellness", "body-battery", "Body battery (energy) through the day.", "get_body_battery",
                 TTL_LIVE, dated="range", unwrap="first",
                 default_fields=(
                     "charged",
                     "drained",
                     "bodyBatteryValuesArray",
                 ),
                 series=(_pairs("bodyBatteryValuesArray"),)),
    EndpointSpec("wellness", "hrv", "Overnight heart rate variability.", "get_hrv_data", TTL_LIVE,
                 dated="single",
                 # Unverified: account has no hrv data; field names from garminconnect docs.
                 default_fields=(
                     "hrvSummary.lastNightAvg",
                     "hrvSummary.lastNight5MinHigh",
                     "hrvSummary.weeklyAvg",
                     "hrvSummary.status",
                     "hrvSummary.feedbackPhrase",
                     "hrvSummary.baseline",
                     "hrvReadings",
                 ),
                 series=(SeriesSpec("hrvReadings", "records", "readingTimeGMT", "hrvValue"),)),
    EndpointSpec("wellness", "hydration", "Water intake for the day.", "get_hydration_data", TTL_LIVE, dated="single",
                 default_fields=(
                     "valueInML",
                     "goalInML",
                     "dailyAverageinML",
                     "sweatLossInML",
                     "activityIntakeInML",
                     "lastEntryTimestampLocal",
                 )),
    EndpointSpec("wellness", "spo2", "Blood oxygen saturation.", "get_spo2_data", TTL_LIVE, dated="single",
                 default_fields=(
                     "averageSpO2",
                     "lowestSpO2",
                     "latestSpO2",
                     "avgSleepSpO2",
                     "lastSevenDaysAvgSpO2",
                     "spO2HourlyAverages",
                 ),
                 series=(_pairs("spO2HourlyAverages"),)),
    # training
    EndpointSpec("training", "status", "Training status, load, VO2max.", "get_training_status", TTL_TRAINING,
                 dated="single",
                 default_fields=(
                     "mostRecentVO2Max",
                     "mostRecentTrainingStatus",
                     "mostRecentTrainingLoadBalance",
                     "heatAltitudeAcclimationDTO",
                 )),
    EndpointSpec("training", "readiness", "Training readiness score and contributors.", "get_training_readiness",
                 TTL_TRAINING, dated="single", unwrap="first",
                 # Unverified: account has no readiness data; field names from garminconnect docs.
                 default_fields=(
                     "score",
                     "level",
                     "feedbackShort",
                     "feedbackLong",
                     "sleepScore",
                     "sleepScoreFactorPercent",
                     "sleepScoreFactorFeedback",
                     "recoveryTime",
                     "recoveryTimeFactorPercent",
                     "recoveryTimeFactorFeedback",
                     "acuteLoad",
                     "acwrFactorPercent",
                     "acwrFactorFeedback",
                     "hrvFactorPercent",
                     "hrvFactorFeedback",
                     "hrvWeeklyAverage",
                     "sleepHistoryFactorPercent",
                     "sleepHistoryFactorFeedback",
                     "stressHistoryFactorPercent",
                     "stressHistoryFactorFeedback",
                     "timestampLocal",
                 )),
    EndpointSpec("training", "endurance", "Endurance score.", "get_endurance_score", TTL_TRAINING, dated="range",
                 default_fields=(
                     "enduranceScoreDTO",
                     "avg",
                     "max",
                 )),
    EndpointSpec("training", "fitness-age", "Fitness age.", "get_fitnessage_data", TTL_TRAINING, dated="single",
                 default_fields=(
                     "fitnessAge",
                     "achievableFitnessAge",
                     "previousFitnessAge",
                     "chronologicalAge",
                     "components",
                     "lastUpdated",
                 )),
    EndpointSpec("training", "hill-score", "Hill score.", "get_hill_score", TTL_TRAINING, dated="range",
                 default_fields=(
                     "hillScoreDTOList",
                     "maxScore",
                     "periodAvgScore",
                 )),
    EndpointSpec("training", "race-predictions", "Predicted race times (5k, 10k, half, marathon).",
                 "get_race_predictions", TTL_TRAINING,
                 default_fields=(
                     "calendarDate",
                     "time5K",
                     "time10K",
                     "timeHalfMarathon",
                     "timeMarathon",
                 )),
    # device
    EndpointSpec("device", "list", "Registered devices.", "get_devices", TTL_STATIC,
                 default_fields=(
                     "productDisplayName",
                     "applicationKey",
                     "deviceStatus",
                     "currentFirmwareVersion",
                     "primary",
                     "primaryActivityTrackerIndicator",
                     "registeredDate",
                     # Capability flags explain why a metric is missing for this account.
                     "bodyBatteryCapable",
                     "hrvStatusCapable",
                     "respirationCapable",
                     "pulseOxSleepCapable",
                     "sleepScoreCapable",
                     "trainingStatusCapable",
                     "trainingReadinessCapable",
                     "hillScoreAndEnduranceScoreCapable",
                     "vo2MaxRunCapable",
                     "racePredictionsRunCapable",
                 )),
    EndpointSpec("device", "last-used", "Most recently synced device.", "get_device_last_used", TTL_STATIC,
                 default_fields=(
                     "lastUsedDeviceName",
                     "lastUsedDeviceApplicationKey",
                     "lastUsedDeviceUploadTime",
                 )),
    EndpointSpec("device", "settings", "Settings for one device.", "get_device_settings", TTL_STATIC,
                 id_arg="DEVICE_ID", id_type=str,
                 default_fields=(
                     "deviceId",
                     "measurementUnits",
                     "allUnits",
                     "timeFormat",
                     "startOfWeek",
                     "dataRecording",
                     "opticalHeartRateEnabled",
                     "intensityMinutesCalcMethod",
                     "moderateIntensityMinutesHrZone",
                     "vigorousIntensityMinutesHrZone",
                     "activityTracking",
                     "autoActivityDetect",
                 )),
    EndpointSpec("device", "alarms", "Configured alarms.", "get_device_alarms", TTL_STATIC,
                 default_fields=(
                     "alarmTime",
                     "alarmDays",
                     "alarmMode",
                     "alarmSound",
                     "alarmMessage",
                     "enabled",
                 )),
    EndpointSpec("device", "solar", "Solar charging data for one device.", "get_device_solar_data", TTL_STATIC,
                 dated="range", id_arg="DEVICE_ID", id_type=str,
                 default_fields=(
                     "solarDailyDataDTOs",
                     "solarUtilization",
                 )),
    # gear
    EndpointSpec("gear", "list", "All gear items.", "get_gear", TTL_STATIC, resolver="upn",
                 default_fields=(
                     # `uuid` is the identifier `gear stats` takes.
                     "uuid",
                     "customMakeModel",
                     "gearMakeName",
                     "gearModelName",
                     "gearTypeName",
                     "gearStatusName",
                     "dateBegin",
                     "dateEnd",
                     "maximumMeters",
                 )),
    EndpointSpec("gear", "stats", "Usage stats for one gear item.", "get_gear_stats", TTL_STATIC,
                 id_arg="GEAR_UUID", id_type=str,
                 default_fields=(
                     "uuid",
                     "totalDistance",
                     "totalActivities",
                     "createDate",
                 )),
    EndpointSpec("gear", "defaults", "Default gear per activity type.", "get_gear_defaults", TTL_STATIC, resolver="upn"),
    # goal
    EndpointSpec("goal", "list", "Active goals.", "get_goals", TTL_STATIC),
    EndpointSpec("goal", "personal-records", "Personal records.", "get_personal_record", TTL_STATIC,
                 default_fields=(
                     "typeId",
                     "prTypeLabelKey",
                     "value",
                     "activityType",
                     "activityId",
                     "activityName",
                     "activityStartDateTimeLocalFormatted",
                 )),
    # profile
    EndpointSpec("profile", "settings", "User profile settings incl. unit system.", "get_userprofile_settings",
                 TTL_PROFILE,
                 default_fields=(
                     "measurementSystem",
                     "timeZone",
                     "preferredLocale",
                     "numberFormat",
                     "firstDayOfWeek.dayName",
                     "timeFormat.formatKey",
                     "dateFormat.formatKey",
                     "heartRateFormat.formatKey",
                     "powerFormat.formatKey",
                     "hydrationMeasurementUnit",
                     "availableTrainingDays",
                     "preferredLongTrainingDays",
                 )),
)


def specs_for(group: str) -> list[EndpointSpec]:
    return [s for s in REGISTRY if s.group == group]
