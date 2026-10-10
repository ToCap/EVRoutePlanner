"""Classification ville / route / autoroute d'après les tags OpenStreetMap."""
from __future__ import annotations

import math
import os
import re
import time
from typing import Protocol

import requests

from .config import ConsumptionConfig
from .errors import RoadClassificationError
from .models import Point

USER_AGENT = os.environ.get("EV_TRIP_USER_AGENT", "ev-trip-simulator/0.1")
# Overpass refuse (HTTP 406) les requêtes sans User-Agent explicite ; en cas d'échec on bascule
# sur les miroirs officiels. EV_TRIP_OVERPASS_URL permet d'imposer un serveur (prioritaire).
OVERPASS_URLS = [
    *([os.environ["EV_TRIP_OVERPASS_URL"]] if os.environ.get("EV_TRIP_OVERPASS_URL") else []),
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
]
OVERPASS_URL = OVERPASS_URLS[0]

# Du plus important au moins important (sert à départager des voies à égale distance).
_PRIORITY = [
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
    "residential", "living_street", "service", "road",
]
DRIVABLE = {name: rank for rank, name in enumerate(_PRIORITY)}


class RoadClassifier(Protocol):
    def classify(self, points: list[Point]) -> list[str | None]: ...


def parse_maxspeed(value: str | None) -> float | None:
    """Convertit un tag maxspeed OSM en km/h (None si inexploitable)."""
    if not value:
        return None
    v = value.strip().lower()
    if "urban" in v:
        return 50.0
    if "rural" in v:
        return 80.0
    if "motorway" in v:
        return 130.0
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(mph)?", v)
    if not m:
        return None
    speed = float(m.group(1))
    return speed * 1.609344 if m.group(2) else speed


def classify_highway(tags: dict[str, str]) -> str | None:
    """Type de route (ville/route/autoroute) d'une voie OSM, None si non carrossable."""
    hw = tags.get("highway")
    if hw not in DRIVABLE:
        return None
    maxspeed = parse_maxspeed(tags.get("maxspeed"))

    if hw == "motorway":
        return "autoroute"
    if hw == "trunk":
        if maxspeed is not None and maxspeed <= 50:
            return "ville"
        return "autoroute" if maxspeed is not None and maxspeed >= 110 else "route"
    if hw.endswith("_link"):
        return "route"
    if maxspeed is not None:
        if maxspeed <= 50:
            return "ville"
        return "autoroute" if maxspeed >= 110 else "route"
    if hw in ("residential", "living_street", "service", "road"):
        return "ville"
    return "route"


def classify_by_speed(avg_speed_kmh: float | None, cfg: ConsumptionConfig) -> str:
    """Repli grossier d'après la vitesse moyenne OSRM (route par défaut si inconnue)."""
    if avg_speed_kmh is None:
        return "route"
    if avg_speed_kmh < cfg.fallback_urban_max_kmh:
        return "ville"
    if avg_speed_kmh >= cfg.fallback_motorway_min_kmh:
        return "autoroute"
    return "route"


def _dist_point_polyline_m(p: Point, geom: list[dict]) -> float:
    kx = 111_320.0 * math.cos(math.radians(p.lat))
    ky = 110_540.0
    xy = [((g["lon"] - p.lon) * kx, (g["lat"] - p.lat) * ky) for g in geom]
    if len(xy) == 1:
        return math.hypot(*xy[0])
    best = math.inf
    for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
        dx, dy = x2 - x1, y2 - y1
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / seg2))
        best = min(best, math.hypot(x1 + t * dx, y1 + t * dy))
    return best


def best_way_type(p: Point, ways: list[dict], radius_m: float) -> str | None:
    """Type de route de la voie la plus proche du point (à ~5 m près, la plus importante gagne)."""
    candidates = []
    for way in ways:
        tags = way.get("tags", {})
        if tags.get("highway") not in DRIVABLE or not way.get("geometry"):
            continue
        d = _dist_point_polyline_m(p, way["geometry"])
        if d <= radius_m:
            candidates.append((round(d / 5.0), DRIVABLE[tags["highway"]], d, tags))
    if not candidates:
        return None
    return classify_highway(min(candidates, key=lambda c: c[:3])[3])


class OverpassRoadClassifier:
    """Interroge Overpass par paquets de points consécutifs de l'itinéraire."""

    def __init__(
        self,
        session: requests.Session | None = None,
        url: str | list[str] | None = None,
        radius_m: float = 30.0,
        chunk_points: int = 40,
        pause_s: float = 1.0,
        retries: int = 3,
        timeout: float = 120.0,
    ) -> None:
        self.http = session or requests
        self.urls = [url] if isinstance(url, str) else list(url or OVERPASS_URLS)
        self.headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        self.radius_m = radius_m
        self.chunk_points = chunk_points
        self.pause_s = pause_s
        self.retries = retries
        self.timeout = timeout

    def classify(self, points: list[Point]) -> list[str | None]:
        out: list[str | None] = []
        for start in range(0, len(points), self.chunk_points):
            chunk = points[start : start + self.chunk_points]
            ways = self._fetch_ways(chunk)
            out.extend(best_way_type(p, ways, self.radius_m) for p in chunk)
            if start + self.chunk_points < len(points) and self.pause_s:
                time.sleep(self.pause_s)
        return out

    def _query(self, chunk: list[Point]) -> str:
        coords = ",".join(f"{p.lat:.6f},{p.lon:.6f}" for p in chunk)
        return f'[out:json][timeout:90];way(around:{self.radius_m:g},{coords})["highway"];out tags geom;'

    def _fetch_ways(self, chunk: list[Point]) -> list[dict]:
        query = self._query(chunk)
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                resp = self.http.post(
                    self.urls[attempt % len(self.urls)],  # un serveur différent à chaque tentative
                    data={"data": query},
                    headers=self.headers,
                    timeout=self.timeout,
                )
                if resp.status_code in (429, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                return resp.json().get("elements", [])
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < self.retries - 1 and self.pause_s:
                    time.sleep(self.pause_s * (attempt + 1) * 2)
        raise RoadClassificationError(f"Overpass indisponible : {last_error}")
