"""Real (sanitized) payloads must shape without error and stay within the token budget."""

import json
from pathlib import Path

import pytest

from pace.shape import apply
from pace.spec import REGISTRY

FIX = Path(__file__).parent / "fixtures"


def _load(spec):
    p = FIX / f"{spec.group}_{spec.name}.json"
    if not p.exists():
        pytest.skip(f"no fixture for {spec.command}")
    return json.loads(p.read_text())


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.command)
def test_default_shape_is_compact(spec) -> None:
    data = _load(spec)
    shaped, report = apply(data, series=spec.series, default_fields=spec.default_fields, fields="default",
                           bucket="auto", raw=False, span_s=86400)
    size = len(json.dumps(shaped, separators=(",", ":")))
    assert size <= 12_000, f"{spec.command} default payload is {size} bytes; curate default_fields or add a SeriesSpec"
    for s in spec.series:
        if s.path in report["reduced_series"]:
            assert len(_dig(shaped, s.path)["rows"]) <= 120


@pytest.mark.parametrize("spec", [s for s in REGISTRY if s.series], ids=lambda s: s.command)
def test_declared_series_exist_in_fixture(spec) -> None:
    data = _load(spec)
    present = [s.path for s in spec.series if _dig(data, s.path) is not None]
    assert present, f"none of {[s.path for s in spec.series]} found in fixture for {spec.command}"


def _dig(obj, path):
    for part in path.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj
