"""Record one --raw payload per read command into tests/fixtures/, sanitized.

Usage: .venv/bin/python scripts/record_fixtures.py [--date YYYY-MM-DD] [--activity-id N]
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
PACE = [sys.executable, "-m", "pace"]

REDACT_KEYS = {"userProfileNumber", "userProfilePk", "userProfileId", "displayName", "fullName", "firstName",
               "lastName", "emailAddress", "profileImageUrlLarge", "profileImageUrlMedium", "profileImageUrlSmall",
               "userName", "ownerDisplayName", "ownerFullName", "ownerProfileImageUrlLarge", "ownerProfileImageUrlMedium",
               "ownerProfileImageUrlSmall", "deviceId", "unitId", "serialNumber", "uuid", "gearUuid", "locationName",
               "startLatitude", "startLongitude", "endLatitude", "endLongitude"}


def sanitize(obj):
    if isinstance(obj, dict):
        return {k: ("REDACTED" if k in REDACT_KEYS and v is not None else sanitize(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, str) and re.match(r"^[\w.+-]+@[\w-]+\.[\w.]+$", obj):
        return "REDACTED"
    return obj


def record(name: str, argv: list[str]) -> None:
    out = subprocess.run([*PACE, *argv, "--raw", "--no-cache"], capture_output=True, text=True)
    doc = json.loads(out.stdout)
    if not doc["ok"]:
        print(f"skip {name}: {doc['error']['code']} {doc['error']['message']}")
        return
    FIXTURES.mkdir(exist_ok=True)
    (FIXTURES / f"{name}.json").write_text(json.dumps(sanitize(doc["data"]), indent=1, sort_keys=True) + "\n")
    print(f"ok   {name}")


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
    for grp, name in (("device", "list"), ("device", "last-used"), ("device", "alarms"), ("gear", "list"),
                      ("gear", "defaults"), ("goal", "list"), ("goal", "personal-records"), ("profile", "settings"),
                      ("profile", "show"), ("activity", "last")):
        record(f"{grp}_{name}", [grp, name])
    record("activity_list", ["activity", "list", "--limit", "5"])
    aid = ns.activity_id
    if aid is None:
        listing = json.loads((FIXTURES / "activity_list.json").read_text())
        aid = str(listing[0]["activityId"]) if listing else None
    if aid:
        for name in ("show", "splits", "weather", "details"):
            record(f"activity_{name}", ["activity", name, aid])


if __name__ == "__main__":
    main()
