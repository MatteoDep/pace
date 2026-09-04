"""Pruning, field selection, and time-series bucketing."""

import pytest

from pace.envelope import PaceError
from pace.shape import (
    SeriesSpec,
    apply,
    bucket_series,
    choose_bucket,
    extract_points,
    parse_bucket,
    parse_fields,
    prune,
    select_fields,
    tz_offset_s,
)

MS = 1000
HOUR = 3600 * MS


def test_prune_removes_empties_recursively() -> None:
    src = {"a": None, "b": "", "c": [], "d": {}, "e": {"f": None, "g": 0}, "h": [None, 1, {}], "i": False}
    assert prune(src) == {"e": {"g": 0}, "h": [1], "i": False}


def test_parse_fields() -> None:
    assert parse_fields("default") == "default"
    assert parse_fields("all") == "all"
    assert parse_fields("a, b.c") == ["a", "b.c"]


def test_select_fields_dotted_and_missing() -> None:
    src = {"a": 1, "b": {"c": 2, "d": 3}, "e": 4}
    assert select_fields(src, ["a", "b.c", "zzz", "b.nope"]) == {"a": 1, "b": {"c": 2}}


def test_choose_bucket_ladder() -> None:
    assert choose_bucket(span_s=3600, n_samples=50) == "none"
    assert choose_bucket(span_s=86400, n_samples=1440) == "15m"   # 96 rows
    assert choose_bucket(span_s=7 * 86400, n_samples=10000) == "3h"  # 56 rows
    assert choose_bucket(span_s=30 * 86400, n_samples=10000) == "1d"  # 30 rows; beyond ladder stays 1d


def test_extract_points_pairs_and_records() -> None:
    pairs = [[HOUR, 60], [HOUR + MS, None], [HOUR + 2 * MS, 62]]
    assert extract_points(pairs, SeriesSpec("x", "pairs")) == [(HOUR, 60.0), (HOUR + 2 * MS, 62.0)]
    recs = [{"startGMT": "1970-01-01T01:00:00.0", "value": 60}, {"startGMT": "1970-01-01T01:00:01.0", "value": 61}]
    got = extract_points(recs, SeriesSpec("x", "records", t_key="startGMT", v_key="value"))
    assert got == [(HOUR, 60.0), (HOUR + MS, 61.0)]


def test_bucket_series_math_and_local_time() -> None:
    pts = [(HOUR, 60.0), (HOUR + 60 * MS, 70.0), (HOUR + 15 * 60 * MS, 50.0)]
    out = bucket_series(pts, bucket_s=900, tz_offset_s=3600)
    assert out["cols"] == ["t", "avg", "min", "max", "n"]
    assert out["rows"] == [["1970-01-01T02:00", 65.0, 60.0, 70.0, 2], ["1970-01-01T02:15", 50.0, 50.0, 50.0, 1]]


def test_tz_offset_from_gmt_local_pair() -> None:
    d = {"startTimeGMT": "2026-06-01T05:00:00.0", "startTimeLocal": "2026-06-01T07:00:00.0"}
    assert tz_offset_s(d) == 7200
    d2 = {"sleepStartTimestampGMT": 1_700_000_000_000, "sleepStartTimestampLocal": 1_700_003_600_000}
    assert tz_offset_s(d2) == 3600


def test_parse_bucket() -> None:
    assert parse_bucket("auto") == "auto"
    assert parse_bucket("1h") == "1h"
    with pytest.raises(PaceError) as exc:
        parse_bucket("7m")
    assert exc.value.code == "bad_args"


def _hr_day() -> dict:
    values = [[i * 60 * MS, 60 + (i % 10)] for i in range(1440)]
    return {"calendarDate": "1970-01-01", "restingHeartRate": 50, "junk": None, "heartRateValues": values}


def test_apply_default_fields_and_auto_bucket() -> None:
    data, shape = apply(
        _hr_day(),
        series=(SeriesSpec("heartRateValues", "pairs"),),
        default_fields=("calendarDate", "restingHeartRate", "heartRateValues"),
        fields="default", bucket="auto", raw=False, span_s=86400,
    )
    assert set(data) == {"calendarDate", "restingHeartRate", "heartRateValues"}
    assert data["heartRateValues"]["cols"][0] == "t"
    assert len(data["heartRateValues"]["rows"]) == 96
    assert shape == {"bucket_used": "15m", "reduced_series": ["heartRateValues"], "fields": "default"}


def test_apply_bucket_none_keeps_full_series() -> None:
    data, shape = apply(_hr_day(), series=(SeriesSpec("heartRateValues", "pairs"),),
                        default_fields=(), fields="all", bucket="none", raw=False, span_s=86400)
    assert len(data["heartRateValues"]) == 1440
    assert shape["bucket_used"] == "none" and shape["reduced_series"] == []
    assert "junk" not in data


def test_apply_raw_bypasses_everything() -> None:
    src = _hr_day()
    data, shape = apply(src, series=(SeriesSpec("heartRateValues", "pairs"),), default_fields=("x",),
                        fields="default", bucket="auto", raw=True, span_s=86400)
    assert data is src and shape == {"raw": True}


def test_apply_on_list_payload_shapes_each_item() -> None:
    items = [{"a": 1, "b": None}, {"a": 2, "c": 3}]
    data, _ = apply(items, series=(), default_fields=("a",), fields="default", bucket="auto", raw=False, span_s=0)
    assert data == [{"a": 1}, {"a": 2}]
