"""Shared runner for every Garmin write: dry-run, call, invalidate, report."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import typer

from pace.api import safe_call
from pace.cache import Cache
from pace.envelope import PaceError, Result, log

#: Shared by every write command, so it lives with the runner rather than in one of them.
DRY_RUN_OPT = typer.Option(False, "--dry-run", help="Show the exact Garmin payload; make no network call.")


@dataclass(frozen=True)
class WriteSpec:
    resource: str
    action: str
    method: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    payload: Any = None
    echo: Any = None
    invalidate: tuple[str, ...] = ()
    id_of: Callable[[Any], Any] | None = None


def invalidate(prefixes: tuple[str, ...]) -> None:
    """Drop cache rows under each prefix after a write.

    The write already landed on Garmin, so a cache failure must never report it as
    `ok: false` -- a stale local row is the lesser evil, and it is logged.
    """
    if not prefixes:
        return
    try:
        cache = Cache()
        try:
            for prefix in prefixes:
                log(f"invalidate {prefix}* -> {cache.invalidate(prefix)} rows")
        finally:
            cache.close()
    except Exception as exc:  # noqa: BLE001 -- see above; never fatal
        log(f"invalidate failed: {exc}")


async def run_write(spec: WriteSpec, *, dry_run: bool) -> Result:
    if dry_run:
        return Result({"dry_run": True, "payload": spec.payload})
    ok, resp, err = await safe_call(spec.method, *spec.args, **spec.kwargs)
    if not ok:
        assert err is not None
        raise PaceError.from_api(err)
    invalidate(spec.invalidate)
    ident = spec.id_of(resp) if spec.id_of is not None and resp is not None else None
    return Result({"action": spec.action, "resource": spec.resource, "id": ident, "echo": spec.echo})
