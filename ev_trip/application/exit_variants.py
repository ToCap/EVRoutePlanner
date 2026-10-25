"""Route variants leaving the motorway at an earlier exit than the fastest route (OSRM alternatives never find them)."""
from __future__ import annotations

import logging
import math
import re
from typing import Callable

from ev_trip.domain.errors import EVTripError
from ev_trip.domain.geo import cumulative_km
from ev_trip.domain.models import Point, Route, RouteStep

log = logging.getLogger(__name__)

MOTORWAY_REF = re.compile(r"^(A|AP|E|M)[\s-]?\d", re.IGNORECASE)  # road ref of a motorway: A 13, E 05, M 1
MAX_GAP_KM = 3.0  # a shorter non-motorway step (interchange) does not end a motorway run
EXIT_MARGIN_KM = 1.0  # exits closer than this to the fast route's own exit are the same exit
MAX_OFF_ROUTE_KM = 0.15  # an exit node farther from the route belongs to the other carriageway or road
MAX_DETOUR_RATIO = 1.3  # variants longer than this x the fast route are dropped
SAMPLE_KM = 2.0  # spacing of the points sent to the junction search
SEARCH_KM = 30.0  # only the last km of the motorway stretch are searched: keeps the Overpass query small

JunctionFn = Callable[[list[Point]], list[Point]]  # polyline -> motorway exit nodes
LegFn = Callable[[Point, Point], Route]  # start, end -> route avoiding motorways


def _is_motorway(step: RouteStep) -> bool:
    return any(MOTORWAY_REF.match(part.strip()) for part in step.ref.split(";"))


def last_motorway_run(route: Route) -> tuple[float, float] | None:
    """(start km, end km) of the last motorway stretch of the route, from its steps."""
    steps = route.steps or []
    last = next((i for i in range(len(steps) - 1, -1, -1) if _is_motorway(steps[i])), None)
    if last is None:
        return None
    first = last
    for i in range(last - 1, -1, -1):
        if _is_motorway(steps[i]):
            first = i
        elif steps[i].end_km - steps[i].start_km >= MAX_GAP_KM:
            break  # a long stretch off the motorway: the run starts after it
    return steps[first].start_km, steps[last].end_km


def _nearest_vertex(coords: list[Point], cum: list[float], p: Point, lo_km: float, hi_km: float) -> tuple[int, float]:
    """(index, distance km) of the route vertex closest to `p` within [lo_km, hi_km]."""
    kx = math.cos(math.radians(p.lat)) * 111.32
    best, best_d = -1, float("inf")
    for i, c in enumerate(coords):
        if cum[i] < lo_km or cum[i] > hi_km:
            continue
        d = math.hypot((c.lon - p.lon) * kx, (c.lat - p.lat) * 111.32)
        if d < best_d:
            best, best_d = i, d
    return best, best_d


def _prefix(route: Route, idx: int, cum: list[float]) -> Route:
    """Start of the route up to vertex `idx`."""
    scale = route.distance_km / cum[-1] if cum[-1] else 1.0
    km = cum[idx]
    if route.durations_s and len(route.durations_s) >= idx:
        minutes = sum(route.durations_s[:idx]) / 60.0
    else:
        minutes = route.duration_min * km / cum[-1] if cum[-1] else 0.0
    steps = [RouteStep(s.start_km, min(s.end_km, km), s.name, s.ref) for s in route.steps or [] if s.start_km < km] or None
    return Route(route.coordinates[: idx + 1], km * scale, minutes,
                 route.durations_s[:idx] if route.durations_s and len(route.durations_s) >= idx else None, steps)


def _join(head: Route, tail: Route) -> Route:
    shift = cumulative_km(head.coordinates)[-1]
    steps = None
    if head.steps is not None and tail.steps is not None:
        steps = [*head.steps, *(RouteStep(s.start_km + shift, s.end_km + shift, s.name, s.ref) for s in tail.steps)]
    durations = [*head.durations_s, *tail.durations_s] if head.durations_s is not None and tail.durations_s is not None else None
    return Route([*head.coordinates, *tail.coordinates[1:]], head.distance_km + tail.distance_km,
                 head.duration_min + tail.duration_min, durations, steps)


def exit_variants(route: Route, destination: Point, k: int, *, junctions_fn: JunctionFn, leg_fn: LegFn) -> list[Route]:
    """Up to `k` routes that leave the motorway at the exits just before the fast route's own exit.

    Each variant follows the fast route up to the exit, then takes `leg_fn` (no motorway) to the destination.
    """
    run = last_motorway_run(route) if k > 0 else None
    if run is None:
        log.info("Variantes de sortie : pas d'autoroute dans les étapes du trajet rapide.")
        return []
    start_km, end_km = run
    start_km = max(start_km, end_km - SEARCH_KM)
    cum = cumulative_km(route.coordinates)
    in_run = [i for i, c in enumerate(cum) if start_km <= c <= end_km]  # step km are rescaled to the polyline: same axis
    if len(in_run) < 2:
        return []
    sampled, last_km = [], -SAMPLE_KM
    for i in in_run:
        if cum[i] - last_km >= SAMPLE_KM:
            sampled.append(route.coordinates[i])
            last_km = cum[i]
    sampled.append(route.coordinates[in_run[-1]])
    nodes = junctions_fn(sampled)
    exits: list[tuple[float, int]] = []
    for node in nodes:
        idx, dist = _nearest_vertex(route.coordinates, cum, node, start_km + EXIT_MARGIN_KM, end_km - EXIT_MARGIN_KM)
        if idx >= 0 and dist <= MAX_OFF_ROUTE_KM and not any(abs(cum[idx] - km) < EXIT_MARGIN_KM for km, _ in exits):
            exits.append((cum[idx], idx))
    chosen = sorted(exits, reverse=True)[:k]
    variants, errors = [], []  # errors: exceptions of failed legs
    for km, idx in chosen:
        try:
            joined = _join(_prefix(route, idx, cum), leg_fn(route.coordinates[idx], destination))
        except EVTripError as exc:  # one failing leg must not hide the other variants
            errors.append(exc)
            continue
        if joined.distance_km <= route.distance_km * MAX_DETOUR_RATIO:
            variants.append(joined)
    log.info("Variantes de sortie : autoroute km %.1f-%.1f, %d nœuds Overpass, sorties retenues %s, %d variante(s)%s",
             run[0], end_km, len(nodes), [round(km, 1) for km, _ in chosen], len(variants), f", erreurs : {errors}" if errors else "")
    if errors and not variants:
        raise errors[0]
    return variants
