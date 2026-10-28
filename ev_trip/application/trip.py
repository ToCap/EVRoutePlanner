"""Orchestration: addresses -> route -> slices -> simulation (one or two passes)."""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Callable

from ev_trip.domain.config import ConsumptionConfig
from ev_trip.domain.errors import EVTripError, RoadClassificationError
from ev_trip.domain.geo import resample
from ev_trip.domain.models import MicroSegment, Point, Route, Slice, VehicleParams
from ev_trip.domain.ports import GeocodeFn, RoadClassifier, RouteFn
from ev_trip.application.exit_variants import exit_variants
from ev_trip.domain.consumption import slice_energy_kwh
from ev_trip.domain.route_profiles import ProfileReports, choose_profiles, distinct_indices
from ev_trip.domain.road_classification import classify_by_speed
from ev_trip.domain.sampling import CoarsePlan, needs_refinement, plan_coarse, plan_fine
from ev_trip.domain.simulation import simulate_all
from ev_trip.domain.slicing import build_slices, smooth_types
from ev_trip.domain.trip_report import TripReport
from ev_trip.providers.nominatim import geocode
from ev_trip.providers.junctions import overpass_junctions
from ev_trip.providers.osrm import osrm_route, osrm_routes
from ev_trip.providers.overpass import OverpassRoadClassifier

log = logging.getLogger(__name__)

__all__ = ["ProfileReports", "TripReport", "plan_slices", "refine_trip", "run_trip", "run_trip_profiles"]


def _plan_grid(
    route: Route, cfg: ConsumptionConfig, classifier: RoadClassifier | None
) -> tuple[list[Slice], list[str]]:
    """Route without steps: one Overpass point per `sample_step_km` sample."""
    warnings: list[str] = []
    samples = resample(route, cfg.sample_step_km)

    types: list[str | None] = [None] * len(samples)
    if classifier is not None:
        try:
            types = classifier.classify([s.mid for s in samples])
        except RoadClassificationError as exc:
            warnings.append(f"{exc} : types de route estimés d'après la vitesse (précision réduite).")

    missing = 0
    segments: list[MicroSegment] = []
    for sample, rtype in zip(samples, types):
        if rtype is None:
            missing += 1
            rtype = classify_by_speed(sample.avg_speed_kmh, cfg)
        segments.append(MicroSegment(sample.start_km, sample.end_km, rtype))

    if missing and classifier is not None and not warnings:
        warnings.append(
            f"{missing}/{len(samples)} tronçons sans donnée OSM : type estimé d'après la vitesse."
        )

    segments = smooth_types(segments, cfg.min_run_km)
    return build_slices(segments, cfg.max_slice_km), warnings


def plan_slices(
    route: Route,
    cfg: ConsumptionConfig,
    classifier: RoadClassifier | None = None,
) -> tuple[list[Slice], list[str]]:
    """Splits a route into typed slices (final result); returns (slices, warnings)."""
    if not route.steps:
        return _plan_grid(route, cfg, classifier)
    slices, warnings, coarse = plan_coarse(route, cfg, classifier)
    if classifier is not None and needs_refinement(coarse, cfg):
        return plan_fine(route, cfg, classifier, coarse)
    return slices, warnings


def run_trip(
    origin: str,
    destination: str,
    temp_c: float,
    vehicle: VehicleParams,
    cfg: ConsumptionConfig | None = None,
    *,
    geocode_fn: GeocodeFn = geocode,
    route_fn: RouteFn = osrm_route,
    classifier: RoadClassifier | None = None,
    use_overpass: bool = True,
    refine: bool = True,
) -> TripReport:
    """Computes a trip; `refine=False` returns a fast first estimate (one Overpass point per road section)."""
    cfg = cfg or ConsumptionConfig()
    if classifier is None and use_overpass:
        classifier = OverpassRoadClassifier()

    report = _estimate(origin, destination, route_fn(geocode_fn(origin), geocode_fn(destination)), temp_c, vehicle, cfg, classifier)
    return refine_trip(report, classifier) if refine and report.refinable else report


def _estimate(
    origin: str, destination: str, route: Route, temp_c: float, vehicle: VehicleParams, cfg: ConsumptionConfig,
    classifier: RoadClassifier | None,
) -> TripReport:
    """First estimate of one route (one Overpass point per road section)."""
    coarse: CoarsePlan | None = None
    precision = "fine"
    if route.steps:
        slices, warnings, coarse = plan_coarse(route, cfg, classifier)
        if classifier is not None and needs_refinement(coarse, cfg):
            precision = "coarse"
    else:
        slices, warnings = _plan_grid(route, cfg, classifier)

    sims = simulate_all(slices, vehicle, temp_c, cfg)
    return TripReport(origin, destination, route, slices, sims, warnings, cfg, precision, coarse)


def route_energy_kwh(report: TripReport, temp_c: float) -> float:
    """Energy of the whole route in the configured scenario (battery-independent: ranks routes, not interrupted runs)."""
    return sum(slice_energy_kwh(s.length_km, s.road_type, temp_c, report.config, report.config.scenario) for s in report.slices)


def _overpass_variants(route: Route, destination: Point, k: int) -> list[Route]:
    """Exit variants with the real providers: Overpass for the exits, OSRM without motorways for the last leg."""
    return exit_variants(
        route, destination, k, junctions_fn=overpass_junctions,
        leg_fn=lambda a, b: osrm_routes(a, b, alternatives=0, exclude="motorway")[0],
    )


def run_trip_profiles(
    origin: str,
    destination: str,
    temp_c: float,
    vehicle: VehicleParams,
    cfg: ConsumptionConfig | None = None,
    *,
    geocode_fn: GeocodeFn = geocode,
    routes_fn: Callable[..., list[Route]] = osrm_routes,
    variants_fn: Callable[[Route, Point, int], list[Route]] | None = None,
    classifier: RoadClassifier | None = None,
    use_overpass: bool = True,
    refine: bool = True,
) -> ProfileReports[TripReport]:
    """Fast route (shortest duration) and consumption route (least energy within `eco_max_extra_duration_pct` of it).

    Candidates: the OSRM alternatives plus up to `eco_exit_variants` routes leaving the motorway at an earlier exit
    (`variants_fn`, by default the Overpass junction search; skipped without Overpass). They are ranked on their first
    estimate; `refine=False` returns those estimates.
    """
    cfg = cfg or ConsumptionConfig()
    if classifier is None and use_overpass:
        classifier = OverpassRoadClassifier()

    start, end = geocode_fn(origin), geocode_fn(destination)
    routes = routes_fn(start, end)
    notes: list[str] = []
    if variants_fn is None and use_overpass:
        variants_fn = _overpass_variants
    if variants_fn is not None and cfg.eco_exit_variants > 0:
        try:
            found = variants_fn(routes[0], end, cfg.eco_exit_variants)
            routes = [*routes, *found]
            if not found:
                notes.append("Aucune variante de sortie d'autoroute trouvée.")
        except EVTripError as exc:
            notes.append(f"{exc} : variantes de sortie non étudiées.")
    routes = [routes[i] for i in distinct_indices([(r.distance_km, r.duration_min) for r in routes])]
    reports = [_estimate(origin, destination, r, temp_c, vehicle, cfg, classifier) for r in routes]
    for r in reports:
        r.warnings.extend(notes)
    energies = [route_energy_kwh(r, temp_c) for r in reports]
    fast_i, eco_i = choose_profiles([(r.route.duration_min, e) for r, e in zip(reports, energies)], cfg.eco_max_extra_duration_pct)
    log.info("Itinéraires candidats : %s ; rapide = %d, consommation = %d", "; ".join(
        f"#{i} {r.route.distance_km:.1f} km {r.route.duration_min:.0f} min {e:.1f} kWh" for i, (r, e) in enumerate(zip(reports, energies))),
        fast_i, eco_i)
    fast, eco = reports[fast_i], reports[eco_i]
    if refine:
        fast = refine_trip(fast, classifier) if fast.refinable else fast
        eco = fast if eco_i == fast_i else (refine_trip(eco, classifier) if eco.refinable else eco)
    return ProfileReports(fast, eco)


def add_exit_variants(
    profiles: ProfileReports[TripReport], k: int, *,
    variants_fn: Callable[[Route, Point, int], list[Route]] | None = None, classifier: RoadClassifier | None = None,
) -> ProfileReports[TripReport]:
    """Profiles re-ranked with up to `k` motorway exit variants of the fast route (raises `EVTripError` if the search fails)."""
    fast = profiles.fast
    base = [fast] if profiles.same else [fast, profiles.eco]
    found = (variants_fn or _overpass_variants)(fast.route, fast.route.coordinates[-1], k)
    routes = [r.route for r in base] + found
    keep = [i for i in distinct_indices([(r.distance_km, r.duration_min) for r in routes]) if i >= len(base)]
    if not keep:
        for r in {id(fast): fast, id(profiles.eco): profiles.eco}.values():  # the displayed route is the consumption one
            r.warnings.append("Aucune variante de sortie d'autoroute trouvée.")
        return profiles
    first, classifier = fast.simulation, classifier or OverpassRoadClassifier()
    reports = base + [_estimate(fast.origin, fast.destination, routes[i], first.temp_c, first.vehicle, fast.config, classifier) for i in keep]
    energies = [route_energy_kwh(r, first.temp_c) for r in reports]
    fast_i, eco_i = choose_profiles([(r.route.duration_min, e) for r, e in zip(reports, energies)], fast.config.eco_max_extra_duration_pct)
    return ProfileReports(reports[fast_i], reports[eco_i])


def refine_trip(
    report: TripReport, classifier: RoadClassifier | None = None, *, use_overpass: bool = True
) -> TripReport:
    """Pass 2: denser Overpass points on non-motorway sections, then a new simulation."""
    if not report.refinable or report.coarse is None:
        return report
    if classifier is None and use_overpass:
        classifier = OverpassRoadClassifier()
    if classifier is None:
        return replace(report, precision="fine")

    first = report.simulation
    slices, warnings = plan_fine(report.route, report.config, classifier, report.coarse)
    sims = simulate_all(slices, first.vehicle, first.temp_c, report.config)
    return replace(report, slices=slices, simulations=sims, warnings=warnings, precision="fine")
