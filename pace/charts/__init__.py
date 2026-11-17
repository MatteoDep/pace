"""Chart registry: kind names and default output paths. Imports no matplotlib."""

from datetime import date
from pathlib import Path

from pace import config

KINDS: tuple[str, ...] = ("volume", "plan", "recovery", "sleep")


def default_out(kind: str, *, start: date | None, end: date | None, metric: str | None = None,
                sport: str | None = None, activity_id: int | None = None) -> Path:
    """$PACE_HOME/charts/<name>.png: volume-<metric>[-all]-<from>-<to>, activity-<id> or <kind>-<from>-<to>."""
    if kind == "activity":
        name = f"activity-{activity_id}"
    elif kind == "volume":
        name = f"volume-{metric}{'-all' if sport == 'all' else ''}-{start}-{end}"
    else:
        name = f"{kind}-{start}-{end}"
    return config.PACE_HOME / "charts" / f"{name}.png"
