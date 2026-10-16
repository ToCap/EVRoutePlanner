"""Task manager (application layer): no Django."""
import pytest

from ev_trip.application.jobs import DONE, FAILED, REFINING, MemoryStore, TripRequest, TripService
from ev_trip.domain.charging import ChargingPlan
from ev_trip.domain.trip_report import TripReport
from test_charging import make_report

REQ = TripRequest("A", "B", 20, 52, 90, 10)


def service(**kw):
    jobs, calls = [], []
    report = make_report(100)

    def fake_run(origin, destination, temp_c, vehicle, **kwargs):
        calls.append(kwargs)
        return report

    svc = TripService(runner=jobs.append, run_trip=kw.pop("run_trip", fake_run), **kw)
    return svc, jobs, calls, report


def test_request_key_is_stable_and_depends_on_every_parameter():
    assert REQ.key() == TripRequest("A", "B", 20, 52, 90, 10).key()
    assert len({REQ.key(), TripRequest("A", "B", 21, 52, 90, 10).key(),
                TripRequest("A", "B", 20, 52, 90, 10, max_detour_km=5).key()}) == 3


def test_invalid_vehicle_raises_value_error():
    svc, *_ = service()
    with pytest.raises(ValueError):
        svc.start(TripRequest("A", "B", 20, 52, 150, 10))


def test_final_result_needs_no_job():
    svc, jobs, calls, report = service()
    key, entry = svc.start(REQ)
    assert entry.state == DONE and entry.report is report and jobs == []
    assert calls == [{"use_overpass": True, "refine": False}]
    assert svc.get(key) is entry
    assert svc.start(REQ)[1] is entry and len(calls) == 1  # result reused


def test_refinable_report_queues_one_job_then_publishes_the_result():
    refined = make_report(100)
    svc, jobs, _, report = service(refine=lambda r: refined)
    report.precision = "coarse"
    report.coarse = object()
    key, entry = svc.start(REQ)
    assert entry.state == REFINING and len(jobs) == 1
    svc.start(REQ)
    assert len(jobs) == 1  # no second task
    jobs[0]()
    done = svc.get(key)
    assert done.state == DONE and done.refined and done.report is refined and done.error is None


def test_charging_job_runs_after_refinement_with_the_refined_report():
    seen = {}
    refined = make_report(100)

    def plan(report, detour, tariffs=None, **_kw):
        seen.update(report=report, detour=detour, tariffs=tariffs)
        return ChargingPlan([], 0, 0, detour, "optionnelle")

    svc, jobs, _, report = service(refine=lambda r: refined, plan=plan, tariffs=lambda: "T")
    report.precision, report.coarse = "coarse", object()
    key, _ = svc.start(TripRequest("A", "B", 20, 52, 90, 10, max_detour_km=4))
    jobs[0]()
    assert seen == {"report": refined, "detour": 4, "tariffs": "T"}
    assert svc.get(key).charging.max_detour_km == 4 and svc.get(key).state == DONE


def test_charging_only_request_skips_refinement():
    called = []
    svc, jobs, _, _ = service(refine=lambda r: called.append(1) or r,
                              plan=lambda r, d, tariffs=None, **_kw: ChargingPlan([], 0, 0, d, "optionnelle"))
    key, entry = svc.start(TripRequest("A", "B", 20, 52, 90, 10, max_detour_km=2))
    assert entry.state == REFINING
    jobs[0]()
    assert called == [] and svc.get(key).refined is False and svc.get(key).charging is not None


def test_failures_are_published_and_partial_results_kept():
    def boom_refine(r):
        raise RuntimeError("HTTP 429")

    def boom_plan(r, d, tariffs=None, **_kw):
        raise RuntimeError("Overpass en panne")

    svc, jobs, _, report = service(refine=boom_refine, plan=boom_plan)
    report.precision, report.coarse = "coarse", object()
    key, _ = svc.start(TripRequest("A", "B", 20, 52, 90, 10, max_detour_km=3))
    jobs[0]()
    e = svc.get(key)
    assert e.state == FAILED and "affinage : HTTP 429" in e.error and "bornes : Overpass en panne" in e.error
    assert e.report is report and e.charging is None and not e.refined


def test_job_for_an_expired_entry_is_a_no_op():
    svc, *_ = service()
    svc.run_job("absent")


def test_memory_store_lock():
    store = MemoryStore()
    assert store.acquire("k") and not store.acquire("k") and store.get("k") is None
    assert isinstance(make_report(10), TripReport)


def test_request_chargers_are_validated_and_canonical():
    a = TripRequest("A", "B", 20, 52, 90, 10, chargers=("ac11", "dc100"))
    b = TripRequest("A", "B", 20, 52, 90, 10, chargers=("dc100", "ac11"))
    assert a.chargers == ("dc100", "ac11") and a.key() == b.key()  # same set, same key
    assert a.key() != REQ.key()
    with pytest.raises(ValueError, match="inconnu"):
        TripRequest("A", "B", 20, 52, 90, 10, chargers=("ac99",))


def test_chargers_reach_the_station_plan():
    seen = []

    def plan(report, detour, tariffs=None, **kw):
        seen.append(kw)
        return ChargingPlan([], 0, 0, detour, "optionnelle")

    svc, jobs, _, _ = service(plan=plan)
    key, entry = svc.start(TripRequest("A", "B", 20, 52, 90, 10, max_detour_km=3, chargers=("ac11",)))
    assert entry.chargers == ("ac11",)
    jobs[0]()
    assert seen == [{"chargers": ("ac11",)}]
