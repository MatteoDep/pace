"""Shared runner for every Garmin write: dry-run, call, invalidate, report."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pace.api import safe_call
from pace.cache import Cache
from pace.envelope import PaceError, Result, log


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


async def run_write(spec: WriteSpec, *, dry_run: bool) -> Result:
    if dry_run:
        return Result({"dry_run": True, "payload": spec.payload})
    ok, resp, err = await safe_call(spec.method, *spec.args, **spec.kwargs)
    if not ok:
        assert err is not None
        raise PaceError.from_api(err)
    if spec.invalidate:
        cache = Cache()
        try:
            for prefix in spec.invalidate:
                log(f"invalidate {prefix}* -> {cache.invalidate(prefix)} rows")
        finally:
            cache.close()
    ident = spec.id_of(resp) if spec.id_of is not None and resp is not None else None
    return Result({"action": spec.action, "resource": spec.resource, "id": ident, "echo": spec.echo})
