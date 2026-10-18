"""Dash interface: validation, layout, components, callback handlers (no browser)."""
import json

import pytest
from dash import no_update

from interfaces.dash_ui import callbacks, components, layout
from interfaces.dash_ui.app import create_app
from interfaces.dash_ui.params import DEFAULTS, from_query, request_from_values, to_query
from ev_trip.application.charging import plan_charging
from ev_trip.application.jobs import MemoryStore, TripService
from ev_trip.domain.config import CHARGER_LEVELS
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
                   "max-detour", "battery-kwh", "chargers", "compute-spinner", "refine-badge", "compute-btn", "charge-graph", "trip-key", "poll", "url", "open-sidebar",
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
    assert sum(type(n).__name__ == "Slider" for n in nodes) == 3  # start charge, alert threshold and battery health
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
    svc = TripService(MemoryStore(), runner=jobs.append, run_trip=lambda *a, **k: report, plan=plan or (lambda r, d, tariffs=None, **_kw: None), **kw)
    return svc, jobs, report


def test_compute_from_button_returns_key_and_query():
    svc, jobs, _ = make_service()
    key, err, query, filled, invalid = callbacks.compute(svc, "compute-btn", VALUES, "", None)
    assert key and err == "" and query.startswith("?origin=Paris") and filled is None and invalid == set() and len(jobs) == 1  # max detour 5: station search as a background task
    assert svc.get(key) is not None


def test_compute_reports_errors_without_changing_the_result():
    svc, *_ = make_service()
    key, err, query, _, invalid = callbacks.compute(svc, "compute-btn", {**VALUES, "origin": ""}, "", "old")
    assert key is no_update and query is no_update and err == "" and invalid == {"origin"}  # red field, no alert
    *_, invalid = callbacks.compute(svc, "compute-btn", {**VALUES, "origin": "", "destination": " ", "temp_c": "x"}, "", None)
    assert invalid == {"origin", "destination"}
    svc.run_trip = lambda *a, **k: (_ for _ in ()).throw(RoutingError("OSRM HS"))
    key, err, *_, invalid = callbacks.compute(svc, "compute-btn", VALUES, "", None)
    assert key is no_update and "OSRM HS" in err.children and invalid == set()  # service error: alert, no red field


def test_compute_from_url_prefills_form_and_skips_duplicate():
    svc, *_ = make_service()
    req = request_from_values({**VALUES, "temp_c": 3})
    search = to_query(req)
    for trigger in ("url", None):  # URL change or page load
        key, err, query, filled, _ = callbacks.compute(svc, trigger, {}, search, None)
        assert key == req.key() and query is no_update and filled["temp_c"] == 3 and filled["origin"] == "Paris"
    key2, _, query2, *_ = callbacks.compute(svc, "url", {}, search, req.key())  # URL set by the button: nothing to redo
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
    svc, jobs, report = make_service(plan=lambda r, d, tariffs=None, **_kw: plan_charging(
        r, d, tariffs=tariffs, stations=stations, detour_fn=lambda *a: (1.0, 2.0), sleep=lambda s: None))
    key, *_ = callbacks.compute(svc, "compute-btn", {**VALUES, "max_detour_km": 5}, "", None)
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


def test_chargers_roundtrip_through_form_request_and_url():
    values = {**VALUES, "chargers": ["ac11", "dc100"]}
    req = request_from_values(values)
    assert req.chargers == ("dc100", "ac11")
    assert from_query(to_query(req))["chargers"] == ["dc100", "ac11"]
    assert request_from_values({**VALUES, "chargers": list(DEFAULTS["chargers"])}).chargers == tuple(DEFAULTS["chargers"])
    with pytest.raises(ValueError, match="Niveau de recharge inconnu"):
        request_from_values({**VALUES, "chargers": ["ac99"]})
    empty = request_from_values({**VALUES, "chargers": [], "max_detour_km": 5})
    assert empty.chargers == () and empty.wants_charging  # nothing selected: stations are not filtered
    assert from_query(to_query(empty))["chargers"] == [] and request_from_values(from_query(to_query(empty))) == empty
    assert request_from_values({**VALUES, "chargers": [], "max_detour_km": 0}).chargers == ()  # irrelevant without station search


def test_energy_tab_has_a_multi_dropdown_with_all_levels():
    root = layout.build_layout("https://t/{z}/{x}/{y}.png", "osm")
    drop = next(n for n in walk(root) if getattr(n, "id", None) == "chargers")
    assert drop.multi is True and len(drop.options) == 6 and drop.value == DEFAULTS["chargers"] and drop.clearable is True


def test_timed_charge_roundtrip_through_form_request_and_url():
    values = {**VALUES, "chargers": ["dc100", "ac11"], "charge_window": [20, 90], "charge_min_dc100": 25, "charge_min_ac11": "120"}
    req = request_from_values(values)
    assert (req.charge_from_pct, req.charge_to_pct, req.charge_minutes) == (20, 90, (("dc100", 25.0), ("ac11", 120.0)))
    assert request_from_values(from_query(to_query(req))) == req
    default = request_from_values(VALUES)
    assert (default.charge_from_pct, default.charge_to_pct, default.charge_minutes) == (15, 80, ())
    assert "charge_minutes" not in to_query(default) and default.key() != req.key()


def test_timed_charge_ignores_unselected_levels_and_rejects_bad_values():
    assert request_from_values({**VALUES, "chargers": ["dc100"], "charge_min_ac11": 90}).charge_minutes == ()  # greyed out: ignored
    with pytest.raises(ValueError, match="Durée") as exc:
        request_from_values({**VALUES, "charge_min_dc100": 0})
    assert exc.value.fields == ("charge_min_dc100",)  # red field
    with pytest.raises(ValueError, match="numérique") as exc:
        request_from_values({**VALUES, "charge_min_ac22": "x"})
    assert exc.value.fields == ("charge_min_ac22",)
    for window in ([80, 15], [50, 50], [0, 101]):
        with pytest.raises(ValueError, match="Niveau de charge"):
            request_from_values({**VALUES, "charge_window": window})


def test_energy_tab_has_the_charge_window_and_one_duration_input_per_level():
    nodes = {getattr(n, "id", None): n for n in walk(layout.build_layout("https://t/{z}/{x}/{y}.png", "osm"))}
    slider = nodes["charge-window"]
    assert type(slider).__name__ == "RangeSlider" and slider.value == [15, 80]
    for level in CHARGER_LEVELS:
        assert nodes[f"charge-min-{level.id}"].disabled is False  # all levels are selected by default


def test_duration_inputs_are_greyed_out_for_unselected_levels():
    flags = dict(zip((level.id for level in CHARGER_LEVELS), callbacks.charge_inputs_disabled(["dc100", "ac11"])))
    assert flags == {"dc100": False, "ac22": True, "ac11": False, "ac7_4": True, "ac3_7": True, "ac2_3": True}
    assert all(callbacks.charge_inputs_disabled(None)) and not any(callbacks.charge_inputs_disabled(DEFAULTS["chargers"]))
    assert any("charge-min-ac11.disabled" in key for key in create_app().callback_map)  # wired to the dropdown


def test_service_passes_the_timed_charge_to_the_plan():
    seen = {}
    svc, jobs, _ = make_service(plan=lambda r, d, tariffs=None, **kw: seen.update(kw))
    svc.start(request_from_values({**VALUES, "charge_min_dc100": 30}))
    jobs[0]()
    assert seen["charge_window"] == (15, 80) and seen["charge_minutes"] == {"dc100": 30.0}
    seen.clear()
    svc2, jobs2, _ = make_service(plan=lambda r, d, tariffs=None, **kw: seen.update(kw))
    svc2.start(request_from_values(VALUES))
    jobs2[0]()
    assert "charge_minutes" not in seen  # no timing: the plan keeps its usual call


def test_battery_health_scales_the_real_capacity_and_roundtrips_through_the_url():
    req = request_from_values({**VALUES, "battery_health_pct": 80})
    assert req.battery_health_pct == 80 and req.vehicle().battery_kwh == pytest.approx(52 * 0.8)
    assert req.vehicle().start_soc_pct == 90 and req.vehicle().min_soc_pct == 10  # levels apply to the real capacity, not scaled
    assert request_from_values(from_query(to_query(req))) == req
    default = request_from_values(VALUES)
    assert default.battery_health_pct == 100 and default.vehicle().battery_kwh == 52 and default.key() != req.key()
    for bad in (0, 101, "x", None):
        with pytest.raises(ValueError, match="Santé"):
            request_from_values({**VALUES, "battery_health_pct": bad})


def test_battery_item_has_a_health_slider_defaulting_to_100():
    nodes = {getattr(n, "id", None): n for n in walk(layout.build_layout("https://t/{z}/{x}/{y}.png", "osm"))}
    slider = nodes["battery-health"]
    assert getattr(slider, "tooltip", None) is None and getattr(slider, "allow_direct_input", None) is not False  # value shown in the input box, not a tooltip
    assert type(slider).__name__ == "Slider" and slider.value == 100 and slider.min == 50 and slider.max == 100


def test_recharge_item_order_is_charge_window_then_charger_type_then_durations():
    ids = [n.id for n in walk(layout.build_layout("https://t/{z}/{x}/{y}.png", "osm")) if getattr(n, "id", None)]
    assert ids.index("charge-window") < ids.index("chargers") < ids.index("charge-min-dc100")


def test_trip_tab_is_an_accordion_with_itinerary_and_charge_items():
    acc = next(n for n in walk(layout.build_layout("https://t/{z}/{x}/{y}.png", "osm")) if getattr(n, "id", None) == "trip-accordion")
    assert [item.title for item in acc.children] == ["Itinéraire", "Charge"] and acc.always_open is True
    assert set(acc.active_item) == set(layout.TRIP_OPEN)
