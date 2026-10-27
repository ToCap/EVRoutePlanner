"""Prepares report data for display (map, chart, tables, stations), shared by all interfaces."""
from __future__ import annotations

from collections import defaultdict

from ev_trip.domain.config import ROAD_TYPES, SCENARIOS
from ev_trip.domain.geo import cumulative_km, point_at_km, polyline_between
from ev_trip.report import LABELS, results_by_index
from ev_trip.domain.trip_report import TripReport

ROAD_COLORS = {"ville": "#e9a23b", "route": "#2f7fd1", "autoroute": "#8e44ad"}
SCENARIO_COLORS = {"optimiste": "#2e9e5b", "moyenne": "#e08a1e", "pessimiste": "#c0392b"}


def _pts(line) -> list[list[float]]:
    return [[round(p.lat, 5), round(p.lon, 5)] for p in line]


def build_view_data(report: TripReport) -> dict:
    cum = cumulative_km(report.route.coordinates)
    ref = report.simulation
    by_index = {name: results_by_index(sim) for name, sim in report.simulations.items()}

    # Full trip, colored by road type.
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

    # Table: one row per planned slice, one cell per scenario (None if not reached).
    names = [n for n in SCENARIOS if n in report.simulations]  # a report may hold a single scenario
    rows = []
    for sl in report.slices:
        cells = []
        for name in names:
            r = by_index[name].get(sl.index)
            if r is None:
                cells.append(None)
                continue
            cells.append(
                {
                    "cons": round(r.consumption_kwh_per_100km, 1),
                    "kwh": round(r.energy_kwh, 2),
                    "soc": round(r.soc_pct, 1),
                    "stop": r.slice.end_km < sl.end_km - 1e-6,  # truncated slice: stop here
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
    for name in names:
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
        "arrival_soc": ref.vehicle.arrival_soc_pct,
        "trip_km": round(ref.trip_km, 2),
        "start": [round(start.lat, 5), round(start.lon, 5)],
        "end": [round(end.lat, 5), round(end.lon, 5)],
    }


def _score_color(score: float) -> str:
    return "#2e9e5b" if score >= 70 else "#e08a1e" if score >= 40 else "#8a93a0"


def build_charging_data(plan) -> dict:
    """Suggested stations ready for display (ranked table and map markers)."""

    def r(v, n=1):
        return None if v is None else round(v, n)

    rows = []
    for rank, s in enumerate(plan.suggestions, 1):
        st = s.station
        rows.append(
            {
                "rank": rank,
                "name": st.label,
                "operator": st.operator_label,
                "charge_type": st.charge_type or "?",
                "power_kw": st.power_kw,
                "capacity": st.capacity,
                "km": r(s.route_km),
                "detour_km": r(s.detour_km),
                "detour_min": r(s.detour_min, 0),
                "detour_kwh": r(s.detour_kwh, 2),
                "detour_estimated": s.detour_estimated,
                "soc_arrival": r(s.soc_arrival_pct, 0),
                "soc_after": r(s.soc_after_pct, 0),
                "reachable": s.reachable,
                "reachable_pessimiste": s.reachable_pessimiste,
                "add_kwh": r(s.add_kwh),
                "charge_min": r(s.charge_min, 0),
                "price": r(s.price_eur_kwh, 2),
                "price_source": s.price_source,
                "cost": r(s.cost_eur, 2),
                "score": round(s.score),
                "color": _score_color(s.score),
                "sub": {k: round(v) for k, v in s.sub_scores.items()},
                "notes": s.notes,
                "lat": round(st.lat, 5),
                "lon": round(st.lon, 5),
            }
        )
    return {
        "rows": rows,
        "found": plan.found,
        "evaluated": plan.evaluated,
        "max_detour_km": plan.max_detour_km,
        "need_level": plan.need_level,
        "warnings": plan.warnings,
    }
