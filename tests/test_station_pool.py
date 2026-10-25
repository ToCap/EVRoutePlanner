"""Shared station pool: one search for all routes, then each route keeps the stations in its own corridor."""
from ev_trip.application.charging import find_stations, plan_charging
from ev_trip.domain.charging import Station
from ev_trip.domain.models import Point, Route, Slice, VehicleParams
from ev_trip.domain.simulation import simulate_all
from ev_trip.domain.trip_report import TripReport


def _report(lon):
    route = Route([Point(48.0 + 0.01 * i, lon) for i in range(91)], 100.0, 90.0)
    slices = [Slice(0, 0.0, 100.0, "autoroute")]
    return TripReport("A", "B", route, slices, simulate_all(slices, VehicleParams(52.0, 90.0, 10.0), 15.0))


class _Source:
    def __init__(self, by_lon):
        self.by_lon, self.calls = by_lon, []

    def find(self, route, radius_km):
        self.calls.append((route.coordinates[0].lon, radius_km))
        return self.by_lon[route.coordinates[0].lon]


def test_find_stations_searches_each_route_once_and_merges_by_id():
    shared, only_a, only_b = Station("s", 48.5, 2.0), Station("a", 48.4, 2.0), Station("b", 48.6, 2.2)
    source = _Source({2.0: [shared, only_a], 2.2: [shared, only_b]})
    pool = find_stations([_report(2.0), _report(2.2)], 5.0, stations=source)
    assert sorted(st.id for st in pool) == ["a", "b", "s"] and len(source.calls) == 2


def test_plan_with_a_pool_keeps_only_the_stations_along_its_route():
    near, far = Station("near", 48.45, 2.0003), Station("far", 48.45, 2.5)
    plan = plan_charging(_report(2.0), 5.0, pool=[near, far], detour_fn=lambda *a: (1.0, 2.0), sleep=lambda s: None)
    assert plan.found == 1
