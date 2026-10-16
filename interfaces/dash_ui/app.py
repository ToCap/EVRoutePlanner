"""Dash application creation and startup."""
from __future__ import annotations

import logging
import os

import dash
import dash_bootstrap_components as dbc

from interfaces.dash_ui.callbacks import register
from interfaces.dash_ui.layout import build_layout
from ev_trip.application.jobs import MemoryStore, TripService
from ev_trip.domain.charging import TariffTable
from ev_trip.providers.files import load_tariffs

log = logging.getLogger(__name__)
TILE_URL = os.environ.get("EV_TRIP_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
TILE_ATTRIBUTION = os.environ.get("EV_TRIP_TILE_ATTRIBUTION", "&copy; contributeurs OpenStreetMap")
TARIFFS_FILE = os.environ.get("EV_TRIP_TARIFFS", "tariffs.json")


def tariffs() -> TariffTable | None:
    """Per-operator tariffs, reloaded on each search; missing or invalid = no tariffs."""
    if os.path.exists(TARIFFS_FILE):
        try:
            return load_tariffs(TARIFFS_FILE)
        except (OSError, ValueError) as exc:
            log.warning("Tarifs ignorés (%s) : %s", TARIFFS_FILE, exc)
    return None


def create_app(service: TripService | None = None) -> dash.Dash:
    """Dash application; the service keeps state in memory: run a single process (or provide a shared TripStore)."""
    service = service or TripService(MemoryStore(), tariffs=tariffs)
    app = dash.Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP], title="Trajet électrique",
                    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}])
    app.layout = build_layout(TILE_URL, TILE_ATTRIBUTION)
    register(app, service)

    @app.server.after_request
    def referrer_policy(response):  # OSM tiles require a Referer
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    app.service = service  # type: ignore[attr-defined]
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    create_app().run(host=os.environ.get("EV_TRIP_HOST", "127.0.0.1"), port=int(os.environ.get("EV_TRIP_PORT", "8050")),
                     debug=os.environ.get("EV_TRIP_DEBUG") == "1")
