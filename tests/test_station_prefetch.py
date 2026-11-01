"""Station search split in two: network part (corridor, detours) fetched while the trip is refined, then the plan."""
import threading
from dataclasses import replace

import pytest

from ev_trip.application.charging import StationPrefetch, fetch_stations, plan_charging
from ev_trip.application.jobs import MemoryStore, TripService
from ev_trip.domain.charging import Station
from ev_trip.domain.config import ChargingConfig
from ev_trip.domain.errors import RoadClassificationError, RoutingError
from ev_trip.domain.models import Point, Route, Slice, VehicleParams
from ev_trip.domain.simulation import simulate_all
from ev_trip.domain.trip_report import TripReport
from interfaces.dash_ui.params import DEFAULTS, request_from_values


def _report(precision="fine"):
    route = Route([Point(48.0 + 0.01 * i, 2.0) for i in range(91)], 100.0, 90.0)
    slices = [Slice(0, 0.0, 100.0, "autoroute")]
    report = TripReport("A", "B", route, slices, simulate_all(slices, VehicleParams(52.0, 90.0, 10.0), 15.0))
    return replace(report, precision=precision)


class _Source:
    def __init__(self, stations=(), error=None):
        self.stations, self.error = list(stations), error

    def find(self, route, radius_km):
        if self.error:
            raise self.error
        return self.stations


NEAR, MID, FAR = Station("near", 48.2, 2.0003), Station("mid", 48.4, 2.0006), Station("far", 48.6, 2.0009)


def test_fetch_routes_the_closest_stations_first_up_to_the_cap_with_a_pause_between_requests():
    calls, pauses = [], []
    cfg = ChargingConfig(max_candidates=2)
    out = fetch_stations(_report().route, 5.0, cfg, stations=_Source([FAR, MID, NEAR]),
                         detour_fn=lambda r, st, km, c, cum: calls.append(st.id) or (1.0, 2.0), sleep=pauses.append)
    assert len(out.found) == 3 and calls == ["near", "mid"] and sorted(out.detours) == ["mid", "near"] and len(pauses) == 1
    assert out.error is None and out.osrm_error is None


def test_fetch_reports_a_short_cause_and_keeps_what_it_has():
    out = fetch_stations(_report().route, 5.0, stations=_Source(error=RoadClassificationError("Overpass indisponible : délai dépassé")))
    assert out.error == "Overpass indisponible : délai dépassé" and out.found == []

    def failing(r, st, km, c, cum):
        raise RoutingError("OSRM HS")

    out = fetch_stations(_report().route, 5.0, stations=_Source([NEAR, MID]), detour_fn=failing, sleep=lambda s: None)
    assert out.osrm_error == "OSRM HS" and len(out.found) == 2 and out.detours == {}


def test_plan_uses_the_prefetched_detours_and_routes_only_the_others():
    routed = []
    prefetch = StationPrefetch(found=[NEAR, MID], detours={"near": (1.0, 2.0)})
    plan = plan_charging(_report(), 5.0, prefetch=prefetch, detour_fn=lambda r, st, km, c, cum: routed.append(st.id) or (1.5, 3.0),
                         sleep=lambda s: None)
    assert routed == ["mid"] and plan.found == 2
    by_id = {s.station.id: s for s in plan.suggestions}
    assert by_id["near"].detour_km == 1.0 and not by_id["near"].detour_estimated and by_id["mid"].detour_km == 1.5


def test_plan_with_a_prefetch_failure_warns_and_does_not_search_again():
    plan = plan_charging(_report(), 5.0, prefetch=StationPrefetch(error="Overpass indisponible : HTTP 504"))
    assert plan.suggestions == [] and plan.warnings == ["Recherche des bornes impossible (Overpass indisponible : HTTP 504)."]
    plan = plan_charging(_report(), 5.0, prefetch=StationPrefetch(found=[NEAR], osrm_error="OSRM HS"), sleep=lambda s: None,
                         detour_fn=lambda *a: pytest.fail("no routing after an OSRM failure"))
    assert plan.suggestions and plan.suggestions[0].detour_estimated and "OSRM HS" in plan.warnings[0]


def test_service_starts_the_station_fetch_before_the_refinement_and_hands_it_to_the_plan():
    started, order = threading.Event(), []
    fetched = StationPrefetch(found=[NEAR])

    def prefetch(route, km, cfg):
        order.append("fetch")
        started.set()
        return fetched

    def refine(report):
        assert started.wait(2), "the fetch must run while the trip is refined"
        order.append("refine")
        return replace(report, precision="fine")

    seen = {}

    def plan(report, km, **kw):
        seen.update(kw)
        return "plan"

    svc = TripService(MemoryStore(), runner=lambda f: f(), run_trip=lambda *a, **k: _report("coarse"), refine=refine, plan=plan,
                      prefetch=prefetch)
    key, _ = svc.start(request_from_values({**DEFAULTS, "origin": "Paris", "destination": "Caen"}))
    entry = svc.get(key)
    assert order == ["fetch", "refine"] and seen["prefetch"] is fetched and entry.charging == "plan" and entry.state == "done"


def test_service_falls_back_to_a_plain_plan_when_the_fetch_thread_fails():
    def prefetch(route, km, cfg):
        raise RuntimeError("boom")

    seen = {}
    svc = TripService(MemoryStore(), runner=lambda f: f(), run_trip=lambda *a, **k: _report(), refine=lambda r: r,
                      plan=lambda report, km, **kw: seen.update(kw) or "plan", prefetch=prefetch)
    key, _ = svc.start(request_from_values({**DEFAULTS, "origin": "Paris", "destination": "Caen"}))
    assert "prefetch" not in seen and svc.get(key).charging == "plan"
