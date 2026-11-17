from datetime import date

from pace.matching import match

TODAY = date(2026, 11, 17)


def sess(id_, day="2026-11-16", sport="running", intensity="easy", duration=None, distance=None, **kw):
    target = {"duration_s": duration, "distance_m": distance, "elevation_m": None}
    return {"id": id_, "date": day, "sport": sport, "title": f"s{id_}", "intensity": intensity, "target": target,
            "status": "planned", "activity_id": None, "garmin": None, "stale": False, **kw}


def act(id_, day="2026-11-16", type_="running", duration=3000, distance=10000.0, **kw):
    return {"activityId": id_, "activityType": {"typeKey": type_}, "startTimeLocal": f"{day} 07:00:00",
            "duration": duration, "movingDuration": duration, "distance": distance, "elevationGain": 50.0,
            "averageSpeed": 3.0, "averageHR": 140.0, "workoutId": None, **kw}


def sessions_of(out):
    return {s["id"]: s for d in out["days"] for s in d["sessions"]}


def unplanned_of(out):
    return [u["activity_id"] for d in out["days"] for u in d["unplanned"]]


def test_done_and_modified_threshold() -> None:
    out = match([sess(1, duration=3000)], [act(10, duration=3500)], today=TODAY)
    assert sessions_of(out)[1]["status"] == "done"
    out = match([sess(1, duration=3000)], [act(10, duration=3700)], today=TODAY)
    assert sessions_of(out)[1]["status"] == "modified"
    out = match([sess(1, distance=10000)], [act(10, distance=None)], today=TODAY)
    assert sessions_of(out)[1]["status"] == "done"


def test_global_closest_pairing() -> None:
    out = match([sess(1, duration=3000), sess(2, duration=3500)],
                [act(10, duration=3400), act(11, duration=6000)], today=TODAY)
    s = sessions_of(out)
    assert s[2]["actual"]["activity_id"] == 10
    assert s[1]["actual"]["activity_id"] == 11


def test_double_run_one_session() -> None:
    out = match([sess(1, duration=3000)], [act(10, duration=1200), act(11, duration=2900)], today=TODAY)
    assert sessions_of(out)[1]["actual"]["activity_id"] == 11
    assert unplanned_of(out) == [10]


def test_workout_id_link_beats_duration() -> None:
    s = sess(1, duration=3000, status="pushed", garmin={"workout_id": 77, "schedule_id": 5})
    out = match([s], [act(10, duration=3000), act(11, duration=6000, workoutId=77)], today=TODAY)
    assert sessions_of(out)[1]["actual"]["activity_id"] == 11
    assert unplanned_of(out) == [10]


def test_manual_link_outside_range() -> None:
    s1 = sess(1, duration=3000, activity_id=999)
    s2 = sess(2, duration=3000)
    out = match([s1, s2], [act(10)], today=TODAY)
    s = sessions_of(out)
    assert s[1]["status"] == "done"
    assert s[1]["actual"]["activity_id"] == 999 and s[1]["actual"]["duration_s"] is None
    assert s[2]["actual"]["activity_id"] == 10


def test_family_mapping_used() -> None:
    out = match([sess(1, sport="running"), sess(2, sport="cycling")],
                [act(10, type_="treadmill_running"), act(11, type_="e_bike_mountain")], today=TODAY)
    s = sessions_of(out)
    assert s[1]["actual"]["activity_id"] == 10 and s[2]["actual"]["activity_id"] == 11


def test_unmatched_classification() -> None:
    sessions = [
        sess(1), sess(2, sport="strength"), sess(3, intensity="rest"),
        sess(4, status="done"), sess(5, status="skipped"),
        sess(6, day="2026-11-20", status="pushed"), sess(7, day="2026-11-20"),
    ]
    s = sessions_of(match(sessions, [], today=TODAY))
    assert [s[i]["status"] for i in range(1, 8)] == ["missed", "unconfirmed", "rest", "done", "skipped",
                                                      "pushed", "planned"]
    assert s[4]["actual"] is None


def test_effort_pace_and_zones() -> None:
    a = act(10, averageSpeed=0, hrTimeInZone_1=10.0, hrTimeInZone_2=20.0, hrTimeInZone_3=None,
            hrTimeInZone_4=0.0, hrTimeInZone_5=5.0)
    out = match([sess(1, duration=3000)], [a, act(11, day="2026-11-15", averageSpeed=4.0)], today=TODAY,
                effort={10: {"rpe": 6, "feel": "strong"}})
    actual = sessions_of(out)[1]["actual"]
    assert actual["rpe"] == 6 and actual["feel"] == "strong"
    assert actual["avg_pace_s_per_km"] is None
    assert actual["hr_zones_s"] == [10.0, 20.0, None, 0.0, 5.0]
    un = out["days"][0]["unplanned"][0]
    assert un["activity_id"] == 11
    assert un["rpe"] is None and un["sport"] == "running"


def test_pace_from_speed() -> None:
    out = match([sess(1)], [act(10, averageSpeed=4.0)], today=TODAY)
    assert sessions_of(out)[1]["actual"]["avg_pace_s_per_km"] == 250.0


def test_totals_and_intensity() -> None:
    sessions = [sess(1, duration=3000, distance=10000, intensity="easy"),
                sess(2, duration=1800, intensity="hard"),
                sess(3, intensity="easy"),
                sess(4, duration=999, status="skipped"),
                sess(5, intensity="rest", duration=100)]
    out = match(sessions, [act(10, duration=3100, distance=10500.0), act(11, day="2026-11-10", type_="cycling",
                                                                         duration=600, distance=None)], today=TODAY)
    assert out["totals"]["running"] == {"planned": {"duration_s": 4800, "distance_m": 10000},
                                        "actual": {"duration_s": 3100, "distance_m": 10500.0}}
    assert out["totals"]["cycling"]["actual"] == {"duration_s": 600, "distance_m": 0}
    assert out["intensity_planned_s"] == {"easy": 3000, "hard": 1800}
    assert out["intensity_missing"] == 1
    assert [d["date"] for d in out["days"]] == sorted(d["date"] for d in out["days"])
