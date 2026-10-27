"""Builds display components (map, banners, cards, chart, tables) from `ev_trip.presentation` data; pure functions."""
from __future__ import annotations

import hashlib

import dash_bootstrap_components as dbc
import dash_leaflet as dl
import plotly.graph_objects as go
from dash import html

from ev_trip.application.jobs import DONE, FAILED, REFINING, TripEntry
from ev_trip.application.trip import route_energy_kwh


def route_layers(data: dict) -> list:
    layers = [
        dl.Polyline(
            positions=s["points"], color=s["color"], weight=5, opacity=0.9,
            children=[dl.Tooltip(f"Tranche {s['index']} · {s['type']} · km {s['start_km']}-{s['end_km']}", sticky=True)],
        )
        for s in data["route_slices"]
    ]
    layers.append(dl.Marker(position=data["start"], children=[dl.Tooltip("Départ")]))
    layers.append(dl.Marker(position=data["end"], children=[dl.Tooltip("Arrivée")]))
    return layers


def station_icon(color: str) -> dict:
    """Diamond with a lightning bolt (distinct from the round stop markers); the color carries the score."""
    html_ = (f'<div style="width:24px;height:24px;background:{color};border:2px solid #fff;border-radius:4px;'
             'box-shadow:0 1px 4px rgba(0,0,0,.5);transform:rotate(45deg);display:flex;align-items:center;justify-content:center">'
             '<span style="transform:rotate(-45deg);color:#fff;font-size:14px;line-height:1;text-shadow:0 0 2px #000">⚡</span></div>')
    return {"html": html_, "className": "station-icon", "iconSize": [24, 24], "iconAnchor": [12, 12]}


def marker_layers(data: dict) -> list:
    layers = []
    for s in data["scenarios"]:  # one stop marker per scenario if the reserve is reached
        if s["alert"]:
            a = s["alert"]
            layers.append(dl.CircleMarker(
                center=[a["lat"], a["lon"]], radius=10, color="#ffffff", weight=2, fillColor=s["color"],
                fillOpacity=1, children=[dl.Tooltip((f"{s['label']} : " if len(data["scenarios"]) > 1 else "") + f"arrêt au km {a['km']} (réserve atteinte)")],
            ))
    for b in (data.get("charging") or {}).get("rows", []):  # charging stations: lightning diamond, colored by score
        layers.append(dl.DivMarker(
            position=[b["lat"], b["lon"]], iconOptions=station_icon(b["color"]),
            children=[dl.Tooltip(f"#{b['rank']} {b['name']} · note {b['score']}/100 · détour {b['detour_km']} km")],
        ))
    return layers


def bounds_of(data: dict) -> list[list[float]]:
    pts = [p for s in data["route_slices"] for p in s["points"]] or [data["start"], data["end"]]
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    return [[min(lats), min(lons)], [max(lats), max(lons)]]


def short_of_arrival(s: dict, data: dict) -> bool:
    """True if the scenario ends but under the wanted charge at arrival."""
    target = data.get("arrival_soc")
    return s["completed"] and target is not None and s["final_soc"] < target - 0.05


def banners(entry: TripEntry, data: dict) -> list:
    """Persistent alert, a single line: interrupted scenarios, else scenarios under the wanted arrival charge (empty otherwise)."""
    stopped = [s for s in data["scenarios"] if not s["completed"]]
    if not stopped:
        short = [s for s in data["scenarios"] if short_of_arrival(s, data)]
        if not short:
            return []
        detail = " · ".join((f"{s['label']} : " if len(data["scenarios"]) > 1 else "") + f"{s['final_soc']:.1f} %" for s in short)
        return [dbc.Alert([html.Strong(f"Arrivée sous la charge souhaitée ({data['arrival_soc']:g} %) : "), detail],
                          color="warning", className="py-1 mb-0")]
    # the scenario name is shown only when several scenarios are displayed
    detail = " · ".join((f"{s['label']} : " if len(data["scenarios"]) > 1 else "") + f"km {s['alert']['km']}" for s in stopped)
    return [dbc.Alert([html.Strong(f"Trajet interrompu avant l'arrivée ({data['trip_km']:.1f} km) : "), detail,
                       f" - seuil de réserve atteint ({data['min_soc']:g} %)."], color="danger", className="py-1 mb-0")]


LEVEL_RANK = {"success": 0, "info": 1, "warning": 2, "danger": 3}
NOTIF_KEEP = 3
NOTIF_AUTO_CLOSE_MS = 4000  # success and info toasts close by themselves; warnings and errors stay


def notifications(entry: TripEntry, key: str) -> list[dict]:
    """Transient messages of a trip state as `{id, level, text}` (the id changes with the trip)."""
    items = [("warning", w) for w in entry.report.warnings]
    if entry.state == REFINING:
        if entry.report.refinable:
            text = ("Première estimation : affinage du type des routes en cours"
                    + (", puis recherche des bornes de recharge" if entry.max_detour_km > 0 else "") + ".")
        else:
            text = "Recherche des bornes de recharge en cours."
        items.append(("info", text))
    elif entry.state == FAILED:
        what = "Affinage impossible" if entry.report.refinable else "Recherche des bornes impossible"
        items.append(("danger", f"{what} ({entry.error}) : résultat partiel conservé."))
    elif entry.refined:
        items.append(("success", "Résultat affiné (types de route vérifiés tous les km hors autoroute)."))
    return [{"id": hashlib.sha1(f"{key}|{level}|{text}".encode()).hexdigest()[:10], "level": level, "text": text} for level, text in items]


def merge_notifications(history: list[dict] | None, incoming: list[dict], keep: int = NOTIF_KEEP) -> tuple[list[dict], list[dict]]:
    """Returns (history kept: the `keep` most important, then newest; fresh: incoming not yet seen)."""
    history = history or []
    seen = {n["id"] for n in history}
    fresh = [n for n in incoming if n["id"] not in seen]
    merged = sorted([*fresh, *history], key=lambda n: -LEVEL_RANK[n["level"]])  # stable: newest first within a level
    return merged[:keep], fresh


def notification_toasts(fresh: list[dict]) -> list:
    return [dbc.Toast(n["text"], id=f"toast-{n['id']}", is_open=True, dismissable=True, icon=n["level"],
                      duration=NOTIF_AUTO_CLOSE_MS if LEVEL_RANK[n["level"]] < 2 else None, className="mb-2 w-100") for n in fresh]


def notification_list(history: list[dict]) -> list:
    """Content of the notification popover."""
    if not history:
        return [html.Div("Aucune notification.", className="small text-muted")]
    return [html.Div([dbc.Badge(" ", color=n["level"], className="me-2"), html.Span(n["text"], className="small")], className="mb-1")
            for n in history]


def trip_summary(data: dict, duration_min: float) -> html.Div:
    """Distance, duration and road-type breakdown of the trip."""
    return html.Div(
        [html.Strong(f"{data['trip_km']:.1f} km"), html.Span(f" · {duration_min:.0f} min (OSRM)", className="text-muted"),
         html.Div(" · ".join(f"{b['type']} {b['km']:.1f} km" for b in data["breakdown"]), className="small text-muted")],
        className="mb-2")


def _status_badge(s: dict, data: dict) -> dbc.Badge:
    if not s["completed"]:
        return dbc.Badge(f"interrompu km {s['alert']['km']}", color="danger")
    return dbc.Badge("sous le souhaité", color="warning", text_color="dark") if short_of_arrival(s, data) else dbc.Badge("arrivée", color="success")


def scenario_cards(data: dict) -> list:
    """One card per scenario, one column each (stacked on narrow screens); a single scenario is one plain line."""
    if len(data["scenarios"]) == 1:
        s = data["scenarios"][0]
        status = _status_badge(s, data)
        kwh_100 = f" · {s['avg_kwh_per_100km']:.1f} kWh/100 km" if s["avg_kwh_per_100km"] is not None else ""
        return [html.Div([f"{s['total_kwh']:.1f} kWh{kwh_100} · charge finale {s['final_soc']:.1f} % ({s['final_kwh']:.1f} kWh) ", status],
                         className="small")]
    cols = []
    for s in data["scenarios"]:
        status = _status_badge(s, data)
        cols.append(dbc.Col(dbc.Card(dbc.CardBody([
            html.Div([html.Span("● ", style={"color": s["color"]}), html.Strong(s["label"]), " ", status]),
            html.Div(f"{s['total_km']:.1f} km · {s['total_kwh']:.1f} kWh"
                     + (f" · {s['avg_kwh_per_100km']:.1f} kWh/100" if s["avg_kwh_per_100km"] is not None else ""), className="small"),
            html.Div(f"Charge finale : {s['final_soc']:.1f} % ({s['final_kwh']:.1f} kWh)", className="small"),
        ], className="py-1 px-2"), className="mb-1"), xs=12, md=4))
    return [dbc.Row(cols, className="g-2")]


def alt_route_layer(data: dict) -> dl.Polyline:
    """The other profile's route: grey and dashed, under the selected one."""
    points = [p for s in data["route_slices"] for p in s["points"]]
    return dl.Polyline(positions=points, color="#888", weight=4, opacity=0.8, dashArray="8 8")


def profile_options(entry: TripEntry) -> list[dict]:
    """Profile buttons with the duration and energy of each route."""
    options = []
    for value, label in (("fast", "Rapide"), ("eco", "Consommation")):
        report = entry.report_for(value)
        kwh = route_energy_kwh(report, report.simulation.temp_c)
        options.append({"label": f"{label} · {report.route.duration_min:.0f} min · {kwh:.0f} kWh", "value": value})
    return options


def charge_figure(data: dict | None = None, other: dict | None = None) -> go.Figure:
    fig = go.Figure()
    single = bool(data) and len(data["scenarios"]) == 1  # one scenario: its name is not repeated
    if data and other:  # mean scenario of the other profile, for comparison
        ref = other["scenarios"][len(other["scenarios"]) // 2]
        fig.add_scatter(x=[p["x"] for p in ref["chart"]], y=[p["y"] for p in ref["chart"]], mode="lines",
                        name="Autre profil" if single else "Autre profil (moyenne)", line={"color": "#aaa", "dash": "dot"})
    if data:
        for s in data["scenarios"]:
            fig.add_scatter(x=[p["x"] for p in s["chart"]], y=[p["y"] for p in s["chart"]], mode="lines+markers",
                            name="Charge restante" if single else s["label"], line={"color": s["color"]}, marker={"size": 4})
        fig.add_scatter(x=[0, data["trip_km"]], y=[data["min_soc"]] * 2, mode="lines", name="Seuil minimum",
                        line={"color": "#888", "dash": "dash"})
    fig.update_layout(margin={"l": 40, "r": 10, "t": 10, "b": 35}, template="plotly_white", height=None,
                      xaxis_title="km", yaxis_title="% de batterie", yaxis_range=[0, 100],
                      legend={"orientation": "h", "y": -0.25}, autosize=True)
    return fig


def detail_table(data: dict) -> html.Div:
    head1 = html.Tr([html.Th(colSpan=4)] + [
        html.Th(s["label"], colSpan=3, style={"textAlign": "center", "borderBottom": f"3px solid {s['color']}"})
        for s in data["scenarios"]])
    heads = [head1] if len(data["scenarios"]) > 1 else []  # the scenario row is useless when only one is shown
    head2 = html.Tr([html.Th(h) for h in ("#", "début km", "fin km", "type")]
                    + [html.Th(h) for _ in data["scenarios"] for h in ("kWh/100", "kWh", "charge %")])
    body = []
    for r in data["rows"]:
        cells = [html.Td(r["index"]), html.Td(f"{r['start_km']:.1f}"), html.Td(f"{r['end_km']:.1f}"), html.Td(r["type"])]
        for c in r["cells"]:
            if c is None:
                cells += [html.Td("–", className="text-muted text-center")] * 3
            else:
                stop = {"className": "text-danger fw-bold", "title": f"Arrêt au km {c['stop_km']}"} if c["stop"] else {}
                cells += [html.Td(f"{c['cons']:.1f}"), html.Td(f"{c['kwh']:.2f}"),
                          html.Td(f"{c['soc']:.1f}" + (" ⛔" if c["stop"] else ""), **stop)]
        body.append(html.Tr(cells))
    return html.Div(dbc.Table([html.Thead([*heads, head2]), html.Tbody(body)], size="sm", striped=True, responsive=True, className="text-end mb-0"))


def stations_table(charging: dict | None, requested: bool) -> html.Div:
    if not requested:
        return html.Div("Recherche de bornes désactivée : renseigner un détour max dans l'onglet « Bornes ».", className="text-muted small")
    if charging is None:
        return html.Div("Recherche des bornes en cours…", className="text-muted small")
    intro = html.P(
        f"{charging['found']} borne(s) trouvée(s) dans le couloir, {len(charging['rows'])} retenue(s). "
        f"Recharge {charging['need_level']} pour ce trajet.", className="small mb-1")
    warns = [dbc.Alert(w, color="light", className="py-1 mb-1") for w in charging["warnings"]]
    if not charging["rows"]:
        return html.Div([intro, *warns])
    head = html.Tr([html.Th(h) for h in ("#", "note", "borne", "type", "km", "détour", "à l'arrivée", "recharge", "prix", "détail")])
    body = []
    for b in charging["rows"]:
        price = "inconnu" if b["price"] is None else f"{b['price']:.2f} €/kWh" + (f" · ≈{b['cost']:.2f} €" if b["cost"] is not None else "") + f" ({b['price_source']})"
        sub = f"détour {b['sub']['detour']} · besoin {b['sub']['need']} · charge {b['sub']['charge']} · prix {b['sub']['price']}"
        if b["notes"]:
            sub += " - " + ", ".join(b["notes"])
        body.append(html.Tr([
            html.Td(b["rank"]),
            html.Td(dbc.Badge(str(b["score"]), style={"backgroundColor": b["color"]})),
            html.Td(b["name"] + (f" ({b['operator']})" if b["operator"] and b["operator"] != b["name"] else "")),
            html.Td(b["charge_type"] + (f" · {b['power_kw']:g} kW" if b["power_kw"] else "")),
            html.Td(f"{b['km']:.1f}"),
            html.Td(("~" if b["detour_estimated"] else "") + f"{b['detour_km']:.1f} km · {b['detour_min']:.0f} min · {b['detour_kwh']:.2f} kWh"),
            html.Td("–" if b["soc_arrival"] is None else f"{b['soc_arrival']:.0f} %"),
            html.Td("–" if not b["reachable"] else f"+{b['add_kwh']:.1f} kWh" + (f" · {b['charge_min']:.0f} min" if b["charge_min"] is not None else "")),
            html.Td(price), html.Td(sub, className="small text-muted"),
        ], className="" if b["reachable"] else "text-danger"))
    note = html.P("Note /100 : détour, besoin de recharge, durée de recharge, prix (prix inconnu = neutre 50). "
                  "Vert ≥ 70, orange 40–70, gris < 40. « ~ » : détour estimé à vol d'oiseau.", className="small text-muted mt-1 mb-0")
    return html.Div([intro, *warns, dbc.Table([html.Thead(head), html.Tbody(body)], size="sm", striped=True, className="mb-0"), note])
