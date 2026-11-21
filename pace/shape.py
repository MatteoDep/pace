"""Payload shaping: prune empties, select fields, bucket time series."""

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from pace.envelope import PaceError

BUCKETS: dict[str, int] = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "3h": 10800, "1d": 86400}
MAX_ROWS = 120
COLS = ["t", "avg", "min", "max", "n"]


@dataclass(frozen=True)
class SeriesSpec:
    path: str
    kind: Literal["pairs", "records"]
    t_key: str = "0"
    v_key: str = "1"
    #: Values below this are sentinels, not readings, and never reach a bucket.
    min_value: float | None = None


def prune(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            pv = prune(v)
            if pv is None or pv == "" or pv == [] or pv == {}:
                continue
            out[k] = pv
        return out
    if isinstance(obj, list):
        out_l = []
        for v in obj:
            pv = prune(v)
            if pv is None or pv == "" or pv == [] or pv == {}:
                continue
            out_l.append(pv)
        return out_l
    return obj


def parse_fields(value: str) -> str | list[str]:
    value = value.strip()
    if value in ("default", "all"):
        return value
    return [p.strip() for p in value.split(",") if p.strip()]


def parse_bucket(value: str) -> str:
    if value in ("auto", "none") or value in BUCKETS:
        return value
    raise PaceError("bad_args", f"Invalid --bucket {value!r}. Use auto, none, or one of {', '.join(BUCKETS)}.")


def _get_path(obj: Any, path: str) -> tuple[bool, Any]:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def _set_path(obj: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        nxt = cur.get(part)
        cur[part] = dict(nxt) if isinstance(nxt, dict) else {}
        cur = cur[part]
    cur[parts[-1]] = value


def select_fields(obj: dict, paths: list[str]) -> dict:
    out: dict = {}
    for p in paths:
        found, v = _get_path(obj, p)
        if found:
            _set_path(out, p, v)
    return out


def choose_bucket(span_s: int, n_samples: int, max_rows: int = MAX_ROWS) -> str:
    if n_samples <= max_rows:
        return "none"
    for name, secs in BUCKETS.items():
        if span_s / secs <= max_rows:
            return name
    return "1d"


def _to_ms(t: Any) -> int | None:
    if isinstance(t, bool):
        return None
    if isinstance(t, (int, float)):
        return int(t)
    if isinstance(t, str):
        try:
            dt = datetime.fromisoformat(t)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    return None


def extract_points(seq: list, spec: SeriesSpec) -> list[tuple[int, float]]:
    pts: list[tuple[int, float]] = []
    for item in seq:
        if spec.kind == "pairs":
            if not isinstance(item, (list, tuple)):
                continue
            ti, vi = int(spec.t_key), int(spec.v_key)
            if len(item) <= max(ti, vi):
                continue
            t, v = item[ti], item[vi]
        else:
            if not isinstance(item, dict):
                continue
            t, v = item.get(spec.t_key), item.get(spec.v_key)
        ms = _to_ms(t)
        if ms is None or not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        if spec.min_value is not None and v < spec.min_value:
            continue
        # Keep the source type: an int series must not grow ".0" on every min/max.
        pts.append((ms, v if isinstance(v, int) else float(v)))
    return pts


def bucket_series(points: list[tuple[int, float]], bucket_s: int, tz_offset_s: int) -> dict:
    groups: dict[int, list[float]] = {}
    for ms, v in points:
        start = (ms // 1000 // bucket_s) * bucket_s
        groups.setdefault(start, []).append(v)
    rows = []
    for start in sorted(groups):
        vals = groups[start]
        local = datetime.fromtimestamp(start + tz_offset_s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M")
        avg = round(sum(vals) / len(vals), 1)
        rows.append([local, int(avg) if avg == int(avg) else avg, min(vals), max(vals), len(vals)])
    return {"cols": COLS, "rows": rows}


def _gmt_local_offset(data: Any, depth: int = 3) -> int | None:
    """Offset in seconds from the first `<X>GMT` / `<X>Local` pair, searching nested dicts
    (sleep buries its only pair in `dailySleepDTO`)."""
    if not isinstance(data, dict):
        return None
    for key, gmt in data.items():
        if not key.endswith("GMT"):
            continue
        g, loc = _to_ms(gmt), _to_ms(data.get(key[:-3] + "Local"))
        if g is not None and loc is not None:
            return int((loc - g) // 1000)
    if depth <= 1:
        return None
    for value in data.values():
        found = _gmt_local_offset(value, depth - 1)
        if found is not None:
            return found
    return None


def tz_offset_s(data: Any, sample_ms: int | None = None) -> int:
    """Local UTC offset for a payload. Garmin's own GMT/Local pair wins; otherwise the host
    zone *as of the sample*, so a payload from the other side of a DST switch is not shifted."""
    found = _gmt_local_offset(data)
    if found is not None:
        return found
    when = sample_ms / 1000 if sample_ms is not None else time.time()
    offset = datetime.fromtimestamp(when).astimezone().utcoffset()
    return int(offset.total_seconds()) if offset is not None else 0


def _shape_one(item: Any, *, series, default_fields, fields, bucket, span_s, report, resolved=None) -> Any:
    if not isinstance(item, dict):
        return prune(item)
    out = dict(item)
    bucketed: list[tuple[str, str]] = []
    for s in series:
        found, seq = _get_path(out, s.path)
        if not found or not isinstance(seq, list):
            continue
        pts = extract_points(seq, s)
        if resolved is not None:
            chosen = resolved.get(s.path, "none")
        else:
            chosen = choose_bucket(span_s, len(pts)) if bucket == "auto" else bucket
        if chosen == "none":
            continue
        if not pts:
            # The bucket came from a sibling day; this one has no samples, so drop the key
            # instead of emitting a headers-only table. `prune` removes the None.
            _set_path(out, s.path, None)
            continue
        _set_path(out, s.path, bucket_series(pts, BUCKETS[chosen], tz_offset_s(item, pts[0][0])))
        bucketed.append((s.path, chosen))
    if fields == "default" and default_fields:
        out = select_fields(out, list(default_fields))
    elif isinstance(fields, list):
        out = select_fields(out, fields)
    # Only series that survived field selection are actually reduced in the payload; a
    # bucket that --fields threw away was not "used".
    for path, chosen in bucketed:
        if not _get_path(out, path)[0]:
            continue
        report["bucket_used"] = chosen
        if path not in report["reduced_series"]:
            report["reduced_series"].append(path)
    return prune(out)


def _resolve_list_buckets(data: list, series: tuple[SeriesSpec, ...], span_s: int) -> dict[str, str]:
    """Pick one bucket per series across all list items: the coarsest any item would
    choose on its own, so every item ends up with the same, homogeneous row grid.
    "none" only if every item independently chooses "none"."""
    order = list(BUCKETS)
    resolved: dict[str, str] = {}
    for s in series:
        best = "none"
        for item in data:
            if not isinstance(item, dict):
                continue
            found, seq = _get_path(item, s.path)
            if not found or not isinstance(seq, list):
                continue
            choice = choose_bucket(span_s, len(extract_points(seq, s)))
            if choice == "none":
                continue
            if best == "none" or order.index(choice) > order.index(best):
                best = choice
        resolved[s.path] = best
    return resolved


def apply(data: Any, *, series: tuple[SeriesSpec, ...], default_fields: tuple[str, ...],
          fields: str | list[str], bucket: str, raw: bool, span_s: int) -> tuple[Any, dict]:
    if raw:
        return data, {"raw": True}
    report: dict[str, Any] = {"bucket_used": "none", "reduced_series": [], "fields": fields}
    resolved = _resolve_list_buckets(data, series, span_s) if isinstance(data, list) and bucket == "auto" and series else None
    kw = dict(series=series, default_fields=default_fields, fields=fields, bucket=bucket, span_s=span_s,
              report=report, resolved=resolved)
    if isinstance(data, list):
        return [_shape_one(i, **kw) for i in data], report
    return _shape_one(data, **kw), report
