"""Command-line interface."""
from __future__ import annotations

import argparse
import sys

import requests

from ev_trip.domain.config import ConsumptionConfig
from ev_trip.domain.errors import EVTripError
from ev_trip.domain.models import VehicleParams
from ev_trip.application.charging import plan_charging
from ev_trip.application.trip import run_trip
from ev_trip.providers.files import load_config, load_tariffs
from ev_trip.report import format_charging, format_report, write_csv


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ev_trip",
        description="Simule la charge restante d'une voiture électrique sur un trajet.",
    )
    p.add_argument("origin", help='Départ, ex. "Paris" (ou "48.85,2.35")')
    p.add_argument("destination", help='Arrivée, ex. "Saint-Lô"')
    p.add_argument("--temp", type=float, required=True, help="Température extérieure uniforme (°C)")
    p.add_argument("--battery", type=float, required=True, help="Capacité de la batterie (kWh)")
    p.add_argument("--soc", type=float, required=True, help="Charge au départ (%%)")
    p.add_argument("--min-soc", type=float, default=10.0, help="Seuil de charge minimum (%%), défaut 10")
    p.add_argument("--config", help="Fichier JSON de configuration (consommations, paliers de température)")
    p.add_argument("--max-slice-km", type=float, help="Longueur maximale d'une tranche (défaut 10)")
    p.add_argument("--csv", help="Écrit le détail des tranches dans ce fichier CSV")
    p.add_argument("--no-refine", action="store_true",
                   help="Première estimation seulement (1 point Overpass par route), sans affinage")
    p.add_argument("--no-overpass", action="store_true",
                   help="N'utilise pas OSM pour le type de route (estimation d'après la vitesse)")
    p.add_argument("--max-detour", type=float, metavar="KM",
                   help="Cherche les bornes de recharge à moins de KM de détour et les note")
    p.add_argument("--tariffs", help="JSON de tarifs par opérateur (€/kWh), voir tariffs.json")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config) if args.config else ConsumptionConfig()
        if args.max_slice_km is not None:
            cfg.max_slice_km = args.max_slice_km
        vehicle = VehicleParams(args.battery, args.soc, args.min_soc)
        if args.max_detour is not None and args.max_detour <= 0:
            raise ValueError("--max-detour doit être > 0.")
        tariffs = load_tariffs(args.tariffs) if args.tariffs else None
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    try:
        report = run_trip(
            args.origin, args.destination, args.temp, vehicle, cfg,
            use_overpass=not args.no_overpass, refine=not args.no_refine,
        )
    except (EVTripError, requests.RequestException) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1

    print(format_report(report))
    if args.max_detour is not None:
        print(format_charging(plan_charging(report, args.max_detour, tariffs=tariffs)))
    if args.csv:
        write_csv(report, args.csv)
        print(f"\nDétail écrit dans {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
