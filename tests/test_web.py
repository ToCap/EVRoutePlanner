import json
import re

import pytest
import requests

from ev_trip.domain.errors import RoutingError
from ev_trip.domain.geo import cumulative_km, haversine_km, point_at_km, polyline_between
from ev_trip.domain.models import Point, Route, VehicleParams
from ev_trip.application.trip import run_trip
from interfaces.django_ui import views


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
    monkeypatch.setattr(views.service, "run_trip", fake_run_trip)
    r = client.get("/", PARAMS)
    html = r.content.decode()
    assert r.status_code == 200
    assert "interrompu" in html and "Détail par tranche" in html and "Comparaison des 3 estimations" in html
    for label in ("Optimiste", "Moyenne", "Pessimiste"):
        assert f">{label}</th>" in html or label in html
    data = page_data(r)
    assert [s["key"] for s in data["scenarios"]] == ["optimiste", "moyenne", "pessimiste"]
    assert data["trip_km"] == pytest.approx(60, abs=0.1) and data["min_soc"] == 50
    # 10 kWh, 90 % -> 50 %: 4 kWh usable; the higher the consumption, the earlier the stop
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
    monkeypatch.setattr(views.service, "run_trip", fake_run_trip)
    data = page_data(client.get("/", PARAMS))
    rows = data["rows"]
    assert len(rows) == len(data["route_slices"]) and all(len(r["cells"]) == 3 for r in rows)
    first, last = rows[0], rows[-1]
    assert all(c is not None and c["stop"] is False for c in first["cells"])
    assert all(c is None for c in last["cells"])  # slice never reached
    # the stop slice is marked, and consumption rises from optimistic to pessimistic
    stops = [next(r["cells"][i] for r in rows if r["cells"][i] and r["cells"][i]["stop"]) for i in range(3)]
    assert [c["soc"] for c in stops] == [50.0, 50.0, 50.0]
    assert first["cells"][0]["cons"] < first["cells"][1]["cons"] < first["cells"][2]["cons"]
    assert 'class="na"' in client.get("/", PARAMS).content.decode()


def test_complete_trip_for_all_scenarios(client, monkeypatch):
    monkeypatch.setattr(views.service, "run_trip", fake_run_trip)
    r = client.get("/", {**PARAMS, "battery_kwh": "100", "min_soc": "10"})
    data = page_data(r)
    assert all(s["completed"] and s["alert"] is None for s in data["scenarios"])
    assert all(c is not None and not c["stop"] for row in data["rows"] for c in row["cells"])
    assert "trajet interrompu" not in r.content.decode()
    assert scenario(data, "optimiste")["total_kwh"] < scenario(data, "pessimiste")["total_kwh"]


def test_mixed_outcome_only_flags_the_scenarios_that_stop(client, monkeypatch):
    monkeypatch.setattr(views.service, "run_trip", fake_run_trip)
    # 60 km (7 city + 53 motorway) at 8 °C; capacity chosen so only the pessimistic scenario fails to arrive
    data = page_data(client.get("/", {**PARAMS, "battery_kwh": "15", "start_soc": "100", "min_soc": "10"}))
    done = {s["key"]: s["completed"] for s in data["scenarios"]}
    assert done == {"optimiste": True, "moyenne": True, "pessimiste": False}


def test_referrer_policy_lets_osm_tiles_work(client):
    # Without a Referer, tile.openstreetmap.org returns 403: Django must not strip it.
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
    monkeypatch.setattr(views.service, "run_trip", lambda *a, **k: pytest.fail("ne doit pas être appelé"))
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

    monkeypatch.setattr(views.service, "run_trip", boom)
    r = client.get("/", {**PARAMS, "start_soc": "150"})
    assert r.status_code == 200 and "trip-data" not in r.content.decode()
    assert "start_soc" in r.context["form"].errors


@pytest.mark.parametrize("exc", [RoutingError("Aucun itinéraire trouvé"), requests.ConnectionError("réseau coupé")])
def test_service_errors_are_shown_not_raised(client, monkeypatch, exc):
    def failing(*a, **k):
        raise exc

    monkeypatch.setattr(views.service, "run_trip", failing)
    r = client.get("/", PARAMS)
    assert r.status_code == 200
    assert str(exc) in r.content.decode()


def test_value_error_becomes_form_error(client, monkeypatch):
    def failing(*a, **k):
        raise ValueError("L'itinéraire a une longueur nulle.")

    monkeypatch.setattr(views.service, "run_trip", failing)
    r = client.get("/", PARAMS)
    assert "longueur nulle" in r.content.decode()


def test_user_text_is_escaped(client, monkeypatch):
    monkeypatch.setattr(views.service, "run_trip", fake_run_trip)
    r = client.get("/", {**PARAMS, "origin": "<script>alert(1)</script>"})
    assert "<script>alert(1)</script>" not in r.content.decode()


# --- geometry helpers added for the map ---------------------------------------
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


# --- first estimate + background refinement -----------------------------------------
from ev_trip.application.trip import refine_trip  # noqa: E402
from test_sampling import KmClassifier, stepped_route  # noqa: E402

TWO_PHASE = {**PARAMS, "battery_kwh": "100", "min_soc": "10"}


@pytest.fixture
def two_phase(monkeypatch):
    """run_trip returns a first estimate; the refinement is queued instead of started."""
    route = stepped_route()
    classifier = KmClassifier(route.coordinates[0])
    jobs = []

    def coarse_run_trip(origin, destination, temp_c, vehicle, cfg=None, **kwargs):
        assert kwargs.get("refine") is False  # the view always asks for the fast version
        return run_trip(origin, destination, temp_c, vehicle, cfg, geocode_fn=lambda q: route.coordinates[0],
                        route_fn=lambda a, b: route, classifier=classifier, refine=False)

    monkeypatch.setattr(views.service, "run_trip", coarse_run_trip)
    monkeypatch.setattr(views.service, "refine", lambda report: refine_trip(report, classifier))
    monkeypatch.setattr(views.service, "runner", jobs.append)
    return jobs


def page_key(response):
    return re.search(r"/status/([0-9a-f]{40})/", response.content.decode()).group(1)


def test_first_response_is_the_coarse_estimate_and_queues_one_refinement(client, two_phase):
    r = client.get("/", TWO_PHASE)
    html = r.content.decode()
    assert r.status_code == 200 and "Première estimation" in html and "affinage en arrière-plan" in html
    assert len(two_phase) == 1
    assert r.context["refine"]["state"] == "refining"
    assert not any(row["type"] == "ville" and row["start_km"] > 7 for row in page_data(r)["rows"])  # village not seen
    status = client.get(f"/status/{page_key(r)}/")
    assert status.status_code == 200 and status.json()["state"] == "refining"


def test_same_request_does_not_start_a_second_refinement(client, two_phase):
    client.get("/", TWO_PHASE)
    r = client.get("/", TWO_PHASE)
    assert len(two_phase) == 1 and r.context["refine"]["state"] == "refining"


def test_when_refinement_finishes_the_page_shows_the_refined_result(client, two_phase):
    first = client.get("/", TWO_PHASE)
    key = page_key(first)
    two_phase[0]()  # background task execution
    assert client.get(f"/status/{key}/").json() == {"state": "done", "error": None}
    r = client.get("/", TWO_PHASE)
    html = r.content.decode()
    assert "Résultat affiné" in html and "affinage en arrière-plan" not in html
    assert f"/status/{key}/" not in html  # no more polling
    rows = page_data(r)["rows"]
    assert any(row["type"] == "ville" and 29.5 < row["start_km"] < 30.5 for row in rows)  # village detected
    assert len(rows) > len(page_data(first)["rows"])
    assert len(two_phase) == 1  # no new refinement started


def test_failed_refinement_keeps_the_first_estimate_and_says_so(client, two_phase, monkeypatch):
    def boom(report):
        raise RuntimeError("Overpass indisponible : HTTP 429")

    monkeypatch.setattr(views.service, "refine", boom)
    key = page_key(client.get("/", TWO_PHASE))
    two_phase[0]()
    assert client.get(f"/status/{key}/").json()["state"] == "failed"
    r = client.get("/", TWO_PHASE)
    assert "Affinage impossible" in r.content.decode() and "HTTP 429" in r.content.decode()
    assert page_data(r)["rows"]  # the first-pass result stays displayed
    assert f"/status/{key}/" not in r.content.decode()


def test_status_of_an_unknown_or_expired_result_is_404(client):
    r = client.get("/status/" + "0" * 40 + "/")
    assert r.status_code == 404 and r.json() == {"state": "unknown"}


def test_trip_without_refinement_needs_no_background_job(client, monkeypatch):
    jobs = []
    monkeypatch.setattr(views.service, "run_trip", fake_run_trip)  # route without steps: final result
    monkeypatch.setattr(views.service, "runner", jobs.append)
    r = client.get("/", PARAMS)
    assert jobs == [] and r.context["refine"]["state"] == "done"
    assert "affinage en arrière-plan" not in r.content.decode()


# --- charging stations ---------------------------------------------------------------------
from ev_trip.application import charging as charging_mod  # noqa: E402
from ev_trip.domain import charging as charging_dom  # noqa: E402
from ev_trip.providers.overpass import OverpassStationSource  # noqa: E402

CHARGING = {**PARAMS, "battery_kwh": "100", "min_soc": "10", "max_detour_km": "5"}


class _StationClient:
    def __init__(self, elements):
        self.elements = elements

    def run(self, query):
        return self.elements


def _elements():
    lon = 1.5 / (111.195 * 0.669)  # ~1.5 km east of the route (lon 0)
    return [
        {"type": "node", "id": 1, "lat": 48.2, "lon": lon,
         "tags": {"amenity": "charging_station", "name": "Borne Test", "operator": "Ionity",
                  "socket:type2_combo:output": "150 kW"}},
        {"type": "node", "id": 2, "lat": 48.4, "lon": lon, "tags": {"amenity": "charging_station", "name": "Autre"}},
    ]


@pytest.fixture
def with_charging(monkeypatch):
    jobs = []
    monkeypatch.setattr(views.service, "run_trip", fake_run_trip)
    monkeypatch.setattr(views.service, "runner", jobs.append)
    real = charging_mod.plan_charging

    def fake_plan(report, detour, tariffs=None, **_kw):
        return real(report, detour, tariffs=tariffs, stations=OverpassStationSource(_StationClient(_elements())),
                    detour_fn=lambda r, st, km, cfg, cum: (1.0, 2.0), sleep=lambda s: None)

    monkeypatch.setattr(views.service, "plan", fake_plan)
    return jobs


def test_detour_zero_or_empty_means_no_charging_job(client, with_charging):
    no_field = {k: v for k, v in CHARGING.items() if k != "max_detour_km"}
    for params in ({**CHARGING, "max_detour_km": "0"}, {**CHARGING, "max_detour_km": ""}, no_field):
        r = client.get("/", params)
        assert r.context["refine"]["state"] == "done" and r.context["refine"]["charging_requested"] is False
        assert "Bornes de recharge" not in r.content.decode() and "charging" not in page_data(r)
    assert with_charging == []


def test_charging_search_runs_in_the_background_then_shows_ranked_stations(client, with_charging):
    first = client.get("/", CHARGING)
    html = first.content.decode()
    assert len(with_charging) == 1 and first.context["refine"]["state"] == "refining"
    assert "Recherche des bornes de recharge en cours" in html and "affinage en arrière-plan" not in html
    assert "charging" not in page_data(first)
    key = page_key(first)

    with_charging[0]()
    assert client.get(f"/status/{key}/").json() == {"state": "done", "error": None}
    r = client.get("/", CHARGING)
    html = r.content.decode()
    data = page_data(r)["charging"]
    assert len(with_charging) == 1  # no second task
    assert "Bornes de recharge (détour max 5 km)" in html and "Borne Test" in html and "Ionity" in html
    assert [b["rank"] for b in data["rows"]] == [1, 2] and data["rows"][0]["score"] >= data["rows"][1]["score"]
    row = next(b for b in data["rows"] if b["name"] == "Borne Test")
    assert row["charge_type"] == "rapide" and row["power_kw"] == 150 and row["detour_km"] == 1.0
    assert row["price"] is None and {"detour", "need", "charge", "price"} == set(row["sub"])
    assert row["color"] in ("#2e9e5b", "#e08a1e", "#8a93a0")
    assert f"/status/{key}/" not in html


def test_tariff_file_is_used_by_the_web_layer(client, with_charging, settings, tmp_path):
    tar = tmp_path / "tariffs.json"
    tar.write_text(json.dumps({"operators": {"ionity": {"rapide": 0.69}}}), encoding="utf-8")
    settings.TARIFFS_FILE = str(tar)
    client.get("/", CHARGING)
    with_charging[0]()
    row = next(b for b in page_data(client.get("/", CHARGING))["charging"]["rows"] if b["name"] == "Borne Test")
    assert row["price"] == 0.69 and row["price_source"] == "tarif"


def test_missing_or_broken_tariff_file_is_ignored(client, with_charging, settings, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{pas du json", encoding="utf-8")
    for path in (str(tmp_path / "absent.json"), str(bad)):
        client.get("/", {**CHARGING, "temp_c": path[-9:].__hash__() % 10})  # different cache key
        settings.TARIFFS_FILE = path
    with_charging.clear()
    settings.TARIFFS_FILE = str(bad)
    client.get("/", {**CHARGING, "temp_c": "11"})
    with_charging[0]()
    r = client.get("/", {**CHARGING, "temp_c": "11"})
    assert page_data(r)["charging"]["rows"] and r.context["refine"]["state"] == "done"


def test_charging_failure_is_reported_and_trip_stays_visible(client, with_charging, monkeypatch):
    def boom(report, detour, tariffs=None, **_kw):
        raise RuntimeError("Overpass en panne")

    monkeypatch.setattr(views.service, "plan", boom)
    key = page_key(client.get("/", CHARGING))
    with_charging[0]()
    assert client.get(f"/status/{key}/").json()["state"] == "failed"
    r = client.get("/", CHARGING)
    html = r.content.decode()
    assert "Recherche des bornes impossible" in html and "Overpass en panne" in html
    assert "Comparaison des 3 estimations" in html and "charging" not in page_data(r)


def test_charging_warning_from_the_plan_is_displayed(client, with_charging, monkeypatch):
    monkeypatch.setattr(
        views.service, "plan",
        lambda report, detour, tariffs=None, **_kw: charging_dom.ChargingPlan([], 0, 0, detour, "optionnelle", ["Recherche des bornes impossible : HTTP 429"]),
    )
    client.get("/", CHARGING)
    with_charging[0]()
    html = client.get("/", CHARGING).content.decode()
    assert "HTTP 429" in html and "0 borne" in html


def test_refinement_and_charging_run_in_one_job(client, two_phase, monkeypatch):
    seen = {}

    def fake_plan(report, detour, tariffs=None, **_kw):
        seen["precision"] = report.precision  # the search uses the refined result
        return charging_dom.ChargingPlan([], 0, 0, detour, "optionnelle")

    monkeypatch.setattr(views.service, "plan", fake_plan)
    first = client.get("/", {**TWO_PHASE, "max_detour_km": "3"})
    assert "Première estimation" in first.content.decode() and "recherche des bornes" in first.content.decode()
    assert len(two_phase) == 1
    two_phase[0]()
    assert seen["precision"] == "fine"
    r = client.get("/", {**TWO_PHASE, "max_detour_km": "3"})
    assert "Résultat affiné" in r.content.decode() and r.context["refine"]["state"] == "done"


def test_max_detour_field_is_bounded(client):
    for bad in ("-1", "51", "abc"):
        r = client.get("/", {**CHARGING, "max_detour_km": bad})
        assert r.context["form"].errors.get("max_detour_km")
