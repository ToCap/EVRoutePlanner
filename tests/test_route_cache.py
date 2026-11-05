"""Same itinerary, other environment or charge settings: the route is reused, only the simulation and the plan are redone."""
from dataclasses import replace

from ev_trip.application.charging import StationPrefetch
from ev_trip.application.jobs import ROUTE_CACHE_SIZE, MemoryStore, TripService
from ev_trip.domain.charging import Station
from interfaces.dash_ui.params import DEFAULTS, request_from_values
from test_station_prefetch import _report

BASE = {**DEFAULTS, "origin": "Paris", "destination": "Caen"}


def _service(calls, fetches, fail=False):
    def run_trip(*a, **k):
        calls.append(a)
        return _report("fine")

    def prefetch(route, km, cfg):
        fetches.append(km)
        return StationPrefetch(found=[Station("s", 48.5, 2.0)], error="panne" if fail else None)

    return TripService(MemoryStore(), runner=lambda f: f(), run_trip=run_trip, refine=lambda r: r,
                       plan=lambda report, km, **kw: kw.get("prefetch"), prefetch=prefetch)


def test_other_temperature_reuses_the_route_and_simulates_again():
    calls = []
    svc = _service(calls, [])
    k1, e1 = svc.start(request_from_values({**BASE, "temp_c": 15.0}))
    k2, e2 = svc.start(request_from_values({**BASE, "temp_c": -5.0}))
    assert k1 != k2 and len(calls) == 1
    assert e2.report.route is e1.report.route and e2.report.simulation.temp_c == -5.0
    assert e2.report.simulation.total_kwh > e1.report.simulation.total_kwh


def test_other_itinerary_computes_again():
    calls = []
    svc = _service(calls, [])
    svc.start(request_from_values(BASE))
    svc.start(request_from_values({**BASE, "destination": "Lille"}))
    svc.start(request_from_values({**BASE, "via": ["Rouen"]}))
    assert len(calls) == 3


def test_stations_are_fetched_once_per_route_and_detour():
    fetches = []
    svc = _service([], fetches)
    svc.start(request_from_values({**BASE, "max_detour_km": 5.0}))
    svc.start(request_from_values({**BASE, "max_detour_km": 5.0, "temp_c": 0.0}))
    assert fetches == [5.0]
    svc.start(request_from_values({**BASE, "max_detour_km": 8.0, "temp_c": 0.0}))
    assert fetches == [5.0, 8.0]


def test_failed_station_search_is_not_cached():
    fetches = []
    svc = _service([], fetches, fail=True)
    svc.start(request_from_values({**BASE, "max_detour_km": 5.0}))
    svc.start(request_from_values({**BASE, "max_detour_km": 5.0, "temp_c": 0.0}))
    assert fetches == [5.0, 5.0]


def test_only_the_last_routes_are_kept():
    calls = []
    svc = _service(calls, [])
    for n in range(ROUTE_CACHE_SIZE + 1):
        svc.start(request_from_values({**BASE, "destination": f"V{n}"}))
    svc.start(request_from_values({**BASE, "destination": "V0", "temp_c": 0.0}))  # evicted: computed again
    assert len(calls) == ROUTE_CACHE_SIZE + 2
