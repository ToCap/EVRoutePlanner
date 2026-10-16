"""Result formatting (console, CSV): one column group per scenario."""
from __future__ import annotations

import csv
from typing import TYPE_CHECKING
from collections import defaultdict
from pathlib import Path

from ev_trip.domain.config import ROAD_TYPES, SCENARIOS
from ev_trip.domain.models import SimulationResult, SliceResult
from ev_trip.domain.trip_report import TripReport

if TYPE_CHECKING:
    from ev_trip.domain.charging import ChargingPlan

LABELS = {"optimiste": "Optimiste", "moyenne": "Moyenne", "pessimiste": "Pessimiste"}
_SHORT = {"optimiste": "Opt.", "moyenne": "Moy.", "pessimiste": "Pess."}
_EPS = 1e-6


def results_by_index(sim: SimulationResult) -> dict[int, SliceResult]:
    return {r.slice.index: r for r in sim.results}


def _summary(sim: SimulationResult) -> str:
    if sim.completed:
        return (
            f"arrivée avec {sim.final_soc_pct:.1f} % ({sim.final_remaining_kwh:.2f} kWh), "
            f"énergie {sim.total_kwh:.2f} kWh"
        )
    return (
        f"TRAJET INTERROMPU au km {sim.total_km:.1f} sur {sim.trip_km:.1f} "
        f"(reste {sim.trip_km - sim.total_km:.1f} km), charge à l'arrêt {sim.final_soc_pct:.1f} % "
        f"({sim.final_remaining_kwh:.2f} kWh), énergie {sim.total_kwh:.2f} kWh"
    )


def _grid(report: TripReport) -> tuple[tuple[str, ...], list[tuple]]:
    """Headers and rows (one per planned slice) with 3 columns per scenario."""
    by_index = {name: results_by_index(sim) for name, sim in report.simulations.items()}
    header = ("#", "début km", "fin km", "type") + tuple(
        f"{_SHORT[s]} {col}" for s in SCENARIOS for col in ("kWh/100", "kWh", "charge %")
    )
    rows = []
    for sl in report.slices:
        row: list = [sl.index, round(sl.start_km, 2), round(sl.end_km, 2), sl.road_type]
        for name in SCENARIOS:
            r = by_index[name].get(sl.index)
            if r is None:  # slice never reached in this scenario
                row += ["-", "-", "-"]
            else:
                row += [round(r.consumption_kwh_per_100km, 2), round(r.energy_kwh, 3), round(r.soc_pct, 1)]
        rows.append(tuple(row))
    return header, rows


def format_report(report: TripReport) -> str:
    ref = report.simulation
    v = ref.vehicle
    cfg = report.config
    ranges = " | ".join(
        f"{t} {cfg.consumption_range_kwh_per_100km[t][0]:g}-{cfg.consumption_range_kwh_per_100km[t][1]:g}"
        for t in ROAD_TYPES
    )
    lines = [
        f"Trajet : {report.origin} -> {report.destination}",
        f"Distance : {ref.trip_km:.1f} km (OSRM : {report.route.distance_km:.1f} km, "
        f"{report.route.duration_min / 60:.1f} h) | Température : {ref.temp_c:g} °C",
        f"Batterie : {v.battery_kwh:g} kWh | départ {v.start_soc_pct:g} % | seuil {v.min_soc_pct:g} %",
        f"Consommations à 20 °C (kWh/100 km, basse-haute) : {ranges}",
        "",
    ]

    header, rows = _grid(report)
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)]
    fmt = "  ".join(f"{{:>{w}}}" for w in widths)
    lines.append(fmt.format(*header))
    lines.extend(fmt.format(*row) for row in rows)

    km_by_type: dict[str, float] = defaultdict(float)
    for sl in report.slices:
        km_by_type[sl.road_type] += sl.length_km
    lines += ["", "Répartition du trajet : " + " | ".join(f"{t} {km_by_type[t]:.1f} km" for t in ROAD_TYPES if km_by_type[t])]

    lines += ["", "Synthèse :"]
    for name in SCENARIOS:
        lines.append(f"  {LABELS[name]:<10} : {_summary(report.simulations[name])}")
    for name in SCENARIOS:
        alert = report.simulations[name].alert
        if alert:
            lines.append(f"ALERTE ({LABELS[name].lower()}) : {alert.message}")
    if report.precision == "coarse":
        lines.append("Précision : première estimation (affinage non effectué).")
    for w in report.warnings:
        lines.append(f"Attention : {w}")
    return "\n".join(lines)


def write_csv(report: TripReport, path: str | Path) -> None:
    """Excel-ready CSV for French locale (";" separator, UTF-8 with BOM)."""
    header, rows = _grid(report)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(header)
        writer.writerows(rows)

        # Summary rows: value placed in each scenario's "charge %" column.
        def summary_row(label: str, values: list) -> tuple:
            out: list = ["", "", "", label]
            for value in values:
                out += ["", "", value]
            return tuple(out)

        sims = [report.simulations[s] for s in SCENARIOS]
        writer.writerow(())
        writer.writerow(summary_row("Km parcourus", [round(s.total_km, 1) for s in sims]))
        writer.writerow(summary_row("Énergie (kWh)", [round(s.total_kwh, 2) for s in sims]))
        writer.writerow(summary_row("Charge finale %", [round(s.final_soc_pct, 1) for s in sims]))
        writer.writerow(summary_row("Statut", ["arrivée" if s.completed else "interrompu" for s in sims]))


_PRICE_SRC = {"osm": "OSM", "gratuit": "gratuit", "tarif": "tarif", "inconnu": "?"}


def format_charging(plan: "ChargingPlan") -> str:
    """Console table of suggested stations (ranked by score)."""
    lines = [
        "",
        f"Bornes de recharge (détour max {plan.max_detour_km:g} km) : "
        f"{plan.found} trouvée(s), {len(plan.suggestions)} retenue(s). Recharge {plan.need_level}.",
    ]
    if plan.suggestions:
        header = ("#", "note", "borne", "type", "kW", "km", "détour km", "+kWh", "arrivée %", "recharge min", "€/kWh", "notes")
        rows = []
        for i, s in enumerate(plan.suggestions, 1):
            st = s.station
            price = "-" if s.price_eur_kwh is None else f"{s.price_eur_kwh:.2f} ({_PRICE_SRC[s.price_source]})"
            rows.append((
                i, f"{s.score:.0f}", st.label[:28], st.charge_type or "?",
                "-" if st.power_kw is None else f"{st.power_kw:g}", f"{s.route_km:.1f}",
                f"{s.detour_km:.1f}" + ("~" if s.detour_estimated else ""),
                f"{s.detour_kwh:.2f}",
                "-" if s.soc_arrival_pct is None else f"{s.soc_arrival_pct:.0f}",
                "-" if s.charge_min is None else f"{s.charge_min:.0f}", price, "; ".join(s.notes),
            ))
        widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)]
        fmt = "  ".join(f"{{:>{w}}}" for w in widths)
        lines.append(fmt.format(*header))
        lines.extend(fmt.format(*r) for r in rows)
        lines.append("Note /100 = détour, besoin de recharge, durée de charge, prix (pondérations dans config « charging »).")
    for w in plan.warnings:
        lines.append(f"Attention : {w}")
    return "\n".join(lines)
