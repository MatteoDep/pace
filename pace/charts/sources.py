"""Health data readers shared by `wellness sleep-range` and the recovery and sleep charts."""

from datetime import date
from typing import Any

from pace.cache import ttl_for_day
from pace.commands._helpers import cached_call

#: Garmin refuses a sleep-stats range over 28 days inclusive (HTTP 400).
SLEEP_CHUNK_DAYS = 28
_TODAY_TTL = 3600
_PATH = "/sleep-service/stats/sleep/daily/{a}/{b}"
_FIELDS = {"sleep_s": "totalSleepTimeInSeconds", "deep_s": "deepTime", "light_s": "lightTime", "rem_s": "remTime",
           "awake_s": "awakeTime", "rhr": "restingHeartRate", "avg_hr": "avgHeartRate",
           "hrv": "avgOvernightHrv", "hrv_7d": "hrv7dAverage", "score": "sleepScore"}


def sleep_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """Fixed 28-day windows anchored on the ordinal that cover start..end; only the last is clipped, at end.

    A window may begin before `start` (callers filter the rows), so the keys do not depend on `start`
    and a repeated read asks for the same (a, b) and hits the cache.
    """
    chunks: list[tuple[date, date]] = []
    if start > end:
        return chunks
    k = start.toordinal() // SLEEP_CHUNK_DAYS
    while k * SLEEP_CHUNK_DAYS <= end.toordinal():
        chunks.append((date.fromordinal(k * SLEEP_CHUNK_DAYS),
                       min(end, date.fromordinal((k + 1) * SLEEP_CHUNK_DAYS - 1))))
        k += 1
    return chunks


async def sleep_stats(start: date, end: date, *, today: date, cache: Any, tracker: Any, refresh: bool) -> list[dict]:
    """One row per date (sorted, deduplicated) from the sleep-stats endpoint; future days are never requested."""
    end = min(end, today)
    rows: dict[str, dict] = {}
    for a, b in sleep_chunks(start, end):
        payload = await cached_call("connectapi", _PATH.format(a=a, b=b), cache=cache,
                                    ttl=ttl_for_day(b, _TODAY_TTL, today), tracker=tracker, refresh=refresh)
        for item in (payload or {}).get("individualStats") or []:
            day = item.get("calendarDate")
            if not isinstance(day, str) or not start.isoformat() <= day <= end.isoformat():
                continue
            values = item.get("values") or {}
            rows[day] = {"date": day, **{k: values.get(v) for k, v in _FIELDS.items()}}
    return [rows[d] for d in sorted(rows)]


async def weigh_ins(start: date, end: date, *, today: date, cache: Any, tracker: Any, refresh: bool) -> list[dict]:
    """The latest weigh-in of each day in the range as {"date", "kg"}, sorted; one Garmin call."""
    end = min(end, today)
    if start > end:
        return []
    payload = await cached_call("get_weigh_ins", start.isoformat(), end.isoformat(), cache=cache, ttl=ttl_for_day(end, _TODAY_TTL, today),
                                tracker=tracker, refresh=refresh)
    rows: dict[str, float] = {}
    for item in (payload or {}).get("dailyWeightSummaries") or []:
        day = item.get("summaryDate")
        grams = (item.get("latestWeight") or {}).get("weight")
        if isinstance(day, str) and start.isoformat() <= day <= end.isoformat() and isinstance(grams, (int, float)):
            rows[day] = round(grams / 1000, 3)
    return [{"date": d, "kg": rows[d]} for d in sorted(rows)]
