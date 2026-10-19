"""Route profiles: fast vs consumption selection, merging of identical routes, OSRM alternatives."""
import pytest

from ev_trip.domain.route_profiles import ProfileReports, choose_profiles, distinct_indices


def test_fast_is_the_shortest_duration_and_eco_the_least_energy_within_tolerance():
    # (duration min, energy kWh): 0 = motorway, 1 = slightly slower and cheaper, 2 = much slower and cheapest
    routes = [(200.0, 60.0), (220.0, 52.0), (300.0, 40.0)]
    assert choose_profiles(routes, 15.0) == (0, 1)
    assert choose_profiles(routes, 60.0) == (0, 2)  # wider tolerance admits the slow route
    assert choose_profiles(routes, 0.0) == (0, 0)  # no tolerance: same route


def test_ties_prefer_lower_energy_for_fast_and_shorter_duration_for_eco():
    assert choose_profiles([(100.0, 20.0), (100.0, 18.0)], 10.0) == (1, 1)
    assert choose_profiles([(100.0, 18.0), (105.0, 18.0)], 10.0) == (0, 0)


def test_single_route_is_both_profiles_and_no_route_is_an_error():
    assert choose_profiles([(100.0, 20.0)], 15.0) == (0, 0)
    with pytest.raises(ValueError):
        choose_profiles([], 15.0)


def test_distinct_indices_merges_near_identical_routes():
    metrics = [(300.0, 200.0), (300.5, 200.4), (310.0, 215.0)]
    assert distinct_indices(metrics) == [0, 2]


def test_profile_reports_same_flag():
    a, b = object(), object()
    assert ProfileReports(a, a).same and not ProfileReports(a, b).same


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _Session:
    def __init__(self, routes):
        self.routes, self.params = routes, None

    def get(self, url, params=None, headers=None, timeout=None):
        self.params = params
        return _Resp({"code": "Ok", "routes": self.routes})


def _osrm_item(km, minutes):
    return {"geometry": {"coordinates": [[2.0, 48.0], [2.1, 48.1]]}, "distance": km * 1000, "duration": minutes * 60, "legs": []}


def test_osrm_routes_returns_alternatives_and_osrm_route_the_first_only():
    from ev_trip.domain.models import Point
    from ev_trip.providers.osrm import osrm_route, osrm_routes

    a, b = Point(48.0, 2.0), Point(48.1, 2.1)
    session = _Session([_osrm_item(300, 200), _osrm_item(310, 215)])
    routes = osrm_routes(a, b, session)
    assert [round(r.duration_min) for r in routes] == [200, 215] and session.params["alternatives"] == "3"
    assert round(osrm_route(a, b, session).duration_min) == 200 and "alternatives" not in session.params
