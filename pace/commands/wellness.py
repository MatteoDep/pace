"""Wellness commands: generated from pace.spec.REGISTRY, plus the hand-written `sleep-range`."""

from datetime import date


from pace.charts import sources
from pace.cache import Cache, CacheTracker
from pace.dates import resolve_range
from pace.envelope import PaceError, Result, fail, run
from pace.factory import DAYS_OPT, FROM_OPT, NO_CACHE_OPT, TO_OPT, build_group

app = build_group("wellness")


@app.command("sleep-range")
def sleep_range(from_: str | None = FROM_OPT, to: str = TO_OPT, days: int = DAYS_OPT,
                no_cache: bool = NO_CACHE_OPT) -> None:
    """Nightly sleep stages, resting HR, HRV and score for a date range (one Garmin call per 28 days)."""
    cmd = "wellness sleep-range"
    try:
        start, end = resolve_range(from_, to, days)
    except PaceError as err:
        fail(cmd, err)

    async def work() -> Result:
        cache = Cache()
        tracker = CacheTracker()
        try:
            rows = await sources.sleep_stats(start, end, today=date.today(), cache=cache, tracker=tracker,
                                             refresh=no_cache)
        finally:
            cache.close()
        return Result(rows, tracker.report())

    run(cmd, {"from": start.isoformat(), "to": end.isoformat()}, work)
