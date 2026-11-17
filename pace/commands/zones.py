"""`zones` commands: show, estimate, analyze, test-workout, add, accept, remove.

The estimate orchestrator (methods doc 14.7) comes first; the typer commands are at the end of the file.

`run_estimate` reads Garmin data through an injected `Fetcher` that checks the cache first and counts the calls
it makes; `--max-calls` is a hard cap on them. At the cap a cache hit is still served and a read that needs a
call raises `Deferred` instead; the orchestrator lists it in `deferred`, runs the methods on what was read and
logs the estimate as partial. Reads are made in the order of 14.7: list rows, personal records, sleep stats,
phase 1 picks in rank order, extension picks, phase 2 (drift) picks, FIT downloads by span score.
Methods: docs/superpowers/specs/2026-11-17-zones-methods.md sections 1, 6, 7 and 14.
"""

import copy
import math
import statistics
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any, Protocol

import typer

from pace import fit as fitmod
from pace import hrv, thresholds, zones
from pace.cache import Cache, CacheTracker
from pace.charts.sources import sleep_chunks, sleep_stats
from pace.commands._helpers import cached_call
from pace.envelope import PaceError, Result
from pace.envelope import run as run_command
from pace.factory import NO_CACHE_OPT
from pace.ratelimit import limiter
from pace.spec import TTL_STATIC
from pace.workout import parse_pace

RR_SINCE = date(2026, 9, 1)     # first date with RR data on this athlete's watch (14.7)
DRIFT_WEEKS = 12                # H5 drift picks and H4 runs: the last 12 weeks
REST_HR_DAYS = 28
MAXCHART_CAP = 20000
LIST_TTL = 600
OLD_RUN_TTL = 60 * 86400        # zones reads of runs started more than 1 day ago
NEW_RUN_TTL = 3600
PR_DISTANCES_M = {1: 1000.0, 2: 1609.344, 3: 5000.0}   # Garmin PR typeId: 1 km, 1 mile, 5 km
STOP_CODES = ("rate_limited", "network")   # a read failing with these defers every later miss
DEFAULT_WEEKS = 8
DEFAULT_MAX_CALLS = 40
GARMIN_ZONES_PATH = "/biometric-service/heartRateZones"
GARMIN_ZONES_TTL = 300
DUE_LOOKBACK_DAYS = 56          # `zones show` reads the list this far before the last estimate (the run before it)
NO_ESTIMATE_LOOKBACK_DAYS = 84
F3_RECENT_DAYS = 28             # F1 compares with the newest F3 of this many days (methods doc 9a)
TESTS = ("drift", "time_trial", "ramp")
ALL_WRIST = "no chest-strap run to examine: wrist HR runs were skipped (use --include-wrist)"


class Deferred(Exception):
    """A read that needs a Garmin call once the call cap is reached; nothing was called."""


class Fetcher(Protocol):
    """Garmin reads for one estimate. Each read checks the cache first; `calls` counts the Garmin calls made
    (cache hits are free). With `max_calls` set, a read that needs a call at the cap raises Deferred."""

    calls: int
    max_calls: int | None

    async def list_rows(self, start: date, end: date) -> list[dict]: ...
    async def activity(self, activity_id: int) -> dict: ...
    async def details(self, activity_id: int, *, maxchart: int) -> dict: ...
    async def prs(self) -> list[dict]: ...
    async def sleep(self, start: date, end: date) -> list[dict]: ...
    async def fit(self, activity_id: int) -> dict | None: ...
    def fit_cached(self, activity_id: int) -> bool: ...


class _BudgetCache:
    """Cache proxy for cached_call: a hit costs nothing; a miss spends one call of the budget or raises Deferred."""

    def __init__(self, cache: Cache, owner: "GarminFetcher") -> None:
        self._cache, self._owner = cache, owner

    async def get_or_fetch(self, key: str, fetch: Callable[[], Awaitable[Any]], ttl: int,
                           refresh: bool = False) -> tuple[Any, bool, int]:
        async def guarded() -> Any:
            self._owner.spend(key)
            return await fetch()

        return await self._cache.get_or_fetch(key, guarded, ttl=ttl, refresh=refresh)


class GarminFetcher:
    """The real Fetcher: cached_call through a budget-checking cache proxy, and pace.fit for FIT data.

    get_activity and get_activity_details use their own keys (`zones:...`), cached 60 days for runs that
    started before yesterday (start dates learned from the list read), so a capped estimate continues from cache.
    """

    def __init__(self, cache: Cache, *, today: date, refresh: bool = False,
                 tracker: CacheTracker | None = None) -> None:
        self.calls = 0
        self.max_calls: int | None = None
        self._cache = _BudgetCache(cache, self)
        self._today, self._refresh, self._tracker = today, refresh, tracker
        self._days: dict[int, date] = {}

    def spend(self, what: str) -> None:
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise Deferred(what)
        self.calls += 1

    async def _call(self, method: str, *args: Any, ttl: int, prefix: str | None = None, **kwargs: Any) -> Any:
        return await cached_call(method, *args, cache=self._cache, ttl=ttl, tracker=self._tracker,  # type: ignore[arg-type]
                                 refresh=self._refresh, key_prefix=prefix, **kwargs)

    def _run_ttl(self, activity_id: int) -> int:
        day = self._days.get(activity_id)
        return OLD_RUN_TTL if day is not None and day < self._today - timedelta(days=1) else NEW_RUN_TTL

    async def list_rows(self, start: date, end: date) -> list[dict]:
        rows = await self._call("get_activities_by_date", start.isoformat(), end.isoformat(), None, ttl=LIST_TTL)
        out = []
        for r in rows or []:
            if r.get("activityId") is None:
                continue
            day = _row_day(r)
            if day is not None:
                self._days[r["activityId"]] = day
            out.append(r)
        return out

    async def activity(self, activity_id: int) -> dict:
        return await self._call("get_activity", activity_id, ttl=self._run_ttl(activity_id), prefix="zones")

    async def details(self, activity_id: int, *, maxchart: int) -> dict:
        return await self._call("get_activity_details", activity_id, ttl=self._run_ttl(activity_id), prefix="zones",
                                maxchart=maxchart)

    async def splits(self, activity_id: int) -> dict:
        return await self._call("get_activity_splits", activity_id, ttl=self._run_ttl(activity_id), prefix="zones")

    def learn_day(self, activity_id: int, day: date) -> None:
        """Tell the fetcher when a run started (for its cache TTL) when it did not come from a list row."""
        self._days[activity_id] = day

    async def prs(self) -> list[dict]:
        return await self._call("get_personal_record", ttl=TTL_STATIC) or []

    async def sleep(self, start: date, end: date) -> list[dict]:
        return await sleep_stats(start, end, today=self._today, cache=self._cache, tracker=self._tracker,
                                 refresh=self._refresh)

    def fit_cached(self, activity_id: int) -> bool:
        return (fitmod.fit_dir() / f"{activity_id}.json").exists()

    async def fit(self, activity_id: int) -> dict | None:
        if not self.fit_cached(activity_id):
            if fitmod.no_rr(activity_id) is not None:
                return None
            self.spend(f"fit:{activity_id}")
        return await fitmod.get_fit(activity_id, tracker=self._tracker)


def estimated_seconds(calls: int, *, min_interval: float, per_minute: int, per_hour: int) -> int:
    """Methods doc 14.7: minimum rate-limiter time for `calls` calls with no earlier calls in the process."""
    if calls <= 0:
        return 0
    k = calls - 1
    return int(3600 * (k // per_hour) + 60 * ((k % per_hour) // per_minute) + min_interval * (k % per_minute))


# --- helpers ---

def _rid(row: dict) -> int:
    return row["activityId"]


def _row_day(row: dict) -> date | None:
    try:
        return date.fromisoformat(str(row.get("startTimeLocal") or row.get("startTimeGMT"))[:10])
    except ValueError:
        return None


def _start_key(row: dict) -> str:
    return str(row.get("startTimeGMT") or row.get("startTimeLocal") or "")


def _maxchart(row: dict) -> int:
    """Section 1: 2 x the run's elapsed seconds, capped at 20000."""
    elapsed = row.get("elapsedDuration") or row.get("duration")
    if not isinstance(elapsed, (int, float)) or elapsed <= 0:
        return MAXCHART_CAP
    return min(MAXCHART_CAP, math.ceil(2 * elapsed))


def _nearest(rows: list[dict], key: str, target: float) -> int | None:
    """The row nearest `target` by `key` (ties: newest first, then lowest id, as in thresholds selection)."""
    if not rows:
        return None
    return _rid(min(rows, key=lambda r: (round(abs(r[key] - target), 9), *thresholds.tie_key(r))))


def _pr_points(prs: list[dict] | None, since: date) -> list[dict]:
    """H1 extra points: Garmin 1 km, 1 mile and 5 km records set on or after `since` (methods doc 3)."""
    out = []
    for p in prs or []:
        dist, secs = PR_DISTANCES_M.get(p.get("typeId")), p.get("value")
        try:
            day = date.fromisoformat(str(p.get("prStartTimeGmtFormatted") or p.get("actStartDateTimeInGMTFormatted"))[:10])
        except ValueError:
            continue
        if dist and isinstance(secs, (int, float)) and day >= since:
            out.append({"duration_s": secs, "distance_m": dist, "date": day.isoformat()})
    return out


def _rest_hr(sleep: list[dict] | None) -> int | None:
    vals = [r["rhr"] for r in sleep or [] if isinstance(r.get("rhr"), (int, float))]
    return zones.R(statistics.median(vals)) if vals else None


def _value(res: dict, hr_key: str = "hr") -> dict | None:
    if res.get("status") != "ok" or (res.get(hr_key) is None and res.get("pace_s_per_km") is None):
        return None
    return {"hr": res.get(hr_key), "pace_s_per_km": res.get("pace_s_per_km")}


# --- reads and picks ---

class _Reads:
    """Per-estimate read state: picked runs fetched in detail, known HR sources, deferred reads, skip reasons."""

    def __init__(self, fetcher: Fetcher, *, include_wrist: bool) -> None:
        self.f = fetcher
        self.include_wrist = include_wrist
        self.by_id: dict[int, dict] = {}
        self.sources: dict[int, str] = {}
        self.wrist: set[int] = set()
        self.runs: dict[int, dict] = {}         # id -> {"id", "date", "start", "hr_source", "table", "groups"}
        self.drift_ids: list[int] = []
        self.deferred: list[dict] = []
        self.skipped: list[dict] = []           # {"id", "group", "reason"}, one per (id, group)
        self.dropped: dict[str, set[int]] = {}  # group -> wrist picks not fetched in detail
        self.fit_errors: list[dict] = []

    def _defer(self, entry: dict) -> None:
        if not any(d["kind"] == entry["kind"] and d.get("id") == entry.get("id") for d in self.deferred):
            self.deferred.append(entry)

    async def read(self, entry: dict, factory: Callable[[], Awaitable[Any]]) -> Any:
        """Run one read; None when it was deferred (cap, or a rate-limit/network error that stops further calls),
        when a picked run is not found (skipped), or when a FIT file is unreadable (marked fit_unreadable).
        auth_required and every other error propagate."""
        try:
            return await factory()
        except Deferred:
            self._defer(entry)
            return None
        except PaceError as err:
            if err.code in STOP_CODES:
                self.f.max_calls = self.f.calls
                self._defer({**entry, "error": err.code})
                return None
            if err.code == "not_found" and entry["kind"] == "run":
                self.skip_one(entry["id"], entry["group"], f"not found on Garmin: {err.message}")
                return None
            if entry["kind"] == "fit" and isinstance(err, fitmod.FitUnreadable):
                fitmod.mark_no_rr(entry["id"], "fit_unreadable")
                self.fit_errors.append({"id": entry["id"], "reason": "fit_unreadable", "detail": err.message})
                return None
            if err.code == "internal" and entry["kind"] == "fit":   # transient download oddity: retry next run
                self._defer({**entry, "error": err.code})
                return None
            raise

    def skip_one(self, rid: int, group: str, reason: str) -> None:
        if not any(s["id"] == rid and s["group"] == group for s in self.skipped):
            self.skipped.append({"id": rid, "group": group, "reason": reason})

    def skip(self, rows: list[dict], group: str) -> None:
        for s in rows:
            self.skip_one(s["id"], group, s["reason"])

    async def pick(self, rid: int, group: str, *, always: bool = False) -> str:
        """Fetch one picked run: get_activity, then get_activity_details unless it is a wrist run that this
        group cannot use. Returns "ok", "wrist" (to be replaced) or "deferred"."""
        if rid in self.runs:
            self.runs[rid]["groups"].append(group)
            return "ok"
        usable_wrist = always or self.include_wrist
        if rid not in self.sources:
            act = await self.read({"kind": "run", "id": rid, "group": group}, lambda: self.f.activity(rid))
            if act is None:
                return "deferred"
            self.sources[rid] = thresholds.hr_source(act)
            if self.sources[rid] == "wrist":
                self.wrist.add(rid)
        if self.sources[rid] == "wrist" and not usable_wrist:
            self.skip_one(rid, group, f"{thresholds.WRIST_REASON}: not fetched in detail for the {group} picks")
            self.dropped.setdefault(group, set()).add(rid)
            return "wrist"
        row = self.by_id[rid]
        det = await self.read({"kind": "run", "id": rid, "group": group},
                              lambda: self.f.details(rid, maxchart=_maxchart(row)))
        if det is None:
            return "deferred"
        self.runs[rid] = {"id": rid, "date": _row_day(row), "start": _start_key(row),
                          "hr_source": self.sources[rid], "table": thresholds.samples(det), "groups": [group]}
        return "ok"

    async def history(self, rows: list[dict], *, exclude: set[int], group: str) -> None:
        """Phase 1: effort picks (always fetched), then steady picks; a wrist steady pick is replaced by the
        untaken candidate nearest its speed target. `group` names the list-row skips ("history" | "extension")."""
        sel = thresholds.select_history(rows, exclude=exclude)
        self.skip(sel["skipped"], group)
        for rid in sel["effort"]:
            await self.pick(rid, "effort", always=True)
        skipped = {s["id"] for s in sel["skipped"]}
        lo, hi = thresholds.STEADY_S
        pool = [r for r in rows if _rid(r) not in exclude | skipped and _rid(r) not in sel["effort"]
                and isinstance(r.get("averageSpeed"), (int, float)) and lo <= r["duration"] <= hi]
        targets = {t["id"]: t["target"] for t in sel["steady_targets"]}
        taken = set(sel["effort"]) | set(sel["steady"]) | exclude
        for rid in sel["steady"]:
            cur: int | None = rid
            while cur is not None and await self.pick(cur, "steady") == "wrist":
                cur = _nearest([r for r in pool if _rid(r) not in taken | self.wrist], "averageSpeed", targets[rid])
                if cur is not None:
                    taken.add(cur)

    async def drift(self, rows: list[dict], *, ref: int | None) -> None:
        """Phase 2: H5 drift picks; a wrist pick is replaced by the next candidate of the same ranking (same side
        of the ref, else the other side; without a ref, nearest its averageHR target)."""
        exclude = set() if self.include_wrist else set(self.wrist)
        if exclude:   # known wrist runs this group would have picked count as dropped wrist picks
            own = set(thresholds.select_drift(rows, ref_hr=ref)["picks"]) & exclude
            self.dropped.setdefault("drift", set()).update(own)
        sel = thresholds.select_drift(rows, ref_hr=ref, exclude=exclude)
        self.skip(sel["skipped"], "drift")
        skipped = {s["id"] for s in sel["skipped"]}
        pool = [r for r in rows if _rid(r) not in exclude | skipped]
        targets = {t["id"]: t["target"] for t in sel["targets"]}
        taken = set(sel["picks"]) | exclude
        for rid in sel["picks"]:
            cur: int | None = rid
            while cur is not None:
                status = await self.pick(cur, "drift")
                if status == "ok":
                    self.drift_ids.append(cur)
                if status != "wrist":
                    break
                cands = [r for r in pool if _rid(r) not in taken | self.wrist]
                if ref is None:
                    cur = _nearest(cands, "averageHR", targets[rid])
                else:
                    below = self.by_id[rid]["averageHR"] < ref
                    side = [r for r in cands if (r["averageHR"] < ref) == below]
                    cur = _nearest(side or cands, "averageHR", ref)
                if cur is not None:
                    taken.add(cur)


# --- the estimate ---

def _history_methods(reads: _Reads, prs: list[dict] | None, since: date) -> tuple[dict, dict]:
    runs = [{"id": r["id"], "hr_source": r["hr_source"], "table": r["table"]}
            for r in reads.runs.values() if {"effort", "steady"} & set(r["groups"])]
    cs = thresholds.critical_speed(runs, prs=_pr_points(prs, since))
    h2 = thresholds.hr_speed(runs, include_wrist=reads.include_wrist)
    return cs, h2


def _no_strap(reads: _Reads, res: dict, ids: list[int], dropped: set[int]) -> None:
    """Review Focus 4: an HR method says so when its own picks had wrist runs that were dropped and none of its
    runs is a strap run."""
    if res.get("status") != "ok" and not reads.include_wrist and dropped \
            and not any(reads.sources.get(i) == "strap" for i in ids):
        res["reasons"].append(ALL_WRIST)


async def _h4(reads: _Reads, data: dict, *, ref: int | None, today: date) -> tuple[dict, dict[int, dict]]:
    """FIT downloads by span score, then H4 on cached and downloaded RR data; updates the H4 memory."""
    h4_state = data["h4"]
    if h4_state["unreliable"]:
        since = h4_state.get("since")
        return ({"status": "skipped", "hr": None, "pace_s_per_km": None, "confidence": None, "runs": [],
                 "notes": [], "rejected": [],
                 "reasons": [f"alpha1 marked unreliable for this athlete since {since} (two not-monotonic runs "
                             "in a row): no FIT downloads; retry with --retry-h4"]}, {})
    window_from = max(today - timedelta(weeks=DRIFT_WEEKS), RR_SINCE)
    eligible = [r for r in reads.runs.values() if r["date"] is not None and r["date"] >= window_from]
    pool = [r for r in eligible if r["hr_source"] == "strap" or reads.include_wrist]
    dropped = {r["id"] for r in eligible if r["hr_source"] != "strap"}
    ids = [r["id"] for r in pool]
    no_rr = {i for i in ids if fitmod.no_rr(i) is not None}
    cached = {i for i in ids if reads.f.fit_cached(i)}
    downloads = thresholds.select_fit(pool, ref_hr=ref, cached=cached, no_rr=no_rr, rr_since=RR_SINCE,
                                      unreliable=False, include_wrist=reads.include_wrist)
    fits: dict[int, dict] = {}
    for rid in [*downloads, *sorted(cached - no_rr)]:
        fd = await reads.read({"kind": "fit", "id": rid}, lambda rid=rid: reads.f.fit(rid))
        if fd is not None:
            fits[rid] = fd
    run_windows: list[dict] = []
    rejected = [{"id": i, "reason": fitmod.no_rr(i)} for i in sorted(no_rr)] + reads.fit_errors
    for run in sorted((reads.runs[i] for i in fits), key=lambda r: r["start"]):
        rid = run["id"]
        try:
            aligned = fitmod.aligned_rr(fits[rid])
        except fitmod.RRRejected as exc:
            fitmod.mark_no_rr(rid, "rr_rejected")
            rejected.append({"id": rid, "reason": "rr_rejected", "detail": str(exc)})
            continue
        if not aligned:
            rejected.append({"id": rid, "reason": fitmod.no_rr(rid) or "no RR data"})
            continue
        w = hrv.windows(aligned)
        run_windows.append({"id": rid, "windows": w["windows"], "dropped_coupling": w["dropped_coupling"]})
    h4 = thresholds.alpha1_lt1(run_windows)
    h4["rejected"] = rejected
    h4["downloads"] = downloads
    for rep in h4["runs"]:  # start-time order within this estimate (earlier estimates counted theirs already)
        zones.record_alpha1_outcome(data, rep["outcome"], day=reads.runs[rep["id"]]["date"], activity=rep["id"])
    wrist_used = [r["id"] for r in h4["runs"] if r["lt1_hr"] is not None and reads.sources.get(r["id"]) != "strap"]
    if h4["status"] == "ok" and wrist_used:
        h4["confidence"] = "low"
        h4["notes"].append("wrist HR runs included: confidence low")
    _no_strap(reads, h4, ids, dropped)
    return h4, fits


async def _h5(reads: _Reads, fits: dict[int, dict], h2: dict) -> dict:
    evidence, rejected = [], []
    for rid in reads.drift_ids:
        run = reads.runs[rid]
        fd = fits.get(rid)
        if fd is None and reads.f.fit_cached(rid):   # cached FIT data only: no call for temperature
            fd = await reads.read({"kind": "fit", "id": rid}, lambda rid=rid: reads.f.fit(rid))
        seg = thresholds.drift_segment({"id": rid, "hr_source": run["hr_source"], "table": run["table"]},
                                       fit_records=(fd or {}).get("records") or None,
                                       include_wrist=reads.include_wrist)
        (rejected if "rejected" in seg else evidence).append(seg)
    h5 = thresholds.drift_bounds(evidence, h2 if h2.get("status") == "ok" else None)
    h5["rejected"] = rejected
    _no_strap(reads, h5, reads.drift_ids, reads.dropped.get("drift", set()))
    return h5


def _record(today: date, lt1: dict, lt2: dict, methods: dict, *, data: dict, rest_hr: int | None,
            reads: _Reads, extras: dict) -> dict | None:
    """The full record `--save` stores (source "estimate"), or None without any threshold value."""
    v1, v2 = _value(lt1), _value(lt2)
    if v1 is None and v2 is None:
        return None
    cs, h2 = methods["H1"], methods["H2"]
    confs = [c for v, c in ((v1, lt1.get("confidence")), (v2, lt2.get("confidence"))) if v is not None and c]
    notes = [*lt1.get("flags", []), *lt1.get("notes", []), *lt2.get("reasons", [])]
    return {
        "date": today.isoformat(), "source": "estimate",
        "method": f"lt1 {lt1.get('method') or '-'}, lt2 {extras['lt2_method'] or '-'}",
        "lt1": v1, "lt2": v2, "max_hr": (data.get("hr_max") or {}).get("bpm"), "rest_hr": rest_hr,
        "cs_mps": cs.get("cs_mps"), "d_prime_m": cs.get("d_prime_m"),
        "activities": sorted(reads.runs), "confidence": min(confs, key=["low", "medium", "high"].index) if confs else None,
        "notes": "; ".join(notes) or None,
        "details": {
            "h2": ({k: h2[k] for k in ("a", "b", "v_min", "v_max")} if h2.get("status") == "ok" else None),
            "h3": {"hr": methods["H3"].get("hr"), "pace_s_per_km": methods["H3"].get("pace_s_per_km")},
            "h4": {"hr": methods["H4"].get("hr"), "status": methods["H4"]["status"]},
            "h5": {"hr": methods["H5"].get("estimate"), "bounds": methods["H5"].get("bounds"),
                   "status": methods["H5"]["status"]},
            "lt1": {"method": lt1.get("method"), "confidence": lt1.get("confidence"), "suggest": lt1.get("suggest")},
            **extras,
        },
    }


async def run_estimate(fetcher: Fetcher, data: dict, *, today: date, weeks: int = 8, max_calls: int = 40,
                       include_wrist: bool = False, retry_h4: bool = False) -> dict:
    """One history estimate (methods doc 14.7). Updates `data` in place: hr_max, the H4 memory, the estimates log
    (always, partial when reads were deferred). Owns mark_no_rr(id, "rr_rejected") when aligned_rr raises and
    mark_no_rr(id, "fit_unreadable") when a FIT download cannot be parsed (fitmod.FitUnreadable only).

    A rate_limited or network error stops further Garmin calls: that read and every later miss are deferred
    (entries carry "error" for the failing read) and the estimate is partial; a picked run not found is skipped.
    H4 outcomes are recorded in start-time order within this estimate; runs counted by earlier estimates are
    never counted again. `record` is the record `--save` stores, or None; its `method` reads
    "lt1 <H3|H4|H5|H4+H5|->, lt2 <H1|H1+H2|->". Methods that still fail after the 2N-week extension carry
    `suggest` {"test": "F2"} (H1, LT2) or {"test": "F1"} (H2, H5).
    """
    if retry_h4:
        zones.reset_h4(data)
    start_calls = fetcher.calls
    fetcher.max_calls = start_calls + max_calls
    reads = _Reads(fetcher, include_wrist=include_wrist)
    history_from = today - timedelta(weeks=weeks)
    ext_from = today - timedelta(weeks=2 * weeks)
    drift_from = today - timedelta(weeks=DRIFT_WEEKS)

    rows = await reads.read({"kind": "list"}, lambda: fetcher.list_rows(min(ext_from, drift_from), today)) or []
    rows = [r for r in rows if r.get("activityId") is not None and _row_day(r) is not None]
    reads.by_id = {_rid(r): r for r in rows}
    prs = await reads.read({"kind": "prs"}, fetcher.prs)
    sleep: list[dict] = []
    sleep_from = today - timedelta(days=REST_HR_DAYS - 1)
    for a, b in sleep_chunks(sleep_from, today):   # one read per fixed chunk: a served chunk is kept
        got = await reads.read({"kind": "sleep", "id": a.isoformat()},
                               lambda a=a, b=b: fetcher.sleep(max(a, sleep_from), b))
        sleep += got or []

    await reads.history([r for r in rows if history_from <= _row_day(r) <= today], exclude=set(), group="history")
    cs, h2 = _history_methods(reads, prs, history_from)
    extended = len(cs.get("windows") or []) < len(thresholds.H1_WINDOWS) or h2.get("status") != "ok"
    if extended:
        await reads.history([r for r in rows if ext_from <= _row_day(r) < history_from], exclude=set(reads.runs),
                            group="extension")
        cs, h2 = _history_methods(reads, prs, ext_from)
    history_ids = [i for i, r in reads.runs.items() if {"effort", "steady"} & set(r["groups"])]
    _no_strap(reads, h2, history_ids,
              {i for i in history_ids if reads.sources.get(i) != "strap"} | reads.dropped.get("steady", set()))
    h3 = thresholds.lt1_from_cs(cs, h2)

    cur_lt1 = zones.current(data)["lt1"]
    ref = ((cur_lt1 or {}).get("lt1") or {}).get("hr")
    if ref is None:
        ref = h3.get("hr")
    await reads.drift([r for r in rows if drift_from <= _row_day(r) <= today], ref=ref)

    h4, fits = await _h4(reads, data, ref=ref, today=today)
    h5 = await _h5(reads, fits, h2)

    for run in sorted(reads.runs.values(), key=lambda r: r["start"]):
        if run["hr_source"] == "strap":
            bpm = thresholds.max_hr(run["table"])
            if bpm is not None:
                zones.update_hr_max(data, bpm, activity=run["id"], day=run["date"])

    lt2 = thresholds.lt2_from_cs(cs, h2)
    if include_wrist and lt2.get("hr") is not None and any(reads.sources.get(s["run"]) != "strap"
                                                           for s in h2.get("segments", [])):
        lt2["confidence"] = "low"
        lt2["reasons"].append("wrist HR runs in H2: confidence low")
    lt1 = thresholds.combine_lt1(h3, h4, h5, h2)
    if extended:   # 14.7: the extension still failed -> the matching field test is suggested
        for res, test in ((cs, "F2"), (h2, "F1")):
            if res.get("status") != "ok":
                res["suggest"] = {"test": test}
        if lt2.get("status") != "ok" or lt2.get("hr") is None:
            lt2["suggest"] = {"test": "F2"}
        if h5.get("status") != "ok":
            h5["suggest"] = {"test": "F1"}
    methods = {"H1": cs, "H2": h2, "H3": h3, "H4": h4, "H5": h5}
    lt2_method = ("H1+H2" if lt2.get("hr") is not None else "H1") if lt2.get("status") == "ok" else None
    partial = bool(reads.deferred)
    summary = {"date": today.isoformat(), "lt1_hr": lt1.get("hr"), "lt2_hr": lt2.get("hr"),
               "lt2_pace": lt2.get("pace_s_per_km"), "methods": {"lt1": lt1.get("method"), "lt2": lt2_method},
               "confidence": {"lt1": lt1.get("confidence"), "lt2": lt2.get("confidence")}, "partial": partial}
    zones.log_estimate(data, summary)
    rest_hr = _rest_hr(sleep)
    calls = fetcher.calls - start_calls
    extras = {"weeks": weeks, "extended": extended, "ref_hr": ref, "partial": partial, "lt2_method": lt2_method}
    used = [{"id": r["id"], "date": r["date"].isoformat() if r["date"] else None, "hr_source": r["hr_source"],
             "groups": r["groups"]} for r in sorted(reads.runs.values(), key=lambda r: r["start"])]
    return {
        "lt1": lt1, "lt2": lt2, "methods": methods, "runs_used": used,
        "runs_skipped": [s for s in reads.skipped if s["id"] not in reads.runs],
        "deferred": reads.deferred, "calls": calls,
        "estimated_seconds": estimated_seconds(calls, min_interval=limiter.min_interval,
                                               per_minute=limiter.per_minute, per_hour=limiter.per_hour),
        "hr_max": data.get("hr_max"), "rest_hr": rest_hr, "h4": dict(data["h4"]), "weeks": weeks,
        "extended": extended, "ref_hr": ref, "summary": summary,
        "record": _record(today, lt1, lt2, methods, data=data, rest_hr=rest_hr, reads=reads, extras=extras),
    }


# --- commands ---

app = typer.Typer(no_args_is_help=True, rich_markup_mode=None, pretty_exceptions_enable=False,
                  help="Thresholds and zones: estimate, analyze field tests, record, accept (local file + Garmin reads).")


def _bad(msg: str, hint: str | None = None) -> PaceError:
    return PaceError("bad_args", msg, hint=hint)


def _current_view(data: dict) -> dict:
    cur = zones.current(data)
    return {t: zones.provenance(cur[t]) for t in zones.THRESHOLDS}


def _is_run(row: dict) -> bool:
    t = row.get("activityType")
    key = t.get("typeKey") if isinstance(t, dict) else t
    return "run" in str(key or "").lower()


def _runs_since(data: dict, rows: list[dict]) -> list[dict]:
    """`due` input: the runs after the last complete estimate plus the last run before it (so a break that
    straddles the estimate is seen); every run in the window when there is no complete estimate."""
    runs = [r for r in rows if _is_run(r) and _row_day(r) is not None]
    last = zones.last_complete_estimate_day(data)
    if last is None:
        return runs
    after = [r for r in runs if _row_day(r) > last]  # type: ignore[operator]
    before = [r for r in runs if _row_day(r) <= last]  # type: ignore[operator]
    return ([max(before, key=lambda r: (_row_day(r), _start_key(r)))] if before else []) + after


async def _garmin_zones(cache: Cache, tracker: CacheTracker, refresh: bool) -> list[dict] | None:
    """The watch's HR zones; any failure but auth leaves them unknown (show still answers from local data)."""
    try:
        payload = await cached_call("connectapi", GARMIN_ZONES_PATH, cache=cache, ttl=GARMIN_ZONES_TTL,
                                    tracker=tracker, refresh=refresh)
    except PaceError as err:
        if err.code == "auth_required":
            raise
        return None
    return [e for e in payload if isinstance(e, dict)] if isinstance(payload, list) else None


@app.command("show")
def show(no_cache: bool = NO_CACHE_OPT) -> None:
    """Current thresholds with provenance, derived zones, records, candidates, due, the watch zones and in_sync."""
    async def work() -> Result:
        today = date.today()
        data = zones.load()
        last = zones.last_complete_estimate_day(data)
        start = last - timedelta(days=DUE_LOOKBACK_DAYS) if last else today - timedelta(days=NO_ESTIMATE_LOOKBACK_DAYS)
        cache, tracker = Cache(), CacheTracker()
        try:
            fetcher = GarminFetcher(cache, today=today, refresh=no_cache, tracker=tracker)
            rows = await fetcher.list_rows(start, today)
            garmin = await _garmin_zones(cache, tracker, no_cache)
        finally:
            cache.close()
        out = zones.status(data, today=today, garmin_zones=garmin, runs_since=_runs_since(data, rows))
        return Result(out, tracker.report())

    run_command("zones show", {"no_cache": no_cache}, work)


def _merge_estimate(fresh: dict, snap: dict, logged_before: int) -> None:
    """Apply what an estimate changed in its snapshot to the freshly locked file (records and current of
    concurrent commands are kept; the H4 memory is the snapshot's)."""
    fresh["estimates"].extend(snap["estimates"][logged_before:])
    top = snap.get("hr_max")
    if top:
        zones.update_hr_max(fresh, top["bpm"], activity=top.get("activity"), day=date.fromisoformat(top["date"]))
    fresh["h4"] = snap["h4"]


@app.command("estimate")
def estimate(weeks: int = typer.Option(DEFAULT_WEEKS, "--weeks", help="History window in weeks (extended to 2x)."),
             max_calls: int = typer.Option(DEFAULT_MAX_CALLS, "--max-calls",
                                           help="Hard cap on Garmin calls; the rest is deferred (partial estimate)."),
             include_wrist: bool = typer.Option(False, "--include-wrist", help="Also use wrist HR runs (low confidence)."),
             retry_h4: bool = typer.Option(False, "--retry-h4", help="Clear the alpha1-unreliable mark and try again."),
             save: bool = typer.Option(False, "--save", help="Also store a full record (source estimate)."),
             no_cache: bool = NO_CACHE_OPT) -> None:
    """Estimate LT1 and LT2 from recent runs; logs a summary locally (and stores a record with --save)."""
    async def work() -> Result:
        if weeks < 1:
            raise _bad("--weeks must be >= 1")
        if max_calls < 0:
            raise _bad("--max-calls must be >= 0")
        today = date.today()
        snap = zones.load()
        logged_before = len(snap["estimates"])
        cache, tracker = Cache(), CacheTracker()
        try:
            fetcher = GarminFetcher(cache, today=today, refresh=no_cache, tracker=tracker)
            result = await run_estimate(fetcher, snap, today=today, weeks=weeks, max_calls=max_calls,
                                        include_wrist=include_wrist, retry_h4=retry_h4)
        finally:
            cache.close()
        saved, refused = None, None
        with zones.transaction() as data:   # short lock: the reads above can take minutes
            _merge_estimate(data, snap, logged_before)
            if save and result["record"] is not None:
                try:
                    saved = zones.add_record(data, result["record"], today=today)
                except PaceError as err:
                    refused = err
        if refused is not None:
            raise PaceError("bad_args", f"{refused.message}; the estimate was logged but no record was saved",
                            hint="Inspect the candidate with a run without --save.")
        if saved is not None:
            result["record"] = saved
        return Result({**result, "saved": saved}, tracker.report())

    run_command("zones estimate", {"weeks": weeks, "max_calls": max_calls, "include_wrist": include_wrist,
                           "retry_h4": retry_h4, "save": save}, work)


# --- analyze ---

def _run_day(act: dict) -> date | None:
    s = act.get("summaryDTO") or {}
    for v in (s.get("startTimeLocal"), act.get("startTimeLocal"), s.get("startTimeGMT")):
        try:
            return date.fromisoformat(str(v)[:10])
        except ValueError:
            continue
    return None


def _newest_f3_hr(data: dict, today: date) -> int | None:
    since = (today - timedelta(days=F3_RECENT_DAYS)).isoformat()
    f3 = [r for r in data["records"] if r.get("source") == "field_test" and (r.get("details") or {}).get("test") == "F3"
          and r["date"] >= since and ((r.get("lt1") or {}).get("hr") is not None)]
    return max(f3, key=lambda r: (r["date"], r["id"]))["lt1"]["hr"] if f3 else None


def _rejected_ramp(reasons: list[str]) -> dict:
    return {"status": "rejected", "outcome": None, "lt1": None, "confidence": None, "bracketed": False,
            "saveable": False, "steps": [], "gate": None, "fit": None, "reasons": reasons, "notes": []}


async def _ramp_analysis(fetcher: GarminFetcher, activity_id: int, *, laps: list[dict], treadmill: bool) -> dict:
    """F3: the FIT RR data (cached, else one download), then thresholds.ramp; unreadable or rejected data is
    remembered in no_rr.json and reported as a rejected test."""
    try:
        fd = await fetcher.fit(activity_id)
    except fitmod.FitUnreadable as err:
        fitmod.mark_no_rr(activity_id, "fit_unreadable")
        return _rejected_ramp([f"FIT file unreadable: {err.message}"])
    if fd is None:
        return _rejected_ramp([f"no RR data (marked {fitmod.no_rr(activity_id) or 'none'})"])
    try:
        aligned = fitmod.aligned_rr(fd)
    except fitmod.RRRejected as exc:
        fitmod.mark_no_rr(activity_id, "rr_rejected")
        return _rejected_ramp([f"RR data rejected: {exc}"])
    return thresholds.ramp(aligned, laps=laps, treadmill=treadmill)


def _field_record(test: str, a: dict, *, activity_id: int, day: date, treadmill: bool, f3_hr: int | None,
                  wrist: bool) -> tuple[dict | None, str | None]:
    """(record --save would store, why --save refuses): methods doc 14.3, 14.4, 14.8."""
    tag = {"drift": "F1", "time_trial": "F2", "ramp": "F3"}[test]
    if a["status"] != "ok" or not a.get("saveable", test == "time_trial"):
        why = "; ".join(a.get("reasons") or []) or "no reason given"
        if test == "drift" and a["status"] == "ok":
            if a["verdict"] == "at LT1":   # uneven pace
                why = "at LT1 but with uneven pace; repeat at a steadier pace"
            else:
                why = f"{a['verdict']} (drift {a['drift_pct']}%); next test: start at HR {a['retest_hr']}"
        return None, f"{tag} not saved, {'status ' + a['status'] + ': ' if a['status'] != 'ok' else 'verdict '}{why}"
    notes = list(a.get("reasons") or []) + list(a.get("notes") or [])
    conf = a.get("confidence")
    if wrist:
        conf = "low"
        notes.append("wrist HR: confidence low")
    if test == "drift" and f3_hr is not None and abs(f3_hr - a["lt1"]["hr"]) <= thresholds.F3_DISAGREE:
        notes.append(f"agrees with the newest F3 LT1 HR {f3_hr} (within {thresholds.F3_DISAGREE} bpm)")
    kind = {"drift": "lt1", "time_trial": "lt2", "ramp": "lt1"}[test]
    details: dict[str, Any] = {"test": tag, "treadmill": treadmill, "bounds": a.get("bounds")}
    if test == "drift":
        details.update(drift_pct=a["drift_pct"], halves=a["halves"], excluded_s=a["excluded_s"],
                       temperature=a["temperature"], f3_hr=f3_hr)
    elif test == "time_trial":
        details.update(distance_m=a["distance_m"], speed_ratio=a["speed_ratio"], fade=a["fade"],
                       excluded_s=a["excluded_s"])
    else:
        details.update(fit=a["fit"], steps=a["steps"], gate=a["gate"])
    method = {"drift": "F1 drift test", "time_trial": "F2 30-minute time trial", "ramp": "F3 alpha1 ramp"}[test]
    return {"date": day.isoformat(), "source": "field_test", "method": method + (" (treadmill)" if treadmill else ""),
            kind: a[kind], "confidence": conf, "activities": [activity_id], "notes": "; ".join(notes) or None,
            "details": details}, None


@app.command("analyze")
def analyze(activity_id: int = typer.Argument(..., metavar="ACTIVITY_ID"),
            test: str = typer.Option(..., "--test", help=f"One of {', '.join(TESTS)}."),
            treadmill: bool = typer.Option(False, "--treadmill", help="Treadmill run: HR only, device speed ignored."),
            save: bool = typer.Option(False, "--save", help="Also store a field-test record (when the test allows it)."),
            no_cache: bool = NO_CACHE_OPT) -> None:
    """Analyze a test run (drift F1, time_trial F2, ramp F3); raises hr_max, ramp records its alpha1 gate outcome."""
    async def work() -> Result:
        if test not in TESTS:
            raise _bad(f"--test must be one of {', '.join(TESTS)}, got {test!r}")
        today = date.today()
        snap = zones.load()
        cache, tracker = Cache(), CacheTracker()
        try:
            fetcher = GarminFetcher(cache, today=today, refresh=no_cache, tracker=tracker)
            act = await fetcher.activity(activity_id)
            day = _run_day(act) or today
            fetcher.learn_day(activity_id, day)
            elapsed = (act.get("summaryDTO") or {}).get("elapsedDuration")
            row = {"elapsedDuration": elapsed}
            table = thresholds.samples(await fetcher.details(activity_id, maxchart=_maxchart(row)))
            laps = (await fetcher.splits(activity_id) or {}).get("lapDTOs") or []
            strap = thresholds.hr_source(act) == "strap"
            f3_hr = _newest_f3_hr(snap, today)
            if test == "ramp":
                analysis = await _ramp_analysis(fetcher, activity_id, laps=laps, treadmill=treadmill)
            elif test == "time_trial":
                analysis = thresholds.time_trial(table, laps=laps)
            else:
                records = None
                if fetcher.fit_cached(activity_id):   # cached FIT data only: it serves the temperature
                    records = ((await fetcher.fit(activity_id)) or {}).get("records") or None
                analysis = thresholds.drift(table, laps=laps, treadmill=treadmill, fit_records=records, f3_hr=f3_hr)
            calls = fetcher.calls
        finally:
            cache.close()
        candidate, refusal = _field_record(test, analysis, activity_id=activity_id, day=day, treadmill=treadmill,
                                           f3_hr=f3_hr, wrist=not strap)
        top = thresholds.max_hr(table) if strap else None
        saved = None
        with zones.transaction() as data:
            if top is not None:
                zones.update_hr_max(data, top, activity=activity_id, day=day)
            if analysis.get("outcome") is not None:
                zones.record_alpha1_outcome(data, analysis["outcome"], day=day, activity=activity_id)
            if save and candidate is not None:
                try:
                    saved = zones.add_record(data, candidate, today=today)
                except PaceError as err:
                    refusal = f"not saved: {err.message}"
            hr_max, h4 = data["hr_max"], copy.deepcopy(data["h4"])
        if save and saved is None:
            raise _bad(refusal or "not saved",
                       hint="Nothing was stored except hr_max and the alpha1 memory; run without --save to see the analysis.")
        out = {**analysis, "test": test, "activity_id": activity_id, "date": day.isoformat(),
               "hr_source": "strap" if strap else "wrist", "calls": calls, "hr_max": hr_max, "h4": h4,
               "record": saved or candidate, "saved": saved}
        return Result(out, tracker.report())

    run_command("zones analyze", {"activity_id": activity_id, "test": test, "treadmill": treadmill, "save": save}, work)


# --- local commands ---

@app.command("test-workout")
def test_workout(kind: str = typer.Argument(..., metavar="KIND", help=f"One of {', '.join(TESTS)}.")) -> None:
    """A workout DSL for a field test, targets from the current LT1 record or the newest estimate."""
    async def work() -> Result:
        data = zones.load()
        cur = zones.current(data)
        est = next((e["lt1_hr"] for e in reversed(data["estimates"]) if e.get("lt1_hr") is not None), None)
        return Result({"workout": thresholds.test_workout(kind, lt1_record=cur["lt1"], estimate_lt1_hr=est,
                                                         h2=zones.h2_of(cur["lt1"], cur["lt2"]))})

    run_command("zones test-workout", {"kind": kind}, work)


def _pace_s(text: str | None, flag: str) -> float | None:
    return None if text is None else float(round(1000.0 / parse_pace(text, flag)))


def _threshold(hr: int | None, pace: str | None, flag: str) -> dict | None:
    p = _pace_s(pace, flag)
    return {"hr": hr, "pace_s_per_km": p} if hr is not None or p is not None else None


@app.command("add")
def add(source: str = typer.Option(..., "--source", help=f"One of {', '.join(zones.SOURCES)}."),
        on: str | None = typer.Option(None, "--date", help="Record date; default today."),
        lt1_hr: int | None = typer.Option(None, "--lt1-hr"),
        lt1_pace: str | None = typer.Option(None, "--lt1-pace", help="m:ss per km."),
        lt2_hr: int | None = typer.Option(None, "--lt2-hr"),
        lt2_pace: str | None = typer.Option(None, "--lt2-pace", help="m:ss per km."),
        max_hr: int | None = typer.Option(None, "--max-hr"),
        rest_hr: int | None = typer.Option(None, "--rest-hr"),
        method: str | None = typer.Option(None, "--method", help="For a lab: its criterion, e.g. VT1/VT2."),
        confidence: str | None = typer.Option(None, "--confidence", help=f"One of {', '.join(zones.CONFIDENCES)}."),
        activity: list[int] | None = typer.Option(None, "--activity", help="Garmin activity id; repeatable."),
        notes: str | None = typer.Option(None, "--notes")) -> None:
    """Add a record; a lab record becomes current for each threshold it has unless that current one is newer."""
    async def work() -> Result:
        from pace.dates import parse_date
        today = date.today()
        record = {"date": parse_date(on).isoformat(), "source": source, "method": method,
                  "lt1": _threshold(lt1_hr, lt1_pace, "--lt1-pace"), "lt2": _threshold(lt2_hr, lt2_pace, "--lt2-pace"),
                  "max_hr": max_hr, "rest_hr": rest_hr, "confidence": confidence, "activities": activity or [],
                  "notes": notes}
        with zones.transaction() as data:
            stored = zones.add_record(data, record, today=today)
            return Result({"record": stored, "current": _current_view(data)})

    run_command("zones add", {"source": source, "date": on, "lt1_hr": lt1_hr, "lt1_pace": lt1_pace, "lt2_hr": lt2_hr,
                      "lt2_pace": lt2_pace, "max_hr": max_hr, "rest_hr": rest_hr, "method": method,
                      "confidence": confidence, "activity": activity, "notes": notes}, work)


@app.command("accept")
def accept(record_id: int = typer.Argument(..., metavar="ID"),
           only: str | None = typer.Option(None, "--only", help="lt1 or lt2: make it current for that threshold only.")) -> None:
    """Make a record current for every threshold it has (or one); refused when LT1 HR would reach LT2 HR."""
    async def work() -> Result:
        with zones.transaction() as data:
            zones.accept(data, record_id, only=only)
            return Result({"current": _current_view(data)})

    run_command("zones accept", {"id": record_id, "only": only}, work)


@app.command("remove")
def remove(record_id: int = typer.Argument(..., metavar="ID"),
           force: bool = typer.Option(False, "--force", help="Remove a record that is current (it becomes none).")) -> None:
    """Delete a record; refused while it is current unless --force."""
    async def work() -> Result:
        with zones.transaction() as data:
            removed = zones.remove(data, record_id, force=force)
            return Result({"removed": removed, "current": _current_view(data)})

    run_command("zones remove", {"id": record_id, "force": force}, work)
