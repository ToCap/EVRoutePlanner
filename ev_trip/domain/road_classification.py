"""Road classification (ville / route / autoroute) from OpenStreetMap tags (pure logic, no network)."""
from __future__ import annotations

import math
import re

from ev_trip.domain.config import ConsumptionConfig
from ev_trip.domain.models import Point

# Most to least important (breaks ties between equidistant ways).
_PRIORITY = [
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
    "residential", "living_street", "service", "road",
]
DRIVABLE = {name: rank for rank, name in enumerate(_PRIORITY)}


def parse_maxspeed(value: str | None) -> float | None:
    """Converts an OSM maxspeed tag to km/h (None if unusable)."""
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
    """Road type (ville/route/autoroute) of an OSM way, None if not drivable."""
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
    """Rough fallback from OSRM average speed (route by default if unknown)."""
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
    """Road type of the way nearest to the point (within ~5 m, the most important wins)."""
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
