"""Dash interface: validation, layout, components, callback handlers (no browser)."""
import json

import pytest
from dash import no_update

from interfaces.dash_ui import callbacks, components, layout
from interfaces.dash_ui.app import create_app
from interfaces.dash_ui.params import DEFAULTS, from_query, request_from_values, to_query
from ev_trip.application.charging import plan_charging
from ev_trip.application.jobs import MemoryStore, TripService
from ev_trip.domain.errors import RoutingError
from ev_trip.presentation import build_charging_data, build_view_data
from ev_trip.providers.overpass import OverpassStationSource
from test_charging import FakeClient, element, make_report

VALUES = {**DEFAULTS, "origin": "Paris", "destination": "Saint-Lô"}


# --- parameters ---------------------------------------------------------------------------

def test_request_from_values_ok_and_defaults():
    r = request_from_values({**VALUES, "max_detour_km": None})
    assert (r.origin, r.destination, r.temp_c, r.battery_kwh, r.start_soc, r.min_soc) == ("Paris", "Saint-Lô", 15, 52, 90, 10)
    assert r.max_detour_km == 0 and not r.wants_charging and r.use_overpass is True
    assert request_from_values({**VALUES, "max_detour_km": 5}).wants_charging


@pytest.mark.parametrize("change,fragment", [
    ({"origin": " "}, "départ"), ({"destination": None}, "destination"),
    ({"temp_c": None}, "Température"), ({"temp_c": "abc"}, "numérique"), ({"temp_c": 99}, "comprise entre"),
    ({"battery_kwh": 0}, "Capacité"), ({"start_soc": 101}, "Charge au départ"), ({"min_soc": -1}, "Seuil"),
    ({"max_detour_km": 80}, "Détour"),
])
def test_request_from_values_rejects_bad_input(change, fragment):
    with pytest.raises(ValueError, match=fragment):
        request_from_values({**VALUES, **change})


def test_query_round_trip():
    req = request_from_values({**VALUES, "temp_c": -3.5, "max_detour_km": 4, "use_overpass": False})
    values = from_query(to_query(req))
    assert request_from_values(values) == req
    assert from_query("") is None and from_query(None) is None and from_query("?origin=Paris") is None
    assert from_query("?origin=A&destination=B&temp_c=x")["temp_c"] == "x"


# --- layout -------------------------------------------------------------------------

def walk(node):
    yield node
    kids = getattr(node, "children", None)
    if kids is None:
        return
    for k in kids if isinstance(kids, (list, tuple)) else [kids]:
        if hasattr(k, "to_plotly_json"):
            yield from walk(k)


def test_layout_structure():
    root = layout.build_layout("https://t/{z}/{x}/{y}.png", "osm")
    nodes = list(walk(root))
    ids = [n.id for n in nodes if getattr(n, "id", None)]
    assert len(ids) == len(set(ids))
    for needed in ("map", "sidebar", "side-tabs", "origin", "destination", "soc-slider", "min-slider", "use-overpass",
                   "max-detour", "battery-kwh", "compute-spinner", "refine-badge", "compute-btn", "charge-graph", "trip-key", "poll", "url", "open-sidebar",
                   "close-sidebar", "resize-sink"):
        assert needed in ids, needed
    rows = [n for n in nodes if type(n).__name__ == "Row"]
    assert [r.style["flex"] for r in rows[:2]] == ["2 1 0", "1 1 0"]  # map 2/3, routes 1/3
    side = next(n for n in nodes if getattr(n, "id", None) == "sidebar")
    assert type(side).__name__ == "Collapse" and side.dimension == "width" and side.is_open  # pushes the layout, no overlay
    assert type(root).__name__ == "Div" and root.children[-2] is side  # sidebar next to the main column
    tabs = next(n for n in nodes if getattr(n, "id", None) == "side-tabs")
    assert [t.label for t in tabs.children] == ["Trajet", "Bornes", "Énergie", "Configuration"]
    groups = [n for n in nodes if type(n).__name__ == "InputGroup"]
    assert len(groups) >= 5
    assert sum(type(n).__name__ == "Slider" for n in nodes) == 2  # start charge and alert threshold
    assert sum(getattr(n, "id", None) == "compute-btn" for n in nodes) == 1  # a single shared button
    for disabled_id in ("eco-mode", "heating", "aircon"):  # tab 3: reserved for future use
        assert next(n for n in nodes if getattr(n, "id", None) == disabled_id).disabled is True
    json.dumps(root.to_plotly_json(), default=str)  # serializable


def test_app_serves_layout_and_registers_callbacks():
    app = create_app()
    assert len(app.callback_map) >= 5
    client = app.server.test_client()
    r = client.get("/")
    assert r.status_code == 200 and r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert client.get("/_dash-layout").status_code == 200


# --- handlers ------------------------------------------------------------------------

def make_service(plan=None, **kw):
    jobs = []
    report = make_report(100)
    svc = TripService(MemoryStore(), runner=jobs.append, run_trip=lambda *a, **k: report, plan=plan or (lambda r, d, tariffs=None: None), **kw)
    return svc, jobs, report


def test_compute_from_button_returns_key_and_query():
    svc, jobs, _ = make_service()
    key, err, query, filled = callbacks.compute(svc, "compute-btn", VALUES, "", None)
    assert key and err == "" and query.startswith("?origin=Paris") and filled is None and len(jobs) == 1  # max detour 5: station search as a background task
    assert svc.get(key) is not None


def test_compute_reports_errors_without_changing_the_result():
    svc, *_ = make_service()
    key, err, query, _ = callbacks.compute(svc, "compute-btn", {**VALUES, "origin": ""}, "", "old")
    assert key is no_update and query is no_update and "départ" in err.children
    svc.run_trip = lambda *a, **k: (_ for _ in ()).throw(RoutingError("OSRM HS"))
    key, err, *_ = callbacks.compute(svc, "compute-btn", VALUES, "", None)
    assert key is no_update and "OSRM HS" in err.children


def test_compute_from_url_prefills_form_and_skips_duplicate():
    svc, *_ = make_service()
    req = request_from_values({**VALUES, "temp_c": 3})
    search = to_query(req)
    for trigger in ("url", None):  # URL change or page load
        key, err, query, filled = callbacks.compute(svc, trigger, {}, search, None)
        assert key == req.key() and query is no_update and filled["temp_c"] == 3 and filled["origin"] == "Paris"
    key2, _, query2, _ = callbacks.compute(svc, "url", {}, search, req.key())  # URL set by the button: nothing to redo
    assert key2 is no_update and query2 is no_update
    assert callbacks.compute(svc, None, {}, "", None)[0] is no_update  # no trip in the URL


def test_refresh_without_result_or_with_expired_result():
    svc, *_ = make_service()
    r = callbacks.refresh(svc, None)
    assert r["poll_disabled"] and r["route"] == []
    r = callbacks.refresh(svc, "0" * 40)
    assert r["poll_disabled"] and "expiré" in r["status"][0].children
    assert callbacks.map_bounds(svc, "0" * 40) is no_update


def test_refresh_renders_every_panel_and_keeps_polling_while_busy():
    stations = OverpassStationSource(FakeClient([element(60, id=1, name="Borne A", operator="Ionity",
                                                         **{"socket:type2_combo:output": "150 kW"})]))
    svc, jobs, report = make_service(plan=lambda r, d, tariffs=None: plan_charging(
        r, d, tariffs=tariffs, stations=stations, detour_fn=lambda *a: (1.0, 2.0), sleep=lambda s: None))
    key, _, _, _ = callbacks.compute(svc, "compute-btn", {**VALUES, "max_detour_km": 5}, "", None)
    busy = callbacks.refresh(svc, key)
    assert busy["poll_disabled"] is False and busy["markers"] == []
    assert "en cours" in str(busy["stations"].children) and any("Recherche des bornes" in str(b) for b in busy["status"])
    jobs[0]()
    done = callbacks.refresh(svc, key)
    assert done["poll_disabled"] is True
    assert len(done["route"]) == len(build_view_data(report)["route_slices"]) + 2  # slices + start + arrival
    assert len(done["markers"]) == 1  # one station (the 100 km trip is not interrupted)
    assert "Borne A" in str(done["stations"]) and "Ionity" in str(done["stations"])
    assert len(done["graph"].data) == 4  # 3 scenarios + threshold
    assert "Détail" not in str(done["detail"]) and "kWh/100" in str(done["detail"])
    (south, west), (north, east) = callbacks.map_bounds(svc, key)
    assert south < north and west <= east


def test_interrupted_trip_shows_alert_and_stop_markers():
    svc, jobs, _ = make_service()
    svc.run_trip = lambda *a, **k: make_report(300)
    key, *_ = callbacks.compute(svc, "compute-btn", VALUES, "", None)
    r = callbacks.refresh(svc, key)
    assert sum("interrompu" in str(b) for b in r["status"]) == 1  # a single alert for the 3 scenarios
    assert len(r["markers"]) >= 1 and "interrompu" in str(r["cards"])


def test_charging_panel_states():
    assert "désactivée" in components.stations_table(None, False).children
    plan = build_charging_data(plan_charging(make_report(100), 5, stations=OverpassStationSource(FakeClient([])), sleep=lambda s: None))
    empty = components.stations_table(plan, True)
    assert "0 borne(s)" in str(empty)


def test_station_markers_differ_from_stop_markers_and_keep_score_color():
    data = {"scenarios": [{"alert": {"lat": 1, "lon": 2, "km": 10}, "color": "#111111", "label": "Moyenne"}],
            "charging": {"rows": [{"lat": 3, "lon": 4, "color": "#2ca02c", "rank": 1, "name": "A", "score": 80, "detour_km": 1.2}]}}
    stop, station = components.marker_layers(data)
    assert type(stop).__name__ == "CircleMarker" and type(station).__name__ == "DivMarker"
    assert "#2ca02c" in station.iconOptions["html"] and "⚡" in station.iconOptions["html"]
