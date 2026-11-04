"""Charging-station use case: search, real detour (routing), scoring and ranking."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from ev_trip.application.messages import user_message
from ev_trip.domain.charging import (
    ChargingPlan, Station, StationSuggestion, TariffTable, apply_charge_profile, apply_chargers, detour_anchors_km,
    detour_from_via,
    estimate_detour, need_level, score_station, station_compatible,
)
from ev_trip.domain.config import ChargingConfig
from ev_trip.domain.recharge import curve_with_stops, final_soc_with_stops, plan_stops
from ev_trip.domain.errors import EVTripError, RoadClassificationError, RoutingError
from ev_trip.domain.geo import cumulative_km, nearest_on_route, point_at_km
from ev_trip.domain.models import Point, Route
from ev_trip.domain.ports import PathFn, StationSource
from ev_trip.domain.trip_report import TripReport
from dataclasses import replace

log = logging.getLogger(__name__)


def osrm_detour(
    route: Route,
    station: Station,
    route_km: float,
    cfg: ChargingConfig,
    *,
    cum: list[float] | None = None,
    path_fn: PathFn | None = None,
) -> tuple[float, float]:
    """Real detour (km, min): route A -> station -> B minus the direct route A -> B (A, B on the route)."""
    if path_fn is None:
        from ev_trip.providers.osrm import osrm_path as path_fn
    cum = cum or cumulative_km(route.coordinates)
    a_km, b_km = detour_anchors_km(route_km, cum[-1], cfg)
    a, b = point_at_km(route, a_km, cum), point_at_km(route, b_km, cum)
    via_km, via_min = path_fn([a, station.point, b])
    return detour_from_via(route, cum, a_km, b_km, via_km, via_min)


@dataclass
class StationPrefetch:
    """Network part of the station search, which depends on the route geometry only (can run while the route is refined)."""

    found: list[Station] = field(default_factory=list)  # stations in the corridor
    detours: dict[str, tuple[float, float]] = field(default_factory=dict)  # station id -> real detour (km, min)
    error: str | None = None  # short cause when the corridor search failed
    osrm_error: str | None = None  # short cause when the detours could not all be routed


def fetch_stations(
    route: Route,
    max_detour_km: float,
    cfg: ChargingConfig | None = None,
    *,
    stations: StationSource | None = None,
    detour_fn: Callable[..., tuple[float, float]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    table_fn: Callable[..., tuple[list[list[float | None]], list[list[float | None]]]] | None = None,
    path_fn: PathFn | None = None,
) -> StationPrefetch:
    """Corridor stations and the real detour of the `max_candidates` closest ones (estimated as the crow flies).

    Detours come from grouped OSRM `table` requests; if that fails (or `detour_fn` is given) they are routed one by one.
    """
    cfg = cfg or ChargingConfig()
    if stations is None:
        from ev_trip.providers.overpass import OverpassStationSource

        stations = OverpassStationSource(cfg=cfg)
    out = StationPrefetch()
    cum = cumulative_km(route.coordinates)
    try:
        out.found = list(stations.find(route, max_detour_km * cfg.corridor_factor))
    except (RoadClassificationError, EVTripError) as exc:
        log.warning("Recherche des bornes impossible : %s", exc)
        out.error = user_message(exc)
        return out
    near = []
    for st in out.found:
        km, dist = nearest_on_route(route, st.point, cum)
        near.append((estimate_detour(dist, cfg)[0], st, km))
    near.sort(key=lambda t: t[0])
    if detour_fn is None:
        try:
            out.detours = osrm_detours(route, [(st, km) for _, st, km in near[: cfg.max_candidates]], cfg, cum=cum, table_fn=table_fn)
            return out
        except (RoutingError, EVTripError) as exc:
            log.warning("Détours : requête groupée impossible, un par un : %s", exc)
    detour = detour_fn or (lambda r, st, km, c, cum_: osrm_detour(r, st, km, c, cum=cum_, path_fn=path_fn))
    for n, (_, st, km) in enumerate(near[: cfg.max_candidates]):
        if n and cfg.osrm_pause_s:
            sleep(cfg.osrm_pause_s)
        try:
            out.detours[st.id] = detour(route, st, km, cfg, cum)
        except (RoutingError, EVTripError) as exc:
            log.warning("Détours : OSRM indisponible : %s", exc)
            out.osrm_error = user_message(exc)
            break
    return out


TABLE_CHUNK = 20  # stations per table request (3 points each: the public server limits a table to 100 points)


def osrm_detours(
    route: Route,
    items: list[tuple[Station, float]],
    cfg: ChargingConfig,
    *,
    cum: list[float] | None = None,
    table_fn: Callable[..., tuple[list[list[float | None]], list[list[float | None]]]] | None = None,
    chunk: int = TABLE_CHUNK,
) -> dict[str, tuple[float, float]]:
    """Real detours (km, min) of (station, route km) pairs from a few OSRM `table` requests, same measure as `osrm_detour`."""
    if table_fn is None:
        from ev_trip.providers.osrm import osrm_table as table_fn
    cum = cum or cumulative_km(route.coordinates)
    out: dict[str, tuple[float, float]] = {}
    for start in range(0, len(items), chunk):
        part = items[start : start + chunk]
        n = len(part)
        anchors = [detour_anchors_km(km, cum[-1], cfg) for _, km in part]
        points = [point_at_km(route, a, cum) for a, _ in anchors] + [st.point for st, _ in part] + [point_at_km(route, b, cum) for _, b in anchors]
        # rows: A_i then S_i; columns: S_j then B_j (points are A, S, B in this order)
        dist, minutes = table_fn(points, list(range(2 * n)), list(range(n, 3 * n)))
        for i, (st, _) in enumerate(part):
            legs = [(dist[i][i], minutes[i][i]), (dist[n + i][n + i], minutes[n + i][n + i])]  # A -> S, S -> B
            if any(d is None or t is None for d, t in legs):
                continue  # no road: left to the one-by-one search
            out[st.id] = detour_from_via(route, cum, anchors[i][0], anchors[i][1], sum(d for d, _ in legs), sum(t for _, t in legs))
    return out


def find_stations(
    reports: list[TripReport], max_detour_km: float, *, stations: StationSource | None = None
) -> list[Station]:
    """Stations near any of the routes, searched once per route and merged (by id): one pool for all profiles."""
    if stations is None:
        from ev_trip.providers.overpass import OverpassStationSource

        stations = OverpassStationSource(cfg=reports[0].config.charging)
    pool: dict[str, Station] = {}
    for report in reports:
        radius = max_detour_km * report.config.charging.corridor_factor
        for st in stations.find(report.route, radius):
            pool.setdefault(st.id, st)
    return list(pool.values())


def plan_charging(
    report: TripReport,
    max_detour_km: float,
    *,
    tariffs: TariffTable | None = None,
    stations: StationSource | None = None,
    detour_fn: Callable[..., tuple[float, float]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    chargers: tuple[str, ...] | None = None,
    charge_window: tuple[float, float] | None = None,
    charge_minutes: dict[str, float] | None = None,
    pool: list[Station] | None = None,
    prefetch: StationPrefetch | None = None,
) -> ChargingPlan:
    """Stations ranked by relevance score, with a detour <= `max_detour_km`; `pool` = stations already searched (by `find_stations`); `prefetch` = corridor and detours already fetched (`fetch_stations`)."""
    if max_detour_km <= 0:
        raise ValueError("Le détour maximum doit être > 0 km.")
    if stations is None and pool is None and prefetch is None:
        from ev_trip.providers.overpass import OverpassStationSource

        stations = OverpassStationSource(cfg=report.config.charging)
    if chargers:  # the vehicle's accepted charging levels cap the charge power
        report = replace(report, config=replace(report.config, charging=apply_chargers(report.config.charging, chargers)))
    if charge_window and charge_minutes:  # user-timed charge: effective power per level, recharge capped at the window end
        timed = apply_charge_profile(
            report.config.charging, report.simulation.vehicle.battery_kwh, charge_window, charge_minutes, chargers)
        report = replace(report, config=replace(report.config, charging=timed))
    cc = report.config.charging
    route = report.route
    cum = cumulative_km(route.coordinates)
    level = need_level(report)
    plan = ChargingPlan([], 0, 0, max_detour_km, level, type_filtered=bool(chargers))

    if prefetch is not None and prefetch.error is not None:
        plan.warnings.append(f"Recherche des bornes impossible ({prefetch.error}).")
        return plan
    try:
        radius = max_detour_km * cc.corridor_factor
        if prefetch is not None:
            found = prefetch.found
        elif pool is not None:  # shared pool: keep the stations along this route
            found = [st for st in pool if nearest_on_route(route, st.point, cum)[1] <= radius]
        else:
            found = stations.find(route, radius)
    except (RoadClassificationError, EVTripError) as exc:
        log.warning("Recherche des bornes impossible : %s", exc)
        plan.warnings.append(f"Recherche des bornes impossible ({user_message(exc)}).")
        return plan
    plan.found = len(found)
    if chargers:
        kept = [st for st in found if station_compatible(st, chargers)]
        if len(kept) < len(found):
            plan.warnings.append(f"{len(found) - len(kept)} borne(s) écartée(s) : type de chargeur non sélectionné.")
        found = kept
    if not found:
        return plan

    # Preselection: detour estimated as the crow flies, provisional score.
    prelim = []
    for st in found:
        km, dist = nearest_on_route(route, st.point, cum)
        est_km, est_min = estimate_detour(dist, cc)
        sug = score_station(report, st, km, est_km, est_min, max_detour_km, level=level, tariffs=tariffs, estimated=True)
        prelim.append((sug.score, -est_km, st, km, est_km, est_min))
    prelim.sort(key=lambda t: (t[0], t[1]), reverse=True)
    candidates = prelim[: cc.max_candidates]
    plan.evaluated = len(candidates)

    detour = detour_fn or (lambda r, st, km, c, cum_: osrm_detour(r, st, km, c, cum=cum_))
    osrm_ok = True
    routed = False  # a detour was requested from OSRM: the next one waits (demo server)
    results: list[StationSuggestion] = []
    if prefetch is not None and prefetch.osrm_error is not None:
        plan.warnings.append(f"Détours estimés à vol d'oiseau (OSRM indisponible : {prefetch.osrm_error}).")
    for n, (_, _, st, km, est_km, est_min) in enumerate(candidates):
        d_km, d_min, estimated = est_km, est_min, True
        known = prefetch.detours.get(st.id) if prefetch is not None else None
        if known is not None:  # already routed while the trip was refined
            d_km, d_min = known
            estimated = False
        elif osrm_ok and not (prefetch is not None and prefetch.osrm_error is not None):
            if routed and cc.osrm_pause_s:
                sleep(cc.osrm_pause_s)
            routed = True
            try:
                d_km, d_min = detour(route, st, km, cc, cum)
                estimated = False
            except (RoutingError, EVTripError) as exc:
                osrm_ok = False
                log.warning("Détours : OSRM indisponible : %s", exc)
                plan.warnings.append(f"Détours estimés à vol d'oiseau (OSRM indisponible : {user_message(exc)}).")
        if d_km > max_detour_km:
            continue
        results.append(
            score_station(report, st, km, d_km, d_min, max_detour_km, level=level, tariffs=tariffs, estimated=estimated)
        )

    results.sort(key=lambda s: (s.score, -s.detour_km), reverse=True)
    plan.suggestions = results[: cc.max_results]
    if level != "optionnelle":  # a recharge is useful: chain the best portion/station couples
        plan.stops, stop_warns = plan_stops(report, results, max_detour_km, tariffs)
        plan.warnings += stop_warns
        plan.covered = bool(plan.stops) and not stop_warns
        if plan.covered:
            plan.final_soc_pct = final_soc_with_stops(report, plan.stops)
            plan.curve = curve_with_stops(report, plan.stops)
            times = [c.charge_min for c in plan.stops]
            plan.charge_total_min = None if None in times else sum(times)
    return plan
