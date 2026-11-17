import asyncio
import json
from datetime import date, timedelta

import pytest
from typer.testing import CliRunner

from pace.cache import Cache, CacheTracker
from pace.charts import sources
from pace.cli import app

runner = CliRunner()
TODAY = date(2026, 11, 18)


def _payload(a: str, b: str, *, hrv: bool = False) -> dict:
    """Garmin sleep-stats shape, one entry per day a..b (hand-built)."""
    day, end, items = date.fromisoformat(a), date.fromisoformat(b), []
    while day <= end:
        values = {"totalSleepTimeInSeconds": 30540, "deepTime": 2760, "lightTime": 18480, "remTime": 9300,
                  "awakeTime": 180, "restingHeartRate": 45, "avgHeartRate": 52, "avgOvernightHrv": None,
                  "hrv7dAverage": None, "hrvStatus": None, "sleepScore": None}
        if hrv:
            values.update(avgOvernightHrv=60, hrv7dAverage=58, sleepScore=81)
        items.append({"calendarDate": day.isoformat(), "values": values})
        day += timedelta(days=1)
    return {"individualStats": items, "overallStats": {}}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("pace.config.PACE_HOME", tmp_path)
    monkeypatch.setattr("pace.config.CACHE_DB", tmp_path / "cache.db")


@pytest.fixture
def api(monkeypatch):
    calls: list[tuple] = []

    async def safe_call(method, *args, **kwargs):
        calls.append((method, args))
        assert method == "connectapi"
        a, b = args[0].rsplit("/", 2)[-2:]
        assert (date.fromisoformat(b) - date.fromisoformat(a)).days <= 27
        return True, _payload(a, b), None

    monkeypatch.setattr("pace.commands._helpers.safe_call", safe_call)
    return calls


def test_sleep_chunks_fixed_windows() -> None:
    a = sources.sleep_chunks(date(2026, 9, 20), TODAY)
    b = sources.sleep_chunks(date(2026, 9, 20), TODAY - timedelta(days=1))
    assert a[:-1] == b[:-1] or a[:-2] == b[:-2]
    assert all((hi - lo).days + 1 <= 28 for lo, hi in a)
    days = [lo + timedelta(days=i) for lo, hi in a for i in range((hi - lo).days + 1)]
    assert days == [date(2026, 9, 20) + timedelta(days=i) for i in range((TODAY - date(2026, 9, 20)).days + 1)]
    long = sources.sleep_chunks(TODAY - timedelta(days=59), TODAY)
    assert sum((hi - lo).days + 1 for lo, hi in long) == 60
    assert sources.sleep_chunks(TODAY, TODAY - timedelta(days=1)) == []
    # a range ending today and one ending yesterday share every chunk but the last
    c = sources.sleep_chunks(TODAY - timedelta(days=59), TODAY - timedelta(days=1))
    assert long[:-1] == c[:-1] or long[:-2] == c[:-2]


def test_sleep_rows_mapping(api, tmp_path) -> None:
    async def go():
        cache = Cache()
        try:
            return await sources.sleep_stats(date(2026, 11, 10), date(2026, 11, 25), today=TODAY, cache=cache,
                                             tracker=CacheTracker(), refresh=False)
        finally:
            cache.close()

    rows = asyncio.run(go())
    assert [r["date"] for r in rows] == [f"2026-11-{d}" for d in range(10, 19)]
    assert rows[0] == {"date": "2026-11-10", "sleep_s": 30540, "deep_s": 2760, "light_s": 18480, "rem_s": 9300,
                       "awake_s": 180, "rhr": 45, "avg_hr": 52, "hrv": None, "hrv_7d": None, "score": None}
    assert all(date.fromisoformat(b) <= TODAY for _, (p,) in api for b in [p.rsplit("/", 1)[-1]])


def test_sleep_range_command(api) -> None:
    res = runner.invoke(app, ["wellness", "sleep-range", "--from", "2026-10-01", "--to", "2026-10-31"])
    assert res.stdout.count("\n") == 1
    doc = json.loads(res.stdout)
    assert doc["ok"] and doc["command"] == "wellness sleep-range"
    assert doc["args"]["from"] == "2026-10-01" and doc["args"]["to"] == "2026-10-31"
    assert len(doc["data"]) == 31 and doc["data"][0]["sleep_s"] == 30540
    assert len(api) == 2 and all(c[0] == "connectapi" for c in api)
    assert api[0][1][0].startswith("/sleep-service/stats/sleep/daily/")
