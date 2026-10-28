"""Charging-station use case: search, real detour (routing), scoring and ranking."""
from __future__ import annotations

import logging
import time
from typing import Callable

from ev_trip.application.messages import user_message
from ev_trip.domain.charging import (
    ChargingPlan, Station, StationSuggestion, TariffTable, apply_charge_profile, apply_chargers, detour_anchors_km,
    detour_from_via,
    estimate_detour, need_level, score_station, station_compatible,
)
from ev_trip.domain.config import ChargingConfig
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
) -> ChargingPlan:
    """Stations ranked by relevance score, with a detour <= `max_detour_km`; `pool` = stations already searched (by `find_stations`)."""
    if max_detour_km <= 0:
        raise ValueError("Le détour maximum doit être > 0 km.")
    if stations is None and pool is None:
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
    plan = ChargingPlan([], 0, 0, max_detour_km, level)

    try:
        radius = max_detour_km * cc.corridor_factor
        if pool is not None:  # shared pool: keep the stations along this route
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
    results: list[StationSuggestion] = []
    for n, (_, _, st, km, est_km, est_min) in enumerate(candidates):
        d_km, d_min, estimated = est_km, est_min, True
        if osrm_ok:
            if n and cc.osrm_pause_s:
                sleep(cc.osrm_pause_s)
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
    return plan
