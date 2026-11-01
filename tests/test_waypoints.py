"""Intermediate stops: request, link, OSRM call, geocoding errors, service call, form editing."""
import pytest

from ev_trip.application.jobs import MAX_WAYPOINTS, MemoryStore, TripService
from ev_trip.application.trip import locate_via, run_trip
from ev_trip.domain.errors import GeocodingNotFound, GeocodingUnavailable
from ev_trip.domain.models import Point, Route, Slice, VehicleParams
from ev_trip.domain.simulation import simulate_all
from ev_trip.domain.trip_report import TripReport
from ev_trip.providers.osrm import osrm_routes
from interfaces.dash_ui import callbacks, components as ui
from interfaces.dash_ui.params import DEFAULTS, from_query, request_from_values, to_query

VALUES = {**DEFAULTS, "origin": "Paris", "destination": "Caen"}


def test_request_keeps_the_stops_in_order_ignores_empty_ones_and_changes_the_key():
    plain = request_from_values(VALUES)
    req = request_from_values({**VALUES, "via": [" Rouen ", "", "Lisieux"]})
    assert req.waypoints == ("Rouen", "Lisieux") and req.key() != plain.key() and plain.waypoints == ()


def test_stops_force_the_fast_profile_and_are_limited():
    assert request_from_values({**VALUES, "profile": "eco", "via": ["Rouen"]}).profile == "fast"
    assert request_from_values({**VALUES, "profile": "eco"}).profile == "eco"
    with pytest.raises(ValueError, match="Étapes"):
        request_from_values({**VALUES, "via": [f"V{i}" for i in range(MAX_WAYPOINTS + 1)]})


def test_stops_survive_the_url_round_trip():
    req = request_from_values({**VALUES, "via": ["Rouen", "Lisieux"]})
    assert from_query(to_query(req))["via"] == ["Rouen", "Lisieux"]
    assert "via" not in to_query(request_from_values(VALUES)) and from_query(to_query(request_from_values(VALUES)))["via"] == []


class _Session:
    def __init__(self):
        self.url = self.params = None

    def get(self, url, params=None, **kw):
        self.url, self.params = url, params
        return self

    def raise_for_status(self):
        pass

    def json(self):
        return {"code": "Ok", "routes": [{"distance": 1000.0, "duration": 60.0, "geometry": {"coordinates": [[2.0, 48.0], [2.1, 48.1]]}, "legs": []}]}


def test_osrm_gets_every_point_in_order_and_no_alternatives_with_stops():
    s = _Session()
    osrm_routes(Point(48.0, 2.0), Point(49.0, 3.0), s, via=[Point(48.5, 2.5)])
    assert s.url.endswith("/2.0,48.0;2.5,48.5;3.0,49.0") and "alternatives" not in s.params
    osrm_routes(Point(48.0, 2.0), Point(49.0, 3.0), s)
    assert s.url.endswith("/2.0,48.0;3.0,49.0") and s.params["alternatives"] == "3"


def test_stop_errors_name_the_stop():
    def geo(q):
        if q == "Rouan":
            raise GeocodingNotFound("Adresse introuvable : « Rouan »")
        if q == "Lisieux":
            raise GeocodingUnavailable("problème de connexion")
        return Point(48.0, 2.0)

    with pytest.raises(GeocodingNotFound) as err:
        locate_via(geo, ("Rouen", "Rouan"))
    assert str(err.value) == "Adresse introuvable : « Rouan » (étape 2). Vérifiez l'orthographe."
    with pytest.raises(GeocodingUnavailable) as err:
        locate_via(geo, ("Lisieux",))
    assert str(err.value) == "Impossible de localiser l'étape 1 : problème de connexion. Réessayez dans un instant."


def test_run_trip_passes_the_stops_to_the_route_provider_only_when_there_are_some():
    calls = []

    def route_fn(a, b, **kw):
        calls.append(kw)
        return Route([Point(48.0, 2.0), Point(48.9, 2.0)], 100.0, 60.0)

    vehicle = VehicleParams(52.0, 90.0, 10.0)
    run_trip("A", "B", 15.0, vehicle, geocode_fn=lambda q: Point(48.0, 2.0), route_fn=route_fn, use_overpass=False, refine=False)
    run_trip("A", "B", 15.0, vehicle, geocode_fn=lambda q: Point(48.0, 2.0), route_fn=route_fn, use_overpass=False, refine=False, via=("C",))
    assert calls[0] == {} and list(calls[1]) == ["via"] and calls[1]["via"] == [Point(48.0, 2.0)]


def test_service_passes_the_stops_to_the_runner():
    seen = []
    slices = [Slice(0, 0.0, 100.0, "autoroute")]
    report = TripReport("A", "B", Route([], 100.0, 60.0), slices, simulate_all(slices, VehicleParams(52.0, 90.0, 10.0), 15.0))

    def run(*a, **kw):
        seen.append(kw.get("via"))
        return report

    svc = TripService(MemoryStore(), runner=lambda f: None, run_trip=run, refine=lambda r: r, plan=lambda *a, **k: None)
    svc.start(request_from_values({**VALUES, "max_detour_km": 0}))
    svc.start(request_from_values({**VALUES, "max_detour_km": 0, "via": ["Rouen"]}))
    assert seen == [None, ("Rouen",)]


def test_form_editing_adds_before_the_limit_and_removes_the_clicked_stop():
    assert callbacks.edit_via("via-add", ["A"]) == ["A", ""]
    assert callbacks.edit_via("via-add", [f"V{i}" for i in range(MAX_WAYPOINTS)]) == [f"V{i}" for i in range(MAX_WAYPOINTS)]
    assert callbacks.edit_via({"type": "via-del", "index": 1}, ["A", "B", "C"]) == ["A", "C"]
    assert callbacks.edit_via("other", None) == []


def test_rows_have_an_input_and_a_delete_button_per_stop():
    rows = ui.via_rows(["Rouen", "Lisieux"])
    text = str(rows)
    assert len(rows) == 2 and "placeholder='Étape 2'" in text and "form-label" not in text and "'index': 1" in text and "via-del" in text and "Rouen" in text
    assert ui.via_rows(None) == []


def test_report_keeps_the_stops_and_the_map_shows_them_numbered():
    stops = []

    def route_fn(a, b, **kw):
        stops.append(kw.get("via"))
        return Route([Point(48.0, 2.0), Point(48.9, 2.0)], 100.0, 60.0)

    geo = lambda q: Point(48.5, 2.0) if q == "C" else Point(48.0, 2.0)  # noqa: E731
    report = run_trip("A", "B", 15.0, VehicleParams(52.0, 90.0, 10.0), geocode_fn=geo, route_fn=route_fn, use_overpass=False, refine=False, via=("C",))
    assert report.via == [Point(48.5, 2.0)] and stops == [[Point(48.5, 2.0)]]
    assert run_trip("A", "B", 15.0, VehicleParams(52.0, 90.0, 10.0), geocode_fn=geo, route_fn=route_fn, use_overpass=False, refine=False).via == []
    data = {"route_slices": [], "start": [48.0, 2.0], "end": [48.9, 2.0], "via": [[48.5, 2.0], [48.7, 2.0]]}
    layers = ui.route_layers(data)
    assert [str(l.children[0].children) for l in layers if getattr(l, "iconOptions", None)] == ["Étape 1", "Étape 2"]
    assert len(ui.route_layers({k: v for k, v in data.items() if k != "via"})) == 2
