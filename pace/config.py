"""Paths, environment-derived settings, and user TOML config."""

import os
import tomllib
from functools import cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict

PACE_HOME = Path(os.getenv("PACE_HOME", "~/.pace")).expanduser()
CACHE_DB = PACE_HOME / "cache.db"
CONFIG_FILE = PACE_HOME / "config.toml"
TOKEN_STORE = Path(os.getenv("GARMINTOKENS", "~/.garminconnect")).expanduser()


def ensure_home() -> None:
    """Create PACE_HOME if missing."""
    PACE_HOME.mkdir(parents=True, exist_ok=True)


class PaceConfig(BaseModel):
    """User-tunable defaults loaded from ~/.pace/config.toml."""

    model_config = ConfigDict(extra="allow")

    default_format: str | None = None
    cache_ttl_overrides: dict[str, int] = {}
    dashboard_panels: list[str] = []


@cache
def load_config() -> PaceConfig:
    """Read and cache the user config. Missing file -> defaults."""
    if not CONFIG_FILE.exists():
        return PaceConfig()
    try:
        with CONFIG_FILE.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return PaceConfig()
    return PaceConfig.model_validate(data)
