import json
import re

import pytest
import requests

from ev_trip.errors import RoutingError
from ev_trip.geo import cumulative_km, haversine_km, point_at_km, polyline_between
from ev_trip.models import Point, Route, VehicleParams
from ev_trip.trip import run_trip
from trips import views


def straight_route(km, n=100):
    pts = [Point(48.0 + (km / 111.195) * i / n, 0.0) for i in range(n + 1)]
    return Route(pts, km, km / 90 * 60, durations_s=[km / 90 * 3600 / n] * n)


class CityThenHighway:
    def __init__(self, origin):
        self.origin = origin

    def classify(self, points):
        return ["ville" if haversine_km(self.origin, p) < 7 else "autoroute" for p in points]


def fake_run_trip(origin, destination, temp_c, vehicle, cfg=None, **kwargs):
    route = straight_route(60)
    return run_trip(
        origin, destination, temp_c, vehicle, cfg,
        geocode_fn=lambda q: route.coordinates[0], route_fn=lambda a, b: route,
        classifier=CityThenHighway(route.coordinates[0]),
    )


PARAMS = {"origin": "Paris", "destination": "Saint-Lô", "temp_c": "8", "battery_kwh": "10",
          "start_soc": "90", "min_soc": "50", "use_overpass": "on"}


def page_data(response):
    html = response.content.decode()
    return json.loads(re.search(r'<script id="trip-data" type="application/json">(.*?)</script>', html, re.S).group(1))


def test_index_shows_empty_form(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'name="origin"' in r.content.decode() and "trip-data" not in r.content.decode()


def scenario(data, key):
    return next(s for s in data["scenarios"] if s["key"] == key)


def test_valid_request_renders_three_scenarios(client, monkeypatch):
    monkeypatch.setattr(views, "run_trip", fake_run_trip)
    r = client.get("/", PARAMS)
    html = r.content.decode()
    assert r.status_code == 200
    assert "interrompu" in html and "Détail par tranche" in html and "Comparaison des 3 estimations" in html
    for label in ("Optimiste", "Moyenne", "Pessimiste"):
        assert f">{label}</th>" in html or label in html
    data = page_data(r)
    assert [s["key"] for s in data["scenarios"]] == ["optimiste", "moyenne", "pessimiste"]
    assert data["trip_km"] == pytest.approx(60, abs=0.1) and data["min_soc"] == 50
    # 10 kWh, 90 % -> 50 % : 4 kWh utilisables ; plus la consommation est haute, plus l'arrêt est proche
    km = [scenario(data, k)["total_km"] for k in ("optimiste", "moyenne", "pessimiste")]
    assert km[0] > km[1] > km[2] and 15 < km[2] and km[0] < 30
    for s in data["scenarios"]:
        assert s["completed"] is False and s["alert"]["km"] == pytest.approx(s["total_km"], abs=0.1)
        assert s["chart"][0] == {"x": 0.0, "y": 90.0} and s["chart"][-1]["y"] == pytest.approx(50, abs=0.1)
    assert {s["type"] for s in data["route_slices"]} == {"ville", "autoroute"}
    assert all(len(s["points"]) >= 2 for s in data["route_slices"])
    assert {b["type"] for b in data["breakdown"]} == {"ville", "autoroute"}
    assert data["tile_url"].startswith("https://") and "{z}" in data["tile_url"]


def test_table_has_one_cell_per_scenario_and_blanks_after_the_stop(client, monkeypatch):
    monkeypatch.setattr(views, "run_trip", fake_run_trip)
    data = page_data(client.get("/", PARAMS))
    rows = data["rows"]
    assert len(rows) == len(data["route_slices"]) and all(len(r["cells"]) == 3 for r in rows)
    first, last = rows[0], rows[-1]
    assert all(c is not None and c["stop"] is False for c in first["cells"])
    assert all(c is None for c in last["cells"])  # tranche jamais atteinte
    # la tranche de l'arrêt est marquée, et le consommation augmente de l'optimiste au pessimiste
    stops = [next(r["cells"][i] for r in rows if r["cells"][i] and r["cells"][i]["stop"]) for i in range(3)]
    assert [c["soc"] for c in stops] == [50.0, 50.0, 50.0]
    assert first["cells"][0]["cons"] < first["cells"][1]["cons"] < first["cells"][2]["cons"]
    assert 'class="na"' in client.get("/", PARAMS).content.decode()


def test_complete_trip_for_all_scenarios(client, monkeypatch):
    monkeypatch.setattr(views, "run_trip", fake_run_trip)
    r = client.get("/", {**PARAMS, "battery_kwh": "100", "min_soc": "10"})
    data = page_data(r)
    assert all(s["completed"] and s["alert"] is None for s in data["scenarios"])
    assert all(c is not None and not c["stop"] for row in data["rows"] for c in row["cells"])
    assert "trajet interrompu" not in r.content.decode()
    assert scenario(data, "optimiste")["total_kwh"] < scenario(data, "pessimiste")["total_kwh"]


def test_mixed_outcome_only_flags_the_scenarios_that_stop(client, monkeypatch):
    monkeypatch.setattr(views, "run_trip", fake_run_trip)
    # 60 km (7 ville + 53 autoroute) a 8 °C ; capacite choisie pour que seul le pessimiste n'arrive pas
    data = page_data(client.get("/", {**PARAMS, "battery_kwh": "15", "start_soc": "100", "min_soc": "10"}))
    done = {s["key"]: s["completed"] for s in data["scenarios"]}
    assert done == {"optimiste": True, "moyenne": True, "pessimiste": False}


def test_referrer_policy_lets_osm_tiles_work(client):
    # Sans Referer, tile.openstreetmap.org répond 403 : Django ne doit pas le supprimer.
    r = client.get("/")
    assert r["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert 'name="referrer" content="strict-origin-when-cross-origin"' in r.content.decode()


@pytest.mark.parametrize(
    "field, value",
    [("temp_c", "abc"), ("temp_c", "70"), ("battery_kwh", "xyz"), ("battery_kwh", "0"),
     ("start_soc", "-1"), ("start_soc", "101"), ("start_soc", "abc"),
     ("min_soc", "-5"), ("min_soc", "100.5"), ("min_soc", "nan")],
)
def test_numeric_fields_reject_bad_values(client, monkeypatch, field, value):
    monkeypatch.setattr(views, "run_trip", lambda *a, **k: pytest.fail("ne doit pas être appelé"))
    r = client.get("/", {**PARAMS, field: value})
    assert r.status_code == 200 and field in r.context["form"].errors


def test_numeric_inputs_carry_browser_bounds(client):
    html = client.get("/").content.decode()
    assert re.search(r'name="start_soc"[^>]*min="0"[^>]*max="100"', html)
    assert re.search(r'name="min_soc"[^>]*min="0"[^>]*max="100"', html)
    assert 'type="number"' in html


def test_invalid_form_does_not_call_services(client, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("run_trip ne doit pas être appelé")

    monkeypatch.setattr(views, "run_trip", boom)
    r = client.get("/", {**PARAMS, "start_soc": "150"})
    assert r.status_code == 200 and "trip-data" not in r.content.decode()
    assert "start_soc" in r.context["form"].errors


@pytest.mark.parametrize("exc", [RoutingError("Aucun itinéraire trouvé"), requests.ConnectionError("réseau coupé")])
def test_service_errors_are_shown_not_raised(client, monkeypatch, exc):
    def failing(*a, **k):
        raise exc

    monkeypatch.setattr(views, "run_trip", failing)
    r = client.get("/", PARAMS)
    assert r.status_code == 200
    assert str(exc) in r.content.decode()


def test_value_error_becomes_form_error(client, monkeypatch):
    def failing(*a, **k):
        raise ValueError("L'itinéraire a une longueur nulle.")

    monkeypatch.setattr(views, "run_trip", failing)
    r = client.get("/", PARAMS)
    assert "longueur nulle" in r.content.decode()


def test_user_text_is_escaped(client, monkeypatch):
    monkeypatch.setattr(views, "run_trip", fake_run_trip)
    r = client.get("/", {**PARAMS, "origin": "<script>alert(1)</script>"})
    assert "<script>alert(1)</script>" not in r.content.decode()


# --- helpers géométriques ajoutés pour la carte ---------------------------------------
def test_polyline_between_matches_requested_span():
    route = straight_route(60)
    cum = cumulative_km(route.coordinates)
    line = polyline_between(route, 10, 20, cum)
    assert haversine_km(line[0], route.coordinates[0]) == pytest.approx(10, abs=0.05)
    assert haversine_km(line[-1], route.coordinates[0]) == pytest.approx(20, abs=0.05)
    assert sum(haversine_km(a, b) for a, b in zip(line, line[1:])) == pytest.approx(10, abs=0.05)


def test_point_at_km_endpoints():
    route = straight_route(60)
    assert point_at_km(route, 0) == route.coordinates[0]
    assert haversine_km(point_at_km(route, 60), route.coordinates[-1]) < 0.05
