"""Builds display components (map, banners, cards, chart, tables) from `ev_trip.presentation` data; pure functions."""
from __future__ import annotations

import dash_bootstrap_components as dbc
import dash_leaflet as dl
import plotly.graph_objects as go
from dash import html

from ev_trip.application.jobs import DONE, FAILED, REFINING, TripEntry


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
                fillOpacity=1, children=[dl.Tooltip(f"{s['label']} : arrêt au km {a['km']} (réserve atteinte)")],
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


def banners(entry: TripEntry, data: dict) -> list:
    out = []
    stopped = [s for s in data["scenarios"] if not s["completed"]]
    if stopped:  # a single line for all interrupted scenarios
        detail = " · ".join(f"{s['label']} : km {s['alert']['km']}" for s in stopped)
        out.append(dbc.Alert(
            [html.Strong(f"Trajet interrompu avant l'arrivée ({data['trip_km']:.1f} km) : "), detail,
             f" — seuil de réserve atteint ({data['min_soc']:g} %)."], color="danger", className="py-1 mb-2"))
    for w in entry.report.warnings:
        out.append(dbc.Alert(w, color="light", className="py-1 mb-2"))
    report = entry.report
    if entry.state == REFINING:
        if report.refinable:
            text = [html.Strong("Première estimation. "),
                    "Le type des routes hors autoroute est encore approximatif : affinage en arrière-plan en cours"
                    + (", puis recherche des bornes de recharge" if entry.max_detour_km > 0 else "")
                    + ", l'affichage se mettra à jour automatiquement."]
        else:
            text = [html.Strong("Recherche des bornes de recharge en cours "), ": l'affichage se mettra à jour automatiquement."]
        out.append(dbc.Alert(text, color="info", className="py-1 mb-2"))
    elif entry.state == FAILED:
        what = "Affinage impossible" if report.refinable else "Recherche des bornes impossible"
        out.append(dbc.Alert(f"{what} ({entry.error}) : résultat partiel conservé.", color="warning", className="py-1 mb-2"))
    elif entry.refined:
        out.append(dbc.Alert("Résultat affiné (types de route vérifiés tous les km hors autoroute).", color="light", className="py-1 mb-2"))
    return out


def trip_summary(data: dict, duration_min: float) -> html.Div:
    """Distance, duration and road-type breakdown of the trip."""
    return html.Div(
        [html.Strong(f"{data['trip_km']:.1f} km"), html.Span(f" · {duration_min:.0f} min (OSRM)", className="text-muted"),
         html.Div(" · ".join(f"{b['type']} {b['km']:.1f} km" for b in data["breakdown"]), className="small text-muted")],
        className="mb-2")


def scenario_cards(data: dict) -> list:
    """One card per scenario, one column each (stacked on narrow screens)."""
    cols = []
    for s in data["scenarios"]:
        status = (dbc.Badge("arrivée", color="success") if s["completed"]
                  else dbc.Badge(f"interrompu km {s['alert']['km']}", color="danger"))
        cols.append(dbc.Col(dbc.Card(dbc.CardBody([
            html.Div([html.Span("● ", style={"color": s["color"]}), html.Strong(s["label"]), " ", status]),
            html.Div(f"{s['total_km']:.1f} km · {s['total_kwh']:.1f} kWh"
                     + (f" · {s['avg_kwh_per_100km']:.1f} kWh/100" if s["avg_kwh_per_100km"] is not None else ""), className="small"),
            html.Div(f"Charge finale : {s['final_soc']:.1f} % ({s['final_kwh']:.1f} kWh)", className="small"),
        ], className="py-1 px-2"), className="mb-1"), xs=12, md=4))
    return [dbc.Row(cols, className="g-2")]


def charge_figure(data: dict | None = None) -> go.Figure:
    fig = go.Figure()
    if data:
        for s in data["scenarios"]:
            fig.add_scatter(x=[p["x"] for p in s["chart"]], y=[p["y"] for p in s["chart"]], mode="lines+markers",
                            name=s["label"], line={"color": s["color"]}, marker={"size": 4})
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
    return html.Div(dbc.Table([html.Thead([head1, head2]), html.Tbody(body)], size="sm", striped=True, responsive=True, className="text-end mb-0"))


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
            sub += " — " + ", ".join(b["notes"])
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
