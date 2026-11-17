"""Suite-wide guard: no test may read or write the real $PACE_HOME/fit (FIT cache and no_rr.json)."""

import pytest

from pace import config, fit

REAL_PACE_HOME = config.PACE_HOME
_fit_dir = fit.fit_dir


def guarded_fit_dir():
    if config.PACE_HOME == REAL_PACE_HOME:
        raise AssertionError("test reached the real PACE_HOME fit dir: monkeypatch pace.config.PACE_HOME")
    return _fit_dir()


@pytest.fixture(autouse=True)
def _no_real_fit_dir(monkeypatch):
    monkeypatch.setattr(fit, "fit_dir", guarded_fit_dir)
