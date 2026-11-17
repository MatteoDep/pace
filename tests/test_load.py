from datetime import date

from pace import load

TODAY = date(2026, 11, 18)  # Wednesday


def act(day, km=10.0, elev=0, secs=3600, type_="running", gmt=None, hhmm="07:00:00"):
    return {"activityId": 1, "startTimeLocal": f"{day} {hhmm}", "startTimeGMT": gmt or f"{day} {hhmm}",
            "activityType": type_, "distance": km * 1000, "elevationGain": elev, "movingDuration": secs,
            "duration": secs}


def ses(day, km=10.0, sport="running", status="planned", intensity="easy", target=None, sid=1):
    t = {"duration_s": 3600, "distance_m": km * 1000 if km is not None else None, "elevation_m": 100}
    return {"id": sid, "date": day, "sport": sport, "title": "t", "intensity": intensity, "status": status,
            "target": target if target is not None else t}


def agg(acts, sess, start, end, sport="running", today=TODAY):
    return load.aggregate(acts, sess, start=start, end=end, today=today, sport=sport)


def by_day(res):
    return {r["date"]: r for r in res["days"]}


def test_week_rows_split() -> None:
    acts = [act("2026-11-10"), act("2026-11-16")]
    sess = [ses("2026-11-19"), ses("2026-11-24")]
    res = agg(acts, sess, date(2026, 11, 9), date(2026, 11, 29))
    w = {r["week"]: r for r in res["weeks"]}
    assert list(w) == ["2026-W46", "2026-W47", "2026-W48"]
    assert w["2026-W46"]["actual"]["km"] == 10 and w["2026-W46"]["planned"] is None
    assert w["2026-W46"]["projected"] == w["2026-W46"]["actual"]
    cur = w["2026-W47"]
    assert cur["actual"]["km"] == 10 and cur["planned"]["km"] == 10 and cur["projected"]["km"] == 20
    nxt = w["2026-W48"]
    assert nxt["actual"] is None and nxt["planned"]["km"] == 10 and nxt["projected"]["km"] == 10
    assert nxt["start"] == "2026-11-23"
    assert res["range"] == {"from": "2026-11-09", "to": "2026-11-29"} and res["sport"] == "running"


def test_effective_and_rolling7() -> None:
    acts = [act("2026-11-16"), act("2026-11-17")]
    sess = [ses("2026-11-19", 12)]
    d = by_day(agg(acts, sess, date(2026, 11, 16), date(2026, 11, 22)))
    assert d["2026-11-17"]["effective"]["km"] == 10 and d["2026-11-17"]["rolling7"]["km"] == 20
    assert d["2026-11-18"]["effective"]["km"] == 0 and d["2026-11-18"]["rolling7"]["km"] == 20
    assert d["2026-11-19"]["effective"]["km"] == 12 and d["2026-11-19"]["rolling7"]["km"] == 32
    assert d["2026-11-19"]["actual"] is None and d["2026-11-18"]["actual"]["km"] == 0
    assert d["2026-11-19"]["planned"]["km"] == 12 and d["2026-11-18"]["planned"] is None


def test_sunday_equals_projected() -> None:
    acts = [act("2026-10-20", 7, 50), act("2026-11-03", 11.5, 80), act("2026-11-17", 9.3, 10, 2500)]
    sess = [ses("2026-11-18", 8.7), ses("2026-11-25", 13.1, sid=2), ses("2026-11-29", 21, sid=3)]
    res = agg(acts, sess, date(2026, 10, 26), date(2026, 11, 29))
    d = by_day(res)
    assert len(res["weeks"]) == 5
    for w in res["weeks"]:
        sunday = date.fromisoformat(w["start"]).toordinal() + 6
        row = d[date.fromordinal(sunday).isoformat()]
        for m in load.METRICS:
            assert row["rolling7"][m] == w["projected"][m]


def test_first_days_complete() -> None:
    acts = [act("2026-11-10"), act("2026-11-11"), act("2026-11-12")]
    res = agg(acts, [], date(2026, 11, 13), date(2026, 11, 15))
    d = by_day(res)
    assert list(d) == ["2026-11-13", "2026-11-14", "2026-11-15"]
    assert d["2026-11-13"]["rolling7"]["km"] == 30 and d["2026-11-15"]["rolling7"]["km"] == 30


def test_today_rule_respects_sport() -> None:
    ride = act("2026-11-18", 40, type_="cycling")
    sess = [ses("2026-11-18", 12)]
    d = by_day(agg([ride], sess, date(2026, 11, 16), date(2026, 11, 22)))["2026-11-18"]
    assert d["effective"]["km"] == 12
    d = by_day(agg([ride], sess, date(2026, 11, 16), date(2026, 11, 22), sport="all"))["2026-11-18"]
    assert d["effective"]["km"] == 40
    run = act("2026-11-18", 5)
    d = by_day(agg([run], sess, date(2026, 11, 16), date(2026, 11, 22)))["2026-11-18"]
    assert d["effective"]["km"] == 5 and d["planned"]["km"] == 12


def test_today_rule_is_per_metric_set() -> None:
    run = act("2026-11-18", 5, elev=0)
    sess = [ses("2026-11-18", 12)]  # planned elevation 100 is not mixed in
    d = by_day(agg([run], sess, date(2026, 11, 16), date(2026, 11, 22)))["2026-11-18"]
    assert d["effective"]["elev_m"] == 0


def test_local_not_gmt() -> None:
    a = act("2026-11-16", hhmm="00:30:00", gmt="2026-11-15 23:30:00")
    b = act("2026-11-15", hhmm="23:30:00", gmt="2026-11-16 00:30:00")
    d = by_day(agg([a, b], [], date(2026, 11, 15), date(2026, 11, 16)))
    assert d["2026-11-16"]["actual"]["km"] == 10 and d["2026-11-15"]["actual"]["km"] == 10


def test_iso_year_boundary() -> None:
    assert load.week_key(date(2027, 1, 2)) == "2026-W53"
    res = agg([act("2027-01-02")], [], date(2026, 12, 21), date(2027, 1, 10), today=date(2027, 1, 5))
    assert [w["week"] for w in res["weeks"]] == ["2026-W52", "2026-W53", "2027-W01"]
    assert res["weeks"][1]["actual"]["km"] == 10


def test_missing_targets_and_exclusions() -> None:
    sess = [ses("2026-11-19", None, sid=1), ses("2026-11-19", 5, status="skipped", sid=2),
            ses("2026-11-19", 5, intensity="rest", sid=3), ses("2026-11-20", 6, sport="cycling", sid=4),
            ses("2026-11-20", 3, status="done", sid=5)]
    d = by_day(agg([], sess, date(2026, 11, 16), date(2026, 11, 22)))
    p = d["2026-11-19"]["planned"]
    assert p["km"] == 0 and p["missing"] == {"km": 1, "elev_m": 0, "hours": 0}
    assert d["2026-11-20"]["planned"]["km"] == 3
    d = by_day(agg([act("2026-11-16", 20, type_="cycling")], sess, date(2026, 11, 16), date(2026, 11, 22)))
    assert d["2026-11-16"]["actual"]["km"] == 0
    d = by_day(agg([act("2026-11-16", 20, type_="cycling")], sess, date(2026, 11, 16), date(2026, 11, 22), sport="all"))
    assert d["2026-11-16"]["actual"]["km"] == 20 and d["2026-11-20"]["planned"]["km"] == 9


def test_hours_use_moving_duration_else_duration() -> None:
    a = act("2026-11-16", secs=3600)
    a["movingDuration"] = 0
    a["duration"] = 1800
    d = by_day(agg([a], [], date(2026, 11, 16), date(2026, 11, 16)))
    assert d["2026-11-16"]["actual"]["hours"] == 0.5


def test_default_range() -> None:
    plan = {"sessions": []}
    assert load.default_range(plan, today=TODAY, weeks=2, weeks_ahead=None) == (date(2026, 11, 9), date(2026, 11, 22))
    old = {"sessions": [{"date": "2026-10-10"}]}
    assert load.default_range(old, today=TODAY, weeks=1, weeks_ahead=None)[1] == date(2026, 11, 22)
    far = {"sessions": [{"date": "2027-08-30"}]}
    cap = date(2026, 11, 22).toordinal() + 26 * 7
    assert load.default_range(far, today=TODAY, weeks=1, weeks_ahead=None)[1].toordinal() == cap
    assert load.default_range(plan, today=TODAY, weeks=1, weeks_ahead=30)[1].toordinal() == cap
    assert load.default_range(far, today=TODAY, weeks=1, weeks_ahead=1)[1] == date(2026, 11, 29)
