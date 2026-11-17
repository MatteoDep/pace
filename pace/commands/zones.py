"""`zones` commands. This part: the estimate orchestrator (methods doc 14.7).

`run_estimate` reads Garmin data through an injected `Fetcher` that checks the cache first and counts the calls
it makes; `--max-calls` is a hard cap on them. At the cap a cache hit is still served and a read that needs a
call raises `Deferred` instead; the orchestrator lists it in `deferred`, runs the methods on what was read and
logs the estimate as partial. Reads are made in the order of 14.7: list rows, personal records, sleep stats,
phase 1 picks in rank order, extension picks, phase 2 (drift) picks, FIT downloads by span score.
Methods: docs/superpowers/specs/2026-11-17-zones-methods.md sections 1, 6, 7 and 14.
"""

import math
import statistics
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any, Protocol

from pace import fit as fitmod
from pace import hrv, thresholds, zones
from pace.cache import Cache, CacheTracker
from pace.charts.sources import sleep_chunks, sleep_stats
from pace.commands._helpers import cached_call
from pace.envelope import PaceError
from pace.ratelimit import limiter
from pace.spec import TTL_STATIC

RR_SINCE = date(2026, 9, 1)     # first date with RR data on this athlete's watch (14.7)
DRIFT_WEEKS = 12                # H5 drift picks and H4 runs: the last 12 weeks
REST_HR_DAYS = 28
MAXCHART_CAP = 20000
LIST_TTL = 600
OLD_RUN_TTL = 60 * 86400        # zones reads of runs started more than 1 day ago
NEW_RUN_TTL = 3600
PR_DISTANCES_M = {1: 1000.0, 2: 1609.344, 3: 5000.0}   # Garmin PR typeId: 1 km, 1 mile, 5 km
STOP_CODES = ("rate_limited", "network")   # a read failing with these defers every later miss
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
            if err.code == "internal" and entry["kind"] == "fit":
                fitmod.mark_no_rr(entry["id"], "fit_unreadable")
                self.fit_errors.append({"id": entry["id"], "reason": "fit_unreadable", "detail": err.message})
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
    mark_no_rr(id, "fit_unreadable") when a FIT download cannot be parsed.

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
