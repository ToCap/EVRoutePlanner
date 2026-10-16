"""Geometry: distances and route resampling."""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

from ev_trip.domain.models import Point, Route

EARTH_RADIUS_KM = 6371.0088
MIN_TAIL_KM = 0.01  # a last section shorter than 10 m is merged with the previous one


def haversine_km(a: Point, b: Point) -> float:
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dphi = p2 - p1
    dlmb = math.radians(b.lon - a.lon)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def cumulative_km(points: list[Point]) -> list[float]:
    cum = [0.0]
    for a, b in zip(points, points[1:]):
        cum.append(cum[-1] + haversine_km(a, b))
    return cum


@dataclass(frozen=True)
class Sample:
    """Small route section, with its midpoint."""

    start_km: float
    end_km: float
    mid: Point
    avg_speed_kmh: float | None


def _locate(cum: list[float], d: float) -> tuple[int, float]:
    """Index of the segment containing distance d, and fraction covered within it."""
    i = bisect.bisect_right(cum, d) - 1
    i = max(0, min(i, len(cum) - 2))
    seg = cum[i + 1] - cum[i]
    frac = 0.0 if seg <= 0 else min(max((d - cum[i]) / seg, 0.0), 1.0)
    return i, frac


def _point_at(points: list[Point], cum: list[float], d: float) -> Point:
    i, f = _locate(cum, d)
    a, b = points[i], points[i + 1]
    return Point(a.lat + f * (b.lat - a.lat), a.lon + f * (b.lon - a.lon))


def point_at_km(route: Route, km: float, cum: list[float] | None = None) -> Point:
    """Route point located `km` kilometers from the start."""
    cum = cum or cumulative_km(route.coordinates)
    return _point_at(route.coordinates, cum, km)


def polyline_between(
    route: Route, start_km: float, end_km: float, cum: list[float] | None = None
) -> list[Point]:
    """Route portion between two km abscissas (to draw a slice)."""
    pts = route.coordinates
    cum = cum or cumulative_km(pts)
    first = bisect.bisect_right(cum, start_km)
    last = bisect.bisect_left(cum, end_km)
    return [_point_at(pts, cum, start_km), *pts[first:last], _point_at(pts, cum, end_km)]


def resample(route: Route, step_km: float = 0.5) -> list[Sample]:
    """Splits the route into `step_km` sections (the last may be shorter)."""
    if step_km <= 0:
        raise ValueError("Le pas d'échantillonnage doit être > 0.")
    pts = route.coordinates
    if len(pts) < 2:
        raise ValueError("L'itinéraire doit contenir au moins 2 points.")
    cum = cumulative_km(pts)
    total = cum[-1]
    if total <= 0:
        raise ValueError("L'itinéraire a une longueur nulle.")

    cum_t: list[float] | None = None
    if route.durations_s is not None and len(route.durations_s) == len(pts) - 1:
        cum_t = [0.0]
        for dt in route.durations_s:
            cum_t.append(cum_t[-1] + dt)

    def time_at(d: float) -> float:
        i, f = _locate(cum, d)
        return cum_t[i] + f * (cum_t[i + 1] - cum_t[i])  # type: ignore[index]

    samples: list[Sample] = []
    # Tolerance: a rounding residue (< 1e-6 step) must not create a ghost section of a few mm.
    n = max(1, math.ceil(total / step_km - 1e-6))
    if n > 1 and total - (n - 1) * step_km < MIN_TAIL_KM:  # remainder < 10 m: absorbed by the previous section
        n -= 1
    for k in range(n):
        s = k * step_km
        e = total if k == n - 1 else (k + 1) * step_km  # the last section goes exactly to the arrival
        speed = None
        if cum_t is not None:
            dt = time_at(e) - time_at(s)
            if dt > 0:
                speed = (e - s) / (dt / 3600.0)
        samples.append(Sample(s, e, _point_at(pts, cum, (s + e) / 2), speed))
    return samples


def nearest_on_route(
    route: Route, point: Point, cum: list[float] | None = None
) -> tuple[float, float]:
    """Nearest route vertex to `point`: (km from start, distance km); equirectangular, coarse then local search."""
    pts = route.coordinates
    cum = cum or cumulative_km(pts)
    kx = 111.320 * math.cos(math.radians(point.lat))
    ky = 110.540

    def dist(i: int) -> float:
        return math.hypot((pts[i].lon - point.lon) * kx, (pts[i].lat - point.lat) * ky)

    n = len(pts)
    stride = max(1, n // 200)
    coarse = min(range(0, n, stride), key=dist)
    lo, hi = max(0, coarse - stride), min(n - 1, coarse + stride)
    best = min(range(lo, hi + 1), key=dist)
    return cum[best], dist(best)
