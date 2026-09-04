"""Profile commands: registry-driven `settings` plus the composite `show`."""

import asyncio

from pace.cache import Cache, CacheTracker
from pace.commands._helpers import cached_call
from pace.envelope import PaceError, Result, fail, run
from pace.factory import BUCKET_OPT, FIELDS_OPT, NO_CACHE_OPT, RAW_OPT, build_group, shape_args
from pace.shape import apply

app = build_group("profile")
_TTL = 604800


@app.command("show")
def show(fields: str = FIELDS_OPT, bucket: str = BUCKET_OPT, raw: bool = RAW_OPT,
         no_cache: bool = NO_CACHE_OPT) -> None:
    """Name, profile and unit system in one call."""
    try:
        f, b, echo = shape_args(fields, bucket, raw)
    except PaceError as err:
        fail("profile show", err)

    async def work() -> Result:
        # Mirrors factory.fetch_spec's --no-cache semantics: always open the cache so a
        # fresh result is still written, just skip the read on this call via `refresh`.
        cache = Cache()
        tracker = CacheTracker()
        try:
            name, profile, units = await asyncio.gather(
                cached_call("get_full_name", cache=cache, ttl=_TTL, tracker=tracker, refresh=no_cache),
                cached_call("get_user_profile", cache=cache, ttl=_TTL, tracker=tracker, refresh=no_cache),
                cached_call("get_unit_system", cache=cache, ttl=_TTL, tracker=tracker, refresh=no_cache),
            )
        finally:
            cache.close()
        data = {"fullName": name, "profile": profile, "unitSystem": units}
        shaped, report = apply(data, series=(), default_fields=(), fields=f, bucket=b, raw=raw, span_s=0)
        return Result(shaped, tracker.report(), report)

    run("profile show", echo, work)
