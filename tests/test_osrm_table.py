"""Grouped detours: OSRM `table` call, same measure as one route per station, fallback to one-by-one."""
import math

import pytest

from ev_trip.application.charging import fetch_stations, osrm_detour, osrm_detours
from ev_trip.domain.charging import Station
from ev_trip.domain.config import ChargingConfig
from ev_trip.domain.errors import RoutingError
from ev_trip.domain.geo import cumulative_km
from ev_trip.domain.models import Point, Route
from ev_trip.providers.osrm import osrm_table

ROUTE = Route([Point(48.0 + 0.01 * i, 2.0) for i in range(91)], 100.0, 90.0)
STATIONS = [Station("a", 48.2, 2.01), Station("b", 48.45, 2.02), Station("c", 48.7, 2.015)]


def _km(p, q):
    return math.hypot((p.lat - q.lat) * 111.0, (p.lon - q.lon) * 74.0)


def _path(points):  # route through the points: km, minutes (1.2 min / km)
    km = sum(_km(a, b) for a, b in zip(points, points[1:]))
    return km, km * 1.2


def _table(points, sources, destinations):
    d = [[_km(points[s], points[t]) for t in destinations] for s in sources]
    return d, [[v * 1.2 for v in row] for row in d]


class _Session:
    def __init__(self, data):
        self.data, self.url, self.params = data, None, None

    def get(self, url, params=None, **kw):
        self.url, self.params = url, params
        return self

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_table_request_asks_for_distances_and_converts_units_keeping_missing_links():
    s = _Session({"code": "Ok", "distances": [[1000.0, None]], "durations": [[120.0, None]]})
    km, minutes = osrm_table([Point(48.0, 2.0), Point(48.1, 2.1), Point(48.2, 2.2)], [0], [1, 2], s)
    assert s.url.endswith("/table/v1/driving/2.0,48.0;2.1,48.1;2.2,48.2")
    assert s.params == {"sources": "0", "destinations": "1;2", "annotations": "duration,distance"}
    assert km == [[1.0, None]] and minutes == [[2.0, None]]
    with pytest.raises(RoutingError, match="TooBig"):
        osrm_table([Point(48.0, 2.0)], [0], [0], _Session({"code": "TooBig"}))


def test_grouped_detours_equal_one_route_per_station():
    cfg, cum = ChargingConfig(), cumulative_km(ROUTE.coordinates)
    from ev_trip.domain.geo import nearest_on_route

    items = [(st, nearest_on_route(ROUTE, st.point, cum)[0]) for st in STATIONS]
    grouped = osrm_detours(ROUTE, items, cfg, cum=cum, table_fn=_table)
    for st, km in items:
        single = osrm_detour(ROUTE, st, km, cfg, cum=cum, path_fn=_path)
        assert grouped[st.id] == pytest.approx(single)


def test_stations_without_a_road_link_are_left_out_and_requests_are_chunked():
    calls = []

    def table(points, sources, destinations):
        calls.append(len(points))
        d, t = _table(points, sources, destinations)
        d[0][0] = t[0][0] = None  # first station of the chunk: no road
        return d, t

    items = [(st, 10.0 * i) for i, st in enumerate(STATIONS, 1)]
    out = osrm_detours(ROUTE, items, ChargingConfig(), table_fn=table, chunk=2)
    assert calls == [6, 3] and sorted(out) == ["b"]


def test_fetch_uses_one_table_request_and_no_pause_then_falls_back_to_one_by_one():
    class Source:
        def find(self, route, radius_km):
            return STATIONS

    tables, pauses = [], []
    out = fetch_stations(ROUTE, 5.0, stations=Source(), table_fn=lambda *a: tables.append(1) or _table(*a), sleep=pauses.append)
    assert len(tables) == 1 and pauses == [] and sorted(out.detours) == ["a", "b", "c"]

    def failing(*a):
        raise RoutingError("TooBig")

    paths = []
    out = fetch_stations(ROUTE, 5.0, stations=Source(), table_fn=failing, path_fn=lambda pts: paths.append(1) or _path(pts), sleep=pauses.append)
    assert len(paths) == 3 and len(pauses) == 2 and sorted(out.detours) == ["a", "b", "c"] and out.osrm_error is None
