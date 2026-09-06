"""Real (sanitized) payloads must shape without error and stay within the token budget."""

import json
from pathlib import Path

import pytest

from pace.shape import apply
from pace.spec import REGISTRY

FIX = Path(__file__).parent / "fixtures"
MISSING = object()


def _items(data) -> list:
    """Dated commands hand `apply` a list of per-day payloads; the fixtures record that
    list verbatim. Undated commands hand it a single payload."""
    return data if isinstance(data, list) else [data]


def _load(spec):
    p = FIX / f"{spec.group}_{spec.name}.json"
    if not p.exists():
        pytest.skip(f"no fixture for {spec.command}")
    data = json.loads(p.read_text())
    if not _has_payload(data):
        pytest.skip(f"fixture for {spec.command} carries no data (account or device lacks it)")
    return data


def _has_payload(data) -> bool:
    """The recorded day is empty when Garmin returned nothing: the runner still stamps
    `date`, and wraps a non-dict payload (usually null) as {"value": ...}.

    A fixture whose only surviving values are the recorder's "REDACTED" placeholders
    carries no real payload either -- shaping it would pass vacuously."""
    for item in _items(data):
        if not isinstance(item, dict):
            return True
        if any(v is not None and v != "REDACTED" for k, v in item.items() if k != "date"):
            return True
    return False


@pytest.mark.parametrize("spec", REGISTRY, ids=lambda s: s.command)
def test_default_shape_is_compact(spec) -> None:
    data = _load(spec)
    shaped, report = apply(data, series=spec.series, default_fields=spec.default_fields, fields="default",
                           bucket="auto", raw=False, span_s=86400)
    size = len(json.dumps(shaped, separators=(",", ":")))
    assert size <= 12_000, f"{spec.command} default payload is {size} bytes; curate default_fields or add a SeriesSpec"
    for s in spec.series:
        if s.path not in report["reduced_series"]:
            continue
        for item in _items(shaped):
            table = _dig(item, s.path)
            if table is not MISSING:
                assert len(table["rows"]) <= 120


@pytest.mark.parametrize("spec", [s for s in REGISTRY if s.series], ids=lambda s: s.command)
def test_declared_series_exist_in_fixture(spec) -> None:
    data = _load(spec)
    # A key present but null means the account has no samples that day -- the path is
    # still proven right, which is what this test is about.
    present = [s.path for s in spec.series
               if any(_dig(item, s.path) is not MISSING for item in _items(data))]
    assert present, f"none of {[s.path for s in spec.series]} found in fixture for {spec.command}"


@pytest.mark.parametrize("spec", [s for s in REGISTRY if s.default_fields], ids=lambda s: s.command)
def test_series_are_kept_by_default_fields(spec) -> None:
    """A bucketed series that `default_fields` forgets is silently dropped from the
    default payload -- the most likely curation mistake."""
    data = _load(spec)
    for s in spec.series:
        if any(_dig(item, s.path) not in (MISSING, None) for item in _items(data)):
            assert s.path in spec.default_fields, f"{spec.command} default_fields drops series {s.path}"


def _dig(obj, path):
    for part in path.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return MISSING
        obj = obj[part]
    return obj
