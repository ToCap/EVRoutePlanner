"""Orchestration: addresses -> route -> slices -> simulation (one or two passes)."""
from __future__ import annotations

from dataclasses import replace
from typing import Callable

from ev_trip.domain.config import ConsumptionConfig
from ev_trip.domain.errors import RoadClassificationError
from ev_trip.domain.geo import resample
from ev_trip.domain.models import MicroSegment, Point, Route, Slice, VehicleParams
from ev_trip.domain.ports import GeocodeFn, RoadClassifier, RouteFn
from ev_trip.domain.road_classification import classify_by_speed
from ev_trip.domain.sampling import CoarsePlan, needs_refinement, plan_coarse, plan_fine
from ev_trip.domain.simulation import simulate_all
from ev_trip.domain.slicing import build_slices, smooth_types
from ev_trip.domain.trip_report import TripReport
from ev_trip.providers.nominatim import geocode
from ev_trip.providers.osrm import osrm_route
from ev_trip.providers.overpass import OverpassRoadClassifier

__all__ = ["TripReport", "plan_slices", "refine_trip", "run_trip"]


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

    route = route_fn(geocode_fn(origin), geocode_fn(destination))
    coarse: CoarsePlan | None = None
    precision = "fine"
    if route.steps:
        slices, warnings, coarse = plan_coarse(route, cfg, classifier)
        if classifier is not None and needs_refinement(coarse, cfg):
            precision = "coarse"
    else:
        slices, warnings = _plan_grid(route, cfg, classifier)

    sims = simulate_all(slices, vehicle, temp_c, cfg)
    report = TripReport(origin, destination, route, slices, sims, warnings, cfg, precision, coarse)
    return refine_trip(report, classifier) if refine and report.refinable else report


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
