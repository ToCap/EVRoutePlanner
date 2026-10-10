"""Calcul d'itinéraire via OSRM."""
from __future__ import annotations

import os

import requests

from .errors import RoutingError
from .models import Point, Route

OSRM_URL = os.environ.get("EV_TRIP_OSRM_URL", "https://router.project-osrm.org")
USER_AGENT = os.environ.get("EV_TRIP_USER_AGENT", "ev-trip-simulator/0.1")


def osrm_route(
    origin: Point,
    destination: Point,
    session: requests.Session | None = None,
    base_url: str = OSRM_URL,
    timeout: float = 60.0,
) -> Route:
    url = f"{base_url}/route/v1/driving/{origin.lon},{origin.lat};{destination.lon},{destination.lat}"
    params = {"overview": "full", "geometries": "geojson", "annotations": "duration", "steps": "false"}
    http = session or requests
    try:
        resp = http.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise RoutingError(f"Calcul d'itinéraire impossible : {exc}") from exc

    if data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingError(f"Aucun itinéraire trouvé ({data.get('code', 'réponse invalide')}).")

    best = data["routes"][0]
    coords = [Point(lat=c[1], lon=c[0]) for c in best["geometry"]["coordinates"]]
    durations: list[float] = []
    for leg in best.get("legs", []):
        durations.extend(leg.get("annotation", {}).get("duration", []))
    return Route(
        coordinates=coords,
        distance_km=best["distance"] / 1000.0,
        duration_min=best["duration"] / 60.0,
        durations_s=durations if len(durations) == len(coords) - 1 else None,
    )
