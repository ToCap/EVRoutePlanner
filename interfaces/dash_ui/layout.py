"""Layout: map on 2/3 of the height, routes on the last third, settings in a collapsible side panel."""
from __future__ import annotations

import dash_bootstrap_components as dbc
import dash_leaflet as dl
from dash import dcc, html

from ev_trip.domain.config import CHARGER_LEVELS
from interfaces.dash_ui.components import charge_figure
from interfaces.dash_ui.params import DEFAULTS, charge_min_id

POLL_MS = 3000
FRANCE = {"center": [46.6, 2.5], "zoom": 6}
# fit-to-route padding: keep the route clear of the toggle button (top left) and the pins (top)
BOUNDS_OPTIONS = {"paddingTopLeft": [60, 70], "paddingBottomRight": [30, 30]}


_OVER_MAP = {"position": "absolute", "zIndex": 1000}
SPINNER_HIDDEN = {**_OVER_MAP, "display": "none"}
SPINNER_SHOWN = {**_OVER_MAP, "top": "50%", "left": "50%", "transform": "translate(-50%, -50%)", "display": "block"}
BADGE_HIDDEN = {**_OVER_MAP, "display": "none"}
BADGE_SHOWN = {**_OVER_MAP, "bottom": "28px", "left": "10px", "display": "flex", "alignItems": "center"}


def _number(id_, value, lo, hi, step="any", **kw):
    return dbc.Input(id=id_, type="number", value=value, min=lo, max=hi, step=step, inputMode="decimal", debounce=True, **kw)


def _group(label, control, unit=None):
    items = [dbc.InputGroupText(label, style={"minWidth": "9.5rem"}), control]
    if unit:
        items.append(dbc.InputGroupText(unit))
    return dbc.InputGroup(items, className="mb-2")


def _percent_slider(id_, label, value, lo=0):
    """Percentage slider (from `lo`) with an always-visible value tooltip."""
    return html.Div([
        html.Label(label, htmlFor=id_, className="form-label mb-5"),
        dcc.Slider(id=id_, min=lo, max=100, step=1, value=value, marks={v: str(v) for v in (0, 25, 50, 75, 100) if v >= lo},
                   allow_direct_input=False, tooltip={"placement": "top", "always_visible": True, "template": "{value} %", "style": {"whiteSpace": "nowrap"}}),
    ], className="mb-3")


def _range_slider(id_, label, value):
    """Two-handle percentage slider (values shown in the input boxes at its ends); the handles cannot cross."""
    return html.Div([
        html.Label(label, htmlFor=id_, className="form-label mb-2"),
        dcc.RangeSlider(id=id_, min=0, max=100, step=1, value=value, marks={v: str(v) for v in (0, 25, 50, 75, 100)},
                        allowCross=False, pushable=1),
    ], className="mb-3")


def _section(title, *children):
    """Titled block: a full-width InputGroup header over the group's fields."""
    header = dbc.InputGroup(dbc.InputGroupText(title, className="w-100 fw-semibold rounded-0 rounded-top border-0 border-bottom bg-body-secondary"))
    return html.Div([header, html.Div(list(children), className="p-2 pb-0")], className="border rounded mt-3")


def tab_trip():
    d = DEFAULTS
    return html.Div([
        _section(
            "Itinéraire",
            _group("Départ", dbc.Input(id="origin", type="text", value=d["origin"], placeholder="Paris", debounce=True)),
            _group("Destination", dbc.Input(id="destination", type="text", value=d["destination"], placeholder="Saint-Lô", debounce=True)),
            _group("Température", _number("temp-c", d["temp_c"], -50, 60), "°C"),
        ),
        _section(
            "Charge",
            _percent_slider("soc-slider", "Charge au départ", d["start_soc"]),
            _percent_slider("min-slider", "Seuil d'alerte (minimum)", d["min_soc"]),
        ),
    ])


def tab_stations():
    return html.Div([
        html.H6("Recherche de bornes de recharge", className="mt-3"),
        _group("Détour max", _number("max-detour", DEFAULTS["max_detour_km"], 0, 50), "km"),
        html.P("0 = pas de recherche. Les bornes sont notées /100 (détour, besoin, durée de charge, prix).", className="small text-muted"),
        html.H6("Pondérations de la note", className="mt-3"),
        html.P("Bientôt : réglage des poids et des tarifs par opérateur depuis l'interface (aujourd'hui : fichiers JSON).", className="small text-muted"),
    ])


def charger_options():
    """Dropdown options: level label + outlet description (the value is the level id)."""
    return [{"label": html.Span([level.label, html.Small(f" · {level.description}", className="text-muted")]), "value": level.id,
             "search": f"{level.label} {level.description}"} for level in CHARGER_LEVELS]


def _accordion_item(title, item_id, *children):
    """Collapsible block of an accordion: the group's fields under a clickable title."""
    return dbc.AccordionItem(list(children), title=title, item_id=item_id)


ENERGY_OPEN = ["battery", "recharge"]  # expanded at load; "consumption" is a placeholder, collapsed


def tab_energy():
    return html.Div([
        dbc.Accordion([
            _accordion_item("Batterie", "battery",
                            _group("Capacité nominale", _number("battery-kwh", DEFAULTS["battery_kwh"], 1, 300), "kWh"),
                            _percent_slider("battery-health", "Santé de la batterie", DEFAULTS["battery_health_pct"], lo=50)),
            _accordion_item(
                "Recharge", "recharge",
                html.Label("Niveaux de recharge acceptés", htmlFor="chargers", className="form-label"),
                dcc.Dropdown(id="chargers", options=charger_options(), value=DEFAULTS["chargers"], multi=True, clearable=False,
                             placeholder="Sélectionner un ou plusieurs niveaux", className="mb-2"),
                html.P("Les bornes non cochées sont écartées.", className="small text-muted"),
                _range_slider("charge-window", "Niveau de charge", DEFAULTS["charge_window"]),
                html.P("Durée du niveau bas au niveau haut, par chargeur.", className="small text-muted mb-2"),
                *[_group(html.Span(level.label, title=level.description),
                         _number(charge_min_id(level.id), None, 0, 1440, placeholder="facultatif",
                                 disabled=level.id not in DEFAULTS["chargers"]), "min")
                  for level in CHARGER_LEVELS],
            ),
            _accordion_item(
                "Consommation", "consumption",
                html.P("Bientôt : consommation selon le type de route, la température, le mode éco, le chauffage et la climatisation.", className="small text-muted"),
                dbc.Switch(id="eco-mode", label="Mode éco", value=False, disabled=True),
                _group("Chauffage", dbc.Select(id="heating", options=[{"label": "Arrêt", "value": "off"}], value="off", disabled=True)),
                _group("Climatisation", dbc.Select(id="aircon", options=[{"label": "Arrêt", "value": "off"}], value="off", disabled=True)),
            ),
        ], id="energy-accordion", always_open=True, active_item=ENERGY_OPEN, className="mt-3"),
    ])


SIDEBAR_WIDTH = "min(400px, 90vw)"


def tab_configuration():
    return html.Div([
        html.H6("Calcul du trajet", className="mt-3"),
        dbc.Switch(id="use-overpass", label="Type de route via OpenStreetMap (plus précis, plus lent)", value=DEFAULTS["use_overpass"]),
        html.P("Désactivé : type de route estimé d'après la vitesse moyenne OSRM.", className="small text-muted"),
    ])


def sidebar():
    return dbc.Collapse(
        html.Div([
            html.Div([
                html.H5("Paramètres", className="mb-0"),
                dbc.Button(html.Span("✕"), id="close-sidebar", color="link", size="sm", title="Replier le panneau", className="text-secondary"),
            ], className="d-flex justify-content-between align-items-center mb-2"),
            dbc.Tabs([
                dbc.Tab(tab_trip(), label="Trajet", tab_id="tab-trip", label_class_name="px-2"),
                dbc.Tab(tab_stations(), label="Bornes", tab_id="tab-stations", label_class_name="px-2"),
                dbc.Tab(tab_energy(), label="Énergie", tab_id="tab-energy", label_class_name="px-2"),
                dbc.Tab(tab_configuration(), label="Configuration", tab_id="tab-configuration", label_class_name="px-2"),
            ], id="side-tabs", active_tab="tab-trip"),
            # footer shared by all tabs: pinned at the bottom of the panel
            html.Div([
                html.Div(id="form-error"),
                dbc.Button("Calculer", id="compute-btn", color="primary", className="w-100 mt-2"),
            ], className="mt-auto pt-2 border-top"),
        ], className="p-3 d-flex flex-column", style={"width": SIDEBAR_WIDTH, "minHeight": "100%"}),
        id="sidebar", is_open=True, dimension="width", className="border-end bg-body flex-shrink-0",
        style={"height": "100vh", "overflowY": "auto"},
    )


def map_view(tile_url: str, attribution: str):
    return html.Div([
        dl.Map([
            dl.TileLayer(url=tile_url, attribution=attribution, maxZoom=18),
            dl.LayerGroup(id="route-layer"),
            dl.LayerGroup(id="markers-layer"),
            dl.ZoomControl(position="topright"),
        ], id="map", style={"height": "100%", "width": "100%"}, boundsOptions=BOUNDS_OPTIONS, zoomControl=False, **FRANCE),
        # panel toggle, top left: visible once the sidebar is collapsed (its close cross only collapses it)
        html.Div(dbc.Spinner(color="success", spinner_style={"width": "4rem", "height": "4rem"}), id="compute-spinner",
                 style=SPINNER_HIDDEN),
        html.Div([dbc.Spinner(color="success", size="sm"), html.Span("Affinage en cours…", className="ms-2 small")],
                 id="refine-badge", className="bg-white border rounded shadow-sm px-2 py-1", style=BADGE_HIDDEN),
        dbc.Button("☰", id="open-sidebar", color="light", size="sm", title="Afficher les paramètres", className="shadow-sm border",
                   style={"position": "absolute", "top": "10px", "left": "10px", "zIndex": 1000}),
    ], style={"position": "relative", "height": "100%"})


def routes_panel():
    return html.Div([
        html.Div(id="status-area"),
        dbc.Row([
            dbc.Col(html.Div(id="scenario-cards"), md=4),
            dbc.Col(dbc.Tabs([
                dbc.Tab(dcc.Graph(id="charge-graph", figure=charge_figure(), style={"height": "calc(34vh - 90px)", "minHeight": "180px"},
                                  config={"displaylogo": False}), label="Charge restante", tab_id="t-charge"),
                dbc.Tab(html.Div(id="detail-table", className="pt-2"), label="Détail par tranche", tab_id="t-detail"),
                dbc.Tab(html.Div(id="stations-table", className="pt-2"), label="Bornes de recharge", tab_id="t-stations"),
            ], active_tab="t-charge"), md=8),
        ]),
    ], className="p-2")


def build_layout(tile_url: str, attribution: str):
    main = html.Div([
        dbc.Row(dbc.Col(map_view(tile_url, attribution), width=12, className="p-0 h-100"),
                className="g-0 m-0", style={"flex": "2 1 0", "minHeight": 0}),
        dbc.Row(dbc.Col(routes_panel(), width=12, className="p-0"),
                className="g-0 m-0 border-top", style={"flex": "1 1 0", "minHeight": 0, "overflowY": "auto"}),
    ], className="d-flex flex-column flex-grow-1", style={"minWidth": 0, "height": "100vh"})
    return html.Div([
        dcc.Location(id="url", refresh=False),
        dcc.Store(id="trip-key"),
        dcc.Store(id="resize-sink"),
        dcc.Interval(id="poll", interval=POLL_MS, disabled=True),
        sidebar(),
        main,
    ], className="d-flex flex-row", style={"height": "100vh", "overflow": "hidden"})
