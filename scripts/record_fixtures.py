"""Record one --raw payload per read command into tests/fixtures/, sanitized.

Usage: .venv/bin/python scripts/record_fixtures.py [--date YYYY-MM-DD] [--activity-id N]

Identifiers needed to reach the by-id endpoints (device id, gear uuid, activity id) are
read from the un-sanitized payload in memory; they never reach a fixture file.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
PACE = [sys.executable, "-m", "pace"]

REDACT_KEYS = {"userProfileNumber", "userProfilePk", "userProfileId", "displayName", "fullName", "firstName",
               "lastName", "emailAddress", "profileImageUrlLarge", "profileImageUrlMedium", "profileImageUrlSmall",
               "userName", "ownerDisplayName", "ownerFullName", "ownerProfileImageUrlLarge", "ownerProfileImageUrlMedium",
               "ownerProfileImageUrlSmall", "deviceId", "unitId", "serialNumber", "uuid", "gearUuid", "locationName",
               "startLatitude", "startLongitude", "endLatitude", "endLongitude",
               "latitude", "longitude", "lat", "lon", "minLat", "maxLat", "minLon", "maxLon",
               "startPoint", "endPoint", "polyline", "weatherStationDTO",
               "activityId", "activityUUID", "activityName", "lastUsedDeviceName",
               "birthDate", "gender", "height", "weight",
               "userId", "userProfilePK", "userDailySummaryId", "ownerId", "fullname", "displayname",
               "gearPk", "alarmId", "timeZone"}

EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Values seen under a redacted key. Garmin repeats the same ids under generic key names
# ("id", "ownerId"), so `finalize` scrubs them everywhere once every payload is in.
SEEN_IDS: set = set()


def _identifier_like(v: Any) -> bool:
    """Only opaque identifiers: big integers, digit strings and UUIDs. Human labels such as
    `productDisplayName` must survive the value scrub."""
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return abs(v) >= 1_000_000
    if not isinstance(v, str):
        return False
    return (v.isdigit() and len(v) >= 6) or bool(UUID_RE.match(v))


def sanitize(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in REDACT_KEYS and v is not None:
                if _identifier_like(v):
                    SEEN_IDS.add(v)
                out[k] = "REDACTED"
            else:
                out[k] = sanitize(v)
        return out
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, str) and EMAIL_RE.match(obj):
        return "REDACTED"
    return obj


def scrub_ids(obj):
    if isinstance(obj, dict):
        return {k: scrub_ids(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_ids(v) for v in obj]
    if _identifier_like(obj) and obj in SEEN_IDS:
        return "REDACTED"
    return obj


def finalize() -> None:
    for path in sorted(FIXTURES.glob("*.json")):
        before = path.read_text()
        after = json.dumps(scrub_ids(json.loads(before)), indent=1, sort_keys=True) + "\n"
        if after != before:
            path.write_text(after)
            print(f"scrub {path.stem}")


def record(name: str, argv: list[str], *, write: bool = True) -> Any:
    """Fetch one payload. Returns the un-sanitized data (for in-memory id lookups) or None.
    With write=False nothing is written and nothing is printed but a failure."""
    out = subprocess.run([*PACE, *argv, "--raw", "--no-cache"], capture_output=True, text=True)
    try:
        doc = json.loads(out.stdout)
    except json.JSONDecodeError:
        print(f"skip {name}: non-JSON output (rc={out.returncode}): {out.stderr[:200]}")
        return None
    if not doc["ok"]:
        print(f"skip {name}: {doc['error']['code']} {doc['error']['message']}")
        return None
    data = doc["data"]
    if write:
        FIXTURES.mkdir(exist_ok=True)
        (FIXTURES / f"{name}.json").write_text(json.dumps(sanitize(data), indent=1, sort_keys=True) + "\n")
        print(f"ok   {name}")
    return data


def _first(data: Any, key: str) -> str | None:
    """The first `key` in a list payload, or in the payload itself."""
    for item in data if isinstance(data, list) else [data]:
        if isinstance(item, dict) and item.get(key) is not None:
            return str(item[key])
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="yesterday")
    ap.add_argument("--activity-id", default=None)
    ns = ap.parse_args()
    d = ["--to", ns.date]
    for name in ("summary", "sleep", "hr", "stress", "body-battery", "hrv", "hydration", "spo2"):
        record(f"wellness_{name}", ["wellness", name, *d])
    for name in ("status", "readiness", "endurance", "fitness-age", "hill-score"):
        record(f"training_{name}", ["training", name, *d])
    record("training_race-predictions", ["training", "race-predictions"])
    devices = record("device_list", ["device", "list"])
    record("device_last-used", ["device", "last-used"])
    record("device_alarms", ["device", "alarms"])
    device_id = _first(devices, "deviceId") if devices else None
    if device_id:
        record("device_settings", ["device", "settings", device_id])
        record("device_solar", ["device", "solar", device_id, *d])
    else:
        print("skip device_settings, device_solar: no device id available")
    gear = record("gear_list", ["gear", "list"])
    record("gear_defaults", ["gear", "defaults"])
    gear_uuid = _first(gear, "uuid") if gear else None
    if gear_uuid:
        record("gear_stats", ["gear", "stats", gear_uuid])
    else:
        print("skip gear_stats: no gear uuid available")
    record("goal_list", ["goal", "list"])
    record("goal_personal-records", ["goal", "personal-records"])
    record("profile_settings", ["profile", "settings"])
    # `activity` and `profile show` fixtures are deliberately not written: those commands are
    # hand-written (outside REGISTRY, so no test consumes them) and their payloads carry GPS
    # traces, place names and body metrics that key-by-key redaction cannot make safe. The
    # listing is still fetched in memory so an activity id is on hand if that ever changes.
    aid = ns.activity_id or _first(record("activity_list", ["activity", "list", "--limit", "5"],
                                          write=False), "activityId")
    print(f"skip activity_*: hand-written commands, payload carries GPS and place names"
          f"{'' if aid else ' (no activity id resolved)'}")
    finalize()


if __name__ == "__main__":
    main()
