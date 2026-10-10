"""Interface en ligne de commande."""
from __future__ import annotations

import argparse
import sys

import requests

from .config import ConsumptionConfig
from .errors import EVTripError
from .models import VehicleParams
from .report import format_report, write_csv
from .trip import run_trip


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
    p.add_argument("--no-overpass", action="store_true",
                   help="N'utilise pas OSM pour le type de route (estimation d'après la vitesse)")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        cfg = ConsumptionConfig.from_json(args.config) if args.config else ConsumptionConfig()
        if args.max_slice_km is not None:
            cfg.max_slice_km = args.max_slice_km
        vehicle = VehicleParams(args.battery, args.soc, args.min_soc)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    try:
        report = run_trip(
            args.origin, args.destination, args.temp, vehicle, cfg, use_overpass=not args.no_overpass
        )
    except (EVTripError, requests.RequestException) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1

    print(format_report(report))
    if args.csv:
        write_csv(report, args.csv)
        print(f"\nDétail écrit dans {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
