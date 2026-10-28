"""Dash callbacks: translation between components and `TripService` (no business logic); `register` wires plain handlers."""
from __future__ import annotations

import logging

import requests
from dash import Input, Output, State, callback_context, no_update
import dash_bootstrap_components as dbc

from interfaces.dash_ui import components as ui
from interfaces.dash_ui.layout import BADGE_HIDDEN, BADGE_SHOWN, NOTIF_BTN_HIDDEN, NOTIF_BTN_SHOWN, SPINNER_HIDDEN, SPINNER_SHOWN
from interfaces.dash_ui.params import FieldError, charge_min_id, charge_min_key, from_query, request_from_values, to_query
from ev_trip.application.jobs import REFINING, TripService
from ev_trip.domain.config import CHARGER_LEVELS
from ev_trip.domain.errors import EVTripError
from ev_trip.presentation import build_charging_data, build_view_data

log = logging.getLogger(__name__)
FIELD_IDS = ("origin", "destination", "temp-c", "battery-kwh", "soc-slider", "min-slider", "max-detour", "use-overpass", "chargers",
             "charge-window", *[charge_min_id(level.id) for level in CHARGER_LEVELS], "battery-health", "eco-extra", "eco-exit", "scenario", "profile", "arrival-on", "arrival-slider")
FIELD_KEYS = ("origin", "destination", "temp_c", "battery_kwh", "start_soc", "min_soc", "max_detour_km", "use_overpass", "chargers",
              "charge_window", *[charge_min_key(level.id) for level in CHARGER_LEVELS], "battery_health_pct", "eco_extra_pct", "eco_exit_variants", "scenario", "profile", "arrival_on", "arrival_soc")
INVALIDABLE = {"origin": "origin", "destination": "destination", "temp_c": "temp-c", "battery_kwh": "battery-kwh", "max_detour_km": "max-detour", "eco_extra_pct": "eco-extra", "eco_exit_variants": "eco-exit",
               **{charge_min_key(level.id): charge_min_id(level.id) for level in CHARGER_LEVELS}}


def charge_inputs_disabled(selected: list[str] | None) -> list[bool]:
    """Disabled flag of each timed-duration input, in `CHARGER_LEVELS` order: greyed out when its level is not selected."""
    return [level.id not in (selected or ()) for level in CHARGER_LEVELS]


def compute(service: TripService, trigger: str | None, values: dict, search: str | None, current_key: str | None):
    """Starts a trip; returns (key, error message, query string, values to fill back, invalid field keys or None)."""
    from_url = trigger != "compute-btn"  # page load or URL change
    if from_url:
        values = from_query(search)
        if values is None:
            return no_update, no_update, no_update, None, None
    try:
        request = request_from_values(values)
        key, _ = service.start(request)
    except FieldError as exc:  # red fields, no alert
        return no_update, "", no_update, None, set(exc.fields)
    except ValueError as exc:
        return no_update, dbc.Alert(str(exc), color="danger", className="py-1 mb-0"), no_update, None, set()
    except (EVTripError, requests.RequestException) as exc:
        return no_update, dbc.Alert(str(exc), color="danger", className="py-1 mb-0"), no_update, None, set()
    query = to_query(request)
    if from_url and key == current_key:
        return no_update, "", no_update, values, set()
    return key, "", (no_update if from_url else query), (values if from_url else None), set()


def refresh(service: TripService, key: str | None):
    """Renders all panels of a trip; returns a dict per output + polling stop."""
    empty = {"route": [], "markers": [], "map_alert": [], "notifs": [], "summary": no_update, "cards": no_update,
             "graph": ui.charge_figure(), "detail": [], "stations": [], "poll_disabled": True, "stage": no_update}
    if not key:
        return empty
    entry = service.get(key)
    if entry is None:
        expired = [dbc.Alert("Résultat expiré : relancer le calcul.", color="warning", className="py-1 mb-0")]
        return {**empty, "map_alert": expired}
    report, charging = entry.report, entry.charging
    data = build_view_data(report)
    if charging is not None:
        data["charging"] = build_charging_data(charging)
    requested = entry.max_detour_km > 0
    route = ui.route_layers(data)
    return {
        "route": route, "markers": ui.marker_layers(data),
        "map_alert": ui.banners(entry, data), "notifs": ui.notifications(entry, key),
        "summary": ui.trip_summary(data, report.route.duration_min), "cards": ui.scenario_cards(data),
        "graph": ui.charge_figure(data), "detail": ui.detail_table(data),
        "stations": ui.stations_table(data.get("charging"), requested),
        "poll_disabled": entry.state != REFINING, "stage": entry.stage or "Calcul en cours…",
    }


def notify(incoming: list[dict] | None, history: list[dict] | None):
    """Merges new notifications into the history; returns (toasts, history, popover content, button style, count)."""
    kept, fresh = ui.merge_notifications(history, incoming or [])
    return (ui.notification_toasts(fresh) if fresh else no_update, kept, ui.notification_list(kept),
            NOTIF_BTN_SHOWN if kept else NOTIF_BTN_HIDDEN, str(len(kept)))


def map_bounds(service: TripService, key: str | None):
    entry = service.get(key) if key else None
    return no_update if entry is None else ui.bounds_of(build_view_data(entry.report))


def register(app, service: TripService) -> None:
    @app.callback(
        Output("sidebar", "is_open"), Input("open-sidebar", "n_clicks"), Input("close-sidebar", "n_clicks"),
        State("sidebar", "is_open"), prevent_initial_call=True)
    def toggle_sidebar(_open, _close, is_open):  # ☰ toggles, ✕ collapses
        return False if callback_context.triggered_id == "close-sidebar" else not is_open

    # the map and the chart follow the new width: Leaflet and Plotly listen to window resize events
    app.clientside_callback(
        """function (open) {
            [50, 200, 400].forEach(ms => setTimeout(() => window.dispatchEvent(new Event('resize')), ms));
            return Date.now();
        }""",
        Output("resize-sink", "data"), Input("sidebar", "is_open"),
    )

    @app.callback([Output(charge_min_id(level.id), "disabled") for level in CHARGER_LEVELS], Input("chargers", "value"))
    def on_chargers(selected):  # a duration can only be entered for a selected level
        return charge_inputs_disabled(selected)

    @app.callback(Output("arrival-slider", "disabled"), Input("arrival-on", "value"))
    def on_arrival(checked):  # the wanted arrival charge is editable only when its box is ticked
        return not checked

    outputs = [Output("trip-key", "data"), Output("form-error", "children"), Output("url", "search"),
               *[Output(i, "value") for i in FIELD_IDS], *[Output(i, "invalid") for i in INVALIDABLE.values()]]
    states = [State("trip-key", "data"), *[State(i, "value") for i in FIELD_IDS]]

    def handle(trigger, search, current_key, field_values):
        values = dict(zip(FIELD_KEYS, field_values))
        key, error, query, filled, invalid = compute(service, trigger, values, search, current_key)
        reported = [filled[k] for k in FIELD_KEYS] if filled else [no_update] * len(FIELD_KEYS)
        flags = [no_update] * len(INVALIDABLE) if invalid is None else [k in invalid for k in INVALIDABLE]
        return key, error, query, *reported, *flags

    # button: busy indicators (spinner, disabled button) only while a click is being processed
    @app.callback(
        *outputs, Input("compute-btn", "n_clicks"), State("url", "search"), *states, prevent_initial_call=True,
        running=[(Output("compute-btn", "disabled"), True, False),
                 (Output("compute-spinner", "style"), SPINNER_SHOWN, SPINNER_HIDDEN)],
    )
    def on_compute(_clicks, search, current_key, *field_values):
        return handle("compute-btn", search, current_key, field_values)

    for field in INVALIDABLE.values():  # editing a field clears its red outline
        app.callback(Output(field, "invalid", allow_duplicate=True), Input(field, "value"), prevent_initial_call=True)(lambda _v: False)

    # page load or URL change (shared link): same outputs, no busy indicators
    @app.callback(
        *[Output(o.component_id, o.component_property, allow_duplicate=True) for o in outputs],
        Input("url", "search"), *states, prevent_initial_call="initial_duplicate",
    )
    def on_url(search, current_key, *field_values):
        return handle("url", search, current_key, field_values)

    @app.callback(
        Output("route-layer", "children"), Output("markers-layer", "children"),
        Output("trip-summary", "children"), Output("scenario-cards", "children"), Output("charge-graph", "figure"), Output("detail-table", "children"),
        Output("stations-table", "children"), Output("poll", "disabled"), Output("refine-badge", "style"),
        Output("map-alert", "children"), Output("notif-new", "data"),
        Output("refine-text", "children"),
        Input("trip-key", "data"), Input("poll", "n_intervals"),
    )
    def on_refresh(key, _n):
        r = refresh(service, key)
        return (r["route"], r["markers"], r["summary"], r["cards"], r["graph"], r["detail"], r["stations"], r["poll_disabled"],
                BADGE_HIDDEN if r["poll_disabled"] else BADGE_SHOWN, r["map_alert"], r["notifs"],
                r["stage"])

    @app.callback(
        Output("toasts", "children"), Output("notif-history", "data"), Output("notif-list", "children"),
        Output("notif-btn", "style"), Output("notif-count", "children"),
        Input("notif-new", "data"), State("notif-history", "data"),
    )
    def on_notifications(incoming, history):
        return notify(incoming, history)

    @app.callback(Output("map", "bounds"), Input("trip-key", "data"), prevent_initial_call=True)
    def on_bounds(key):
        return map_bounds(service, key)
