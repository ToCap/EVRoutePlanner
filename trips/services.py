"""Prépare les données du rapport pour l'affichage (carte, graphe, tableau, comparaison)."""
from __future__ import annotations

from collections import defaultdict

from ev_trip.config import ROAD_TYPES, SCENARIOS
from ev_trip.geo import cumulative_km, point_at_km, polyline_between
from ev_trip.report import LABELS, results_by_index
from ev_trip.trip import TripReport

ROAD_COLORS = {"ville": "#e9a23b", "route": "#2f7fd1", "autoroute": "#8e44ad"}
SCENARIO_COLORS = {"optimiste": "#2e9e5b", "moyenne": "#e08a1e", "pessimiste": "#c0392b"}


def _pts(line) -> list[list[float]]:
    return [[round(p.lat, 5), round(p.lon, 5)] for p in line]


def build_view_data(report: TripReport) -> dict:
    cum = cumulative_km(report.route.coordinates)
    ref = report.simulation
    by_index = {name: results_by_index(sim) for name, sim in report.simulations.items()}

    # Trajet complet, coloré par type de route.
    route_slices = [
        {
            "index": sl.index,
            "type": sl.road_type,
            "color": ROAD_COLORS[sl.road_type],
            "start_km": round(sl.start_km, 2),
            "end_km": round(sl.end_km, 2),
            "points": _pts(polyline_between(report.route, sl.start_km, sl.end_km, cum)),
        }
        for sl in report.slices
    ]

    # Tableau : une ligne par tranche prévue, une cellule par scénario (None si non atteinte).
    rows = []
    for sl in report.slices:
        cells = []
        for name in SCENARIOS:
            r = by_index[name].get(sl.index)
            if r is None:
                cells.append(None)
                continue
            cells.append(
                {
                    "cons": round(r.consumption_kwh_per_100km, 1),
                    "kwh": round(r.energy_kwh, 2),
                    "soc": round(r.soc_pct, 1),
                    "stop": r.slice.end_km < sl.end_km - 1e-6,  # tranche tronquée : arrêt ici
                    "stop_km": round(r.slice.end_km, 1),
                }
            )
        rows.append(
            {
                "index": sl.index,
                "type": sl.road_type,
                "start_km": round(sl.start_km, 1),
                "end_km": round(sl.end_km, 1),
                "cells": cells,
            }
        )

    scenarios = []
    for name in SCENARIOS:
        sim = report.simulations[name]
        alert = None
        if sim.alert:
            p = point_at_km(report.route, sim.alert.km, cum)
            alert = {
                "km": round(sim.alert.km, 1),
                "lat": round(p.lat, 5),
                "lon": round(p.lon, 5),
                "message": sim.alert.message,
            }
        chart = [{"x": 0.0, "y": round(sim.vehicle.start_soc_pct, 2)}]
        chart += [{"x": round(r.slice.end_km, 2), "y": round(r.soc_pct, 2)} for r in sim.results]
        scenarios.append(
            {
                "key": name,
                "label": LABELS[name],
                "color": SCENARIO_COLORS[name],
                "completed": sim.completed,
                "total_km": round(sim.total_km, 1),
                "total_kwh": round(sim.total_kwh, 2),
                "avg_kwh_per_100km": round(100 * sim.total_kwh / sim.total_km, 1) if sim.total_km else None,
                "final_soc": round(sim.final_soc_pct, 1),
                "final_kwh": round(sim.final_remaining_kwh, 2),
                "alert": alert,
                "chart": chart,
            }
        )

    km_by_type: dict[str, float] = defaultdict(float)
    for sl in report.slices:
        km_by_type[sl.road_type] += sl.length_km
    breakdown = [
        {"type": t, "color": ROAD_COLORS[t], "km": round(km_by_type[t], 1)} for t in ROAD_TYPES if km_by_type[t]
    ]

    cfg = report.config
    ranges = [
        {"type": t, "low": cfg.consumption_range_kwh_per_100km[t][0], "high": cfg.consumption_range_kwh_per_100km[t][1]}
        for t in ROAD_TYPES
    ]

    start, end = report.route.coordinates[0], report.route.coordinates[-1]
    return {
        "route_slices": route_slices,
        "rows": rows,
        "scenarios": scenarios,
        "breakdown": breakdown,
        "ranges": ranges,
        "min_soc": ref.vehicle.min_soc_pct,
        "trip_km": round(ref.trip_km, 2),
        "start": [round(start.lat, 5), round(start.lon, 5)],
        "end": [round(end.lat, 5), round(end.lon, 5)],
    }
