"""Route computation via OSRM."""
from __future__ import annotations

import os

import requests

from ev_trip.domain.errors import RoutingError
from ev_trip.domain.geo import cumulative_km
from ev_trip.domain.models import Point, Route, RouteStep

OSRM_URL = os.environ.get("EV_TRIP_OSRM_URL", "https://router.project-osrm.org")
USER_AGENT = os.environ.get("EV_TRIP_USER_AGENT", "ev-trip-simulator/0.1")


def _parse_steps(best: dict, polyline_km: float) -> list[RouteStep] | None:
    """OSRM steps -> km positions, rescaled to the real polyline length."""
    raw = [
        (float(s.get("distance", 0.0)), s.get("name") or "", s.get("ref") or "")
        for leg in best.get("legs", [])
        for s in leg.get("steps", [])
    ]
    raw = [r for r in raw if r[0] > 0]  # ignore zero-length start/arrival markers
    total_m = sum(d for d, _, _ in raw)
    if not raw or total_m <= 0 or polyline_km <= 0:
        return None
    scale = polyline_km / (total_m / 1000.0)
    steps, pos = [], 0.0
    for dist_m, name, ref in raw:
        end = pos + dist_m / 1000.0 * scale
        steps.append(RouteStep(pos, end, name, ref))
        pos = end
    return steps


def _build_route(item: dict) -> Route:
    """One OSRM route object -> `Route`."""
    coords = [Point(lat=c[1], lon=c[0]) for c in item["geometry"]["coordinates"]]
    durations: list[float] = []
    for leg in item.get("legs", []):
        durations.extend(leg.get("annotation", {}).get("duration", []))
    return Route(
        coordinates=coords,
        distance_km=item["distance"] / 1000.0,
        duration_min=item["duration"] / 60.0,
        durations_s=durations if len(durations) == len(coords) - 1 else None,
        steps=_parse_steps(item, cumulative_km(coords)[-1]) if len(coords) > 1 else None,
    )


def osrm_routes(
    origin: Point,
    destination: Point,
    session: requests.Session | None = None,
    base_url: str = OSRM_URL,
    timeout: float = 60.0,
    alternatives: int = 3,
) -> list[Route]:
    """Fastest route first, then up to `alternatives` OSRM alternatives."""
    url = f"{base_url}/route/v1/driving/{origin.lon},{origin.lat};{destination.lon},{destination.lat}"
    params = {"overview": "full", "geometries": "geojson", "annotations": "duration", "steps": "true"}
    if alternatives > 0:
        params["alternatives"] = str(alternatives)
    http = session or requests
    try:
        resp = http.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise RoutingError(f"Calcul d'itinéraire impossible : {exc}") from exc

    if data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingError(f"Aucun itinéraire trouvé ({data.get('code', 'réponse invalide')}).")
    return [_build_route(item) for item in data["routes"]]


def osrm_route(
    origin: Point,
    destination: Point,
    session: requests.Session | None = None,
    base_url: str = OSRM_URL,
    timeout: float = 60.0,
) -> Route:
    return osrm_routes(origin, destination, session, base_url, timeout, alternatives=0)[0]


def osrm_path(
    points: list[Point],
    session: requests.Session | None = None,
    base_url: str = OSRM_URL,
    timeout: float = 60.0,
) -> tuple[float, float]:
    """Distance (km) and duration (min) of the trip through `points` in order (no geometry)."""
    if len(points) < 2:
        raise RoutingError("Il faut au moins deux points.")
    coords = ";".join(f"{p.lon},{p.lat}" for p in points)
    url = f"{base_url}/route/v1/driving/{coords}"
    http = session or requests
    try:
        resp = http.get(
            url, params={"overview": "false", "steps": "false"},
            headers={"User-Agent": USER_AGENT}, timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise RoutingError(f"Calcul de détour impossible : {exc}") from exc
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingError(f"Aucun itinéraire trouvé ({data.get('code', 'réponse invalide')}).")
    best = data["routes"][0]
    return best["distance"] / 1000.0, best["duration"] / 60.0
