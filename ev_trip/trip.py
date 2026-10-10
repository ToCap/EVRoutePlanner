"""Orchestration : adresses -> itinéraire -> tranches -> simulation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .config import DEFAULT_SCENARIO, ConsumptionConfig
from .errors import RoadClassificationError
from .geo import resample
from .geocoding import geocode
from .models import MicroSegment, Point, Route, SimulationResult, Slice, VehicleParams
from .road_types import OverpassRoadClassifier, RoadClassifier, classify_by_speed
from .routing import osrm_route
from .simulation import simulate_all
from .slicing import build_slices, smooth_types


@dataclass
class TripReport:
    origin: str
    destination: str
    route: Route
    slices: list[Slice]
    simulations: dict[str, SimulationResult]  # clé : optimiste / moyenne / pessimiste
    warnings: list[str] = field(default_factory=list)
    config: ConsumptionConfig = field(default_factory=ConsumptionConfig)

    @property
    def simulation(self) -> SimulationResult:
        """Scénario « moyenne » (référence)."""
        return self.simulations[DEFAULT_SCENARIO]


def plan_slices(
    route: Route,
    cfg: ConsumptionConfig,
    classifier: RoadClassifier | None = None,
) -> tuple[list[Slice], list[str]]:
    """Découpe un itinéraire en tranches typées. Renvoie (tranches, avertissements)."""
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


def run_trip(
    origin: str,
    destination: str,
    temp_c: float,
    vehicle: VehicleParams,
    cfg: ConsumptionConfig | None = None,
    *,
    geocode_fn: Callable[[str], Point] = geocode,
    route_fn: Callable[[Point, Point], Route] = osrm_route,
    classifier: RoadClassifier | None = None,
    use_overpass: bool = True,
) -> TripReport:
    cfg = cfg or ConsumptionConfig()
    if classifier is None and use_overpass:
        classifier = OverpassRoadClassifier()

    route = route_fn(geocode_fn(origin), geocode_fn(destination))
    slices, warnings = plan_slices(route, cfg, classifier)
    sims = simulate_all(slices, vehicle, temp_c, cfg)
    return TripReport(origin, destination, route, slices, sims, warnings, cfg)
