"""Per-section sampling to limit Overpass requests: 1 point per section, then denser points off motorways (names only decide where)."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from ev_trip.domain.config import ConsumptionConfig
from ev_trip.domain.errors import RoadClassificationError
from ev_trip.domain.geo import Sample, cumulative_km, point_at_km, resample
from ev_trip.domain.models import MicroSegment, Point, Route, RouteStep, Slice
from ev_trip.domain.ports import RoadClassifier
from ev_trip.domain.road_classification import classify_by_speed
from ev_trip.domain.slicing import build_slices, smooth_types

_EPS = 1e-9


@dataclass(frozen=True)
class RoadRun:
    """Section: consecutive steps on the same road."""

    start_km: float
    end_km: float
    label: str

    @property
    def length_km(self) -> float:
        return self.end_km - self.start_km


@dataclass
class CoarsePlan:
    """Pass 1 result, reused by the refinement."""

    runs: list[RoadRun]
    types: list[str | None]  # type found for each section (None: unknown)


def _identity(step: RouteStep) -> str:
    """The same road written "A 13", "A13" or "a-13" is matched as one."""
    return re.sub(r"[\s\-_.]+", "", (step.ref or step.name).lower())


def road_runs(steps: list[RouteStep]) -> list[RoadRun]:
    runs: list[RoadRun] = []
    last = None
    for st in steps:
        ident = _identity(st)
        if runs and ident == last:
            runs[-1] = RoadRun(runs[-1].start_km, st.end_km, runs[-1].label)
        else:
            runs.append(RoadRun(st.start_km, st.end_km, st.ref or st.name or "?"))
            last = ident
    return runs


def fine_count(run: RoadRun, step_km: float) -> int:
    return max(1, math.ceil(run.length_km / step_km - _EPS))


def anchor_kms(run: RoadRun, n: int) -> list[float]:
    """n points at the middle of n equal subintervals of the section."""
    return [run.start_km + (i + 0.5) * run.length_km / n for i in range(n)]


def _speed_segments(samples: list[Sample], start: float, end: float, cfg: ConsumptionConfig) -> list[MicroSegment]:
    """Fallback without OSM data: type from OSRM average speed, per 500 m sample."""
    out = []
    for sm in samples:
        a, b = max(sm.start_km, start), min(sm.end_km, end)
        if b - a > _EPS:
            out.append(MicroSegment(a, b, classify_by_speed(sm.avg_speed_kmh, cfg)))
    return out


def build_segments(
    runs: list[RoadRun],
    anchors: list[list[float]],
    types: list[list[str | None]],
    samples: list[Sample],
    cfg: ConsumptionConfig,
) -> list[MicroSegment]:
    """Each anchor point gives its type to the nearest section portion (cuts at midpoints)."""
    segments: list[MicroSegment] = []
    for run, kms, rtypes in zip(runs, anchors, types):
        bounds = [run.start_km] + [(a + b) / 2 for a, b in zip(kms, kms[1:])] + [run.end_km]
        for (start, end), rtype in zip(zip(bounds, bounds[1:]), rtypes):
            if end - start <= _EPS:
                continue
            if rtype is None:
                segments.extend(_speed_segments(samples, start, end, cfg))
            else:
                segments.append(MicroSegment(start, end, rtype))
    return segments


def _classify(classifier: RoadClassifier | None, points: list[Point], warnings: list[str]) -> list[str | None]:
    if classifier is None or not points:
        return [None] * len(points)
    try:
        return classifier.classify(points)
    except RoadClassificationError as exc:
        warnings.append(f"{exc} : types de route estimés d'après la vitesse (précision réduite).")
        return [None] * len(points)


def _warn_missing(classifier: RoadClassifier | None, types: list[str | None], warnings: list[str]) -> None:
    missing = sum(t is None for t in types)
    if classifier is not None and missing and not warnings:
        warnings.append(f"{missing}/{len(types)} points sans donnée OSM : type estimé d'après la vitesse.")


def _finish(segments: list[MicroSegment], cfg: ConsumptionConfig) -> list[Slice]:
    return build_slices(smooth_types(segments, cfg.min_run_steps_km), cfg.max_slice_km)


def plan_coarse(
    route: Route, cfg: ConsumptionConfig, classifier: RoadClassifier | None
) -> tuple[list[Slice], list[str], CoarsePlan]:
    """Pass 1: one Overpass point per section."""
    runs = road_runs(route.steps or [])
    cum = cumulative_km(route.coordinates)
    mids = [anchor_kms(r, 1)[0] for r in runs]
    warnings: list[str] = []
    types = _classify(classifier, [point_at_km(route, km, cum) for km in mids], warnings)
    _warn_missing(classifier, types, warnings)
    samples = resample(route, cfg.sample_step_km)
    segments = build_segments(runs, [[m] for m in mids], [[t] for t in types], samples, cfg)
    return _finish(segments, cfg), warnings, CoarsePlan(runs, types)


def _needs_query(run: RoadRun, coarse_type: str | None, cfg: ConsumptionConfig) -> bool:
    if coarse_type is None:
        return True  # unknown type in pass 1: refinement also acts as a retry
    return coarse_type != "autoroute" and fine_count(run, cfg.refine_step_km) > 1


def needs_refinement(coarse: CoarsePlan, cfg: ConsumptionConfig) -> bool:
    return any(_needs_query(r, t, cfg) for r, t in zip(coarse.runs, coarse.types))


def plan_fine(
    route: Route, cfg: ConsumptionConfig, classifier: RoadClassifier | None, coarse: CoarsePlan
) -> tuple[list[Slice], list[str]]:
    """Pass 2: denser points on non-motorway sections (others keep the pass-1 type)."""
    cum = cumulative_km(route.coordinates)
    plan: list[tuple[list[float], str | None, bool]] = []  # (abscissas, pass-1 type, to query?)
    query_points: list[Point] = []
    for run, ctype in zip(coarse.runs, coarse.types):
        if classifier is not None and _needs_query(run, ctype, cfg):
            kms = anchor_kms(run, fine_count(run, cfg.refine_step_km))
            query_points.extend(point_at_km(route, km, cum) for km in kms)
            plan.append((kms, ctype, True))
        else:
            plan.append((anchor_kms(run, 1), ctype, False))

    warnings: list[str] = []
    answers = iter(_classify(classifier, query_points, warnings))
    anchors, types = [], []
    for kms, ctype, queried in plan:
        anchors.append(kms)
        if queried:
            types.append([a if a is not None else ctype for a in (next(answers) for _ in kms)])
        else:
            types.append([ctype])
    _warn_missing(classifier, [t for ts in types for t in ts], warnings)
    samples = resample(route, cfg.sample_step_km)
    return _finish(build_segments(coarse.runs, anchors, types, samples, cfg), cfg), warnings
