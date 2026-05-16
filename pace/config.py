"""Paths and environment-derived settings."""

import os
from pathlib import Path

PACE_HOME = Path(os.getenv("PACE_HOME", "~/.pace")).expanduser()
CACHE_DB = PACE_HOME / "cache.db"
TOKEN_STORE = Path(os.getenv("GARMINTOKENS", "~/.garminconnect")).expanduser()


def ensure_home() -> None:
    """Create PACE_HOME if missing."""
    PACE_HOME.mkdir(parents=True, exist_ok=True)
