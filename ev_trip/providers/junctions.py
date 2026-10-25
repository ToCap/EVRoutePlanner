"""Motorway exits (junctions) near a route, from OpenStreetMap (Overpass)."""
from __future__ import annotations

import os
import time
from typing import Callable

import requests

from ev_trip.domain.errors import RoutingError
from ev_trip.domain.models import Point

OVERPASS_URL = os.environ.get("EV_TRIP_OVERPASS_URL", "")  # preferred server, tried first
MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
USER_AGENT = os.environ.get("EV_TRIP_USER_AGENT", "ev-trip-simulator/0.1")
RETRY_STATUS = (429, 502, 503, 504)  # overloaded server: wait, then try the next mirror
MAX_WAIT_S = 30.0


def overpass_junctions(
    points: list[Point],
    session: requests.Session | None = None,
    urls: tuple[str, ...] | None = None,
    radius_m: int = 300,
    timeout: float = 60.0,
    attempts: int = 4,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Point]:
    """Motorway exit nodes within `radius_m` of the polyline `points`; retries on overloaded servers, one mirror per attempt."""
    if len(points) < 2:
        return []
    servers = urls or tuple(dict.fromkeys(u for u in (OVERPASS_URL, *MIRRORS) if u))
    line = ",".join(f"{p.lat:.5f},{p.lon:.5f}" for p in points)
    query = f'[out:json][timeout:30];node["highway"="motorway_junction"](around:{radius_m},{line});out;'
    http = session or requests
    error = "aucune réponse"
    for n in range(attempts):
        delay = 2.0 * (n + 1)
        try:
            resp = http.post(servers[n % len(servers)], data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=timeout)
            if resp.status_code in RETRY_STATUS:
                error = f"{resp.status_code} (serveur surchargé)"
                try:
                    delay = float(resp.headers.get("Retry-After", delay))
                except (TypeError, ValueError, AttributeError):
                    pass
            elif resp.status_code >= 400:  # a request the server rejects: retrying would not help
                raise RoutingError(f"Recherche des sorties d'autoroute impossible : {resp.status_code}")
            else:
                elements = resp.json().get("elements", [])
                return [Point(lat=e["lat"], lon=e["lon"]) for e in elements if "lat" in e and "lon" in e]
        except (requests.RequestException, ValueError) as exc:
            error = str(exc)
        if n < attempts - 1:
            sleep(min(delay, MAX_WAIT_S))
    raise RoutingError(f"Recherche des sorties d'autoroute impossible : {error}")
