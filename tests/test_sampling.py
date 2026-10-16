import pytest

from ev_trip.domain.config import ConsumptionConfig
from ev_trip.domain.errors import RoadClassificationError
from ev_trip.domain.geo import cumulative_km, haversine_km
from ev_trip.domain.models import Point, Route, RouteStep, VehicleParams
from ev_trip.providers.osrm import osrm_route
from ev_trip.domain.sampling import anchor_kms, fine_count, road_runs
from ev_trip.application.trip import plan_slices, refine_trip, run_trip

from test_osm_and_trip import FakeResponse, FakeSession

CFG = ConsumptionConfig()
VEHICLE = VehicleParams(52, 90, 10)


def stepped_route(with_speed=True):
    """~60 km: street 0-7 km, N 13 from 7 to 40 km, A 13 from 40 km to the arrival."""
    n = 120
    pts = [Point(48.0 + (60 / 111.195) * i / n, 0.0) for i in range(n + 1)]
    total = cumulative_km(pts)[-1]
    steps = [
        RouteStep(0.0, 7.0, "Rue de la Gare", ""),
        RouteStep(7.0, 20.0, "Route de Paris", "N 13"),
        RouteStep(20.0, 40.0, "Route de Paris", "N13"),  # same road written differently: merged
        RouteStep(40.0, total, "Autoroute de Normandie", "A 13"),
    ]
    durations = [total / 90 * 3600 / n] * n if with_speed else None
    return Route(pts, total, total / 90 * 60, durations_s=durations, steps=steps)


class KmClassifier:
    """city < 7 km; N13: route, except a village between km 30 and 32; motorway after 40 km."""

    def __init__(self, origin):
        self.origin, self.calls = origin, []

    def classify(self, points):
        self.calls.append(len(points))
        out = []
        for p in points:
            km = haversine_km(self.origin, p)
            out.append("ville" if km < 7 or 30 <= km < 32 else ("route" if km < 40 else "autoroute"))
        return out


def types_by_range(slices):
    return [(s.road_type, round(s.start_km, 1), round(s.end_km, 1)) for s in slices]


# --- OSRM steps -------------------------------------------------------------------------
def test_osrm_steps_are_parsed_and_rescaled_to_the_polyline():
    payload = {
        "code": "Ok",
        "routes": [{
            "distance": 6000.0, "duration": 360.0,
            "geometry": {"coordinates": [[2.0, 48.0], [2.0, 48.027], [2.0, 48.054]]},
            "legs": [{"annotation": {"duration": [180.0, 180.0]}, "steps": [
                {"distance": 2000.0, "name": "Rue A", "ref": ""},
                {"distance": 4000.0, "name": "", "ref": "A 13"},
                {"distance": 0.0, "name": "", "ref": ""},  # arrival marker
            ]}],
        }],
    }
    r = osrm_route(Point(48.0, 2.0), Point(48.054, 2.0), session=FakeSession([FakeResponse(payload)]))
    total = cumulative_km(r.coordinates)[-1]
    assert [(s.name, s.ref) for s in r.steps] == [("Rue A", ""), ("", "A 13")]
    assert r.steps[0].start_km == 0 and r.steps[0].end_km == pytest.approx(total / 3)
    assert r.steps[-1].end_km == pytest.approx(total)


def test_osrm_without_steps_gives_none():
    payload = {"code": "Ok", "routes": [{
        "distance": 2000.0, "duration": 120.0,
        "geometry": {"coordinates": [[2.0, 48.0], [2.0, 48.02]]}, "legs": [{"annotation": {"duration": [120.0]}}]}]}
    assert osrm_route(Point(48, 2), Point(48.02, 2), session=FakeSession([FakeResponse(payload)])).steps is None


# --- sections ------------------------------------------------------------------------------
def test_runs_merge_steps_on_the_same_road_whatever_its_spelling():
    runs = road_runs(stepped_route().steps)
    assert [(r.label, r.start_km, r.end_km) for r in runs[:2]] == [("Rue de la Gare", 0.0, 7.0), ("N 13", 7.0, 40.0)]
    assert len(runs) == 3


def test_anchors_are_midpoints_of_equal_subintervals():
    run = road_runs(stepped_route().steps)[1]  # 7 -> 40 km
    assert anchor_kms(run, 1) == [pytest.approx(23.5)]
    assert fine_count(run, 1.0) == 33
    kms = anchor_kms(run, 33)
    assert kms[0] == pytest.approx(7.5) and kms[-1] == pytest.approx(39.5)


# --- pass 1: one point per section ---------------------------------------------------------
def test_coarse_pass_queries_one_point_per_road_run():
    route = stepped_route()
    clf = KmClassifier(route.coordinates[0])
    rep = run_trip("A", "B", 20, VEHICLE, geocode_fn=lambda q: route.coordinates[0],
                   route_fn=lambda a, b: route, classifier=clf, refine=False)
    assert clf.calls == [3]  # 3 sections -> 3 points, instead of ~120 samples of 500 m
    assert rep.precision == "coarse" and rep.refinable and rep.coarse.types == ["ville", "route", "autoroute"]
    # exact limits at road changes (not rounded to the 500 m grid)
    t = types_by_range(rep.slices)
    assert t[0] == ("ville", 0.0, 7.0)
    assert t[1][0] == "route" and t[1][1] == 7.0 and not any(s[0] == "ville" for s in t[1:])  # village not seen yet
    assert [s for s in t if s[0] == "autoroute"][0][1] == 40.0
    assert all(s.length_km <= 10 + 1e-9 for s in rep.slices)


# --- pass 2: refinement ---------------------------------------------------------------------
def test_refine_finds_the_village_and_skips_motorway_runs():
    route = stepped_route()
    clf = KmClassifier(route.coordinates[0])
    rep = run_trip("A", "B", 20, VEHICLE, geocode_fn=lambda q: route.coordinates[0],
                   route_fn=lambda a, b: route, classifier=clf, refine=False)
    before_kwh = rep.simulation.total_kwh
    fine = refine_trip(rep, clf)
    # street (7 points) + N 13 (33 points); the motorway keeps the pass-1 type: no query
    assert clf.calls == [3, 40]
    assert fine.precision == "fine" and not fine.refinable and fine.coarse is rep.coarse
    village = [s for s in fine.slices if s.road_type == "ville" and s.start_km > 7]
    assert len(village) == 1 and village[0].start_km == pytest.approx(30.0, abs=0.01)
    assert village[0].end_km == pytest.approx(32.0, abs=0.01)
    assert fine.simulation.total_kwh < before_kwh  # the village (50 km/h) consumes less than the road
    assert refine_trip(fine, clf) is fine  # already refined: nothing to redo
    assert clf.calls == [3, 40]


def test_run_trip_refines_by_default_and_matches_the_two_steps():
    route = stepped_route()
    one_shot = run_trip("A", "B", 20, VEHICLE, geocode_fn=lambda q: route.coordinates[0],
                        route_fn=lambda a, b: route, classifier=KmClassifier(route.coordinates[0]))
    assert one_shot.precision == "fine"
    assert types_by_range(one_shot.slices) == types_by_range(
        plan_slices(route, CFG, KmClassifier(route.coordinates[0]))[0])
    assert any(s.road_type == "ville" and s.start_km > 7 for s in one_shot.slices)


def test_far_fewer_overpass_points_than_the_500m_grid():
    route = stepped_route()
    grid_points = round(cumulative_km(route.coordinates)[-1] / CFG.sample_step_km)  # ~120 points before
    coarse_clf = KmClassifier(route.coordinates[0])
    run_trip("A", "B", 20, VEHICLE, geocode_fn=lambda q: route.coordinates[0],
             route_fn=lambda a, b: route, classifier=coarse_clf, refine=False)
    assert sum(coarse_clf.calls) < grid_points / 20


def test_route_without_steps_keeps_the_grid_behaviour():
    route = stepped_route()
    route.steps = None
    clf = KmClassifier(route.coordinates[0])
    rep = run_trip("A", "B", 20, VEHICLE, geocode_fn=lambda q: route.coordinates[0],
                   route_fn=lambda a, b: route, classifier=clf, refine=False)
    assert rep.precision == "fine" and rep.coarse is None and sum(clf.calls) > 100


def test_motorway_only_trip_needs_no_refinement():
    route = stepped_route()
    route.steps = [RouteStep(0.0, route.steps[-1].end_km, "Autoroute", "A 13")]

    class Motorway:
        calls = []

        def classify(self, points):
            self.calls.append(len(points))
            return ["autoroute"] * len(points)

    clf = Motorway()
    rep = run_trip("A", "B", 20, VEHICLE, geocode_fn=lambda q: route.coordinates[0],
                   route_fn=lambda a, b: route, classifier=clf, refine=False)
    assert rep.precision == "fine" and clf.calls == [1]


def test_no_classifier_means_nothing_to_refine():
    route = stepped_route()
    rep = run_trip("A", "B", 20, VEHICLE, geocode_fn=lambda q: route.coordinates[0],
                   route_fn=lambda a, b: route, use_overpass=False, refine=False)
    assert rep.precision == "fine"
    assert {s.road_type for s in rep.slices} == {"autoroute"}  # 90 km/h average -> fall back to speed


class FlakyClassifier(KmClassifier):
    """Unavailable on the first call (e.g. HTTP 429), then restored."""

    def classify(self, points):
        if not self.calls and not getattr(self, "failed", False):
            self.failed = True
            raise RoadClassificationError("Overpass indisponible : HTTP 429")
        return super().classify(points)


def test_refinement_retries_when_overpass_failed_in_the_first_pass():
    route = stepped_route()
    clf = FlakyClassifier(route.coordinates[0])
    rep = run_trip("A", "B", 20, VEHICLE, geocode_fn=lambda q: route.coordinates[0],
                   route_fn=lambda a, b: route, classifier=clf, refine=False)
    assert rep.warnings and "HTTP 429" in rep.warnings[0]
    assert rep.coarse.types == [None, None, None] and rep.refinable  # speed fallback, but to be retried
    fine = refine_trip(rep, clf)
    assert fine.warnings == [] and fine.precision == "fine"
    assert any(s.road_type == "ville" and s.start_km > 7 for s in fine.slices)


def test_refinement_failure_keeps_a_usable_result_with_a_warning():
    route = stepped_route()

    class Down:
        def classify(self, points):
            raise RoadClassificationError("Overpass indisponible : HTTP 429")

    rep = run_trip("A", "B", 20, VEHICLE, geocode_fn=lambda q: route.coordinates[0],
                   route_fn=lambda a, b: route, classifier=KmClassifier(route.coordinates[0]), refine=False)
    fine = refine_trip(rep, Down())
    assert fine.precision == "fine" and fine.slices and "HTTP 429" in fine.warnings[0]
    # known pass-1 types are kept when a refinement query fails
    assert {s.road_type for s in fine.slices} >= {"ville", "route", "autoroute"}


def test_refine_step_must_be_positive():
    with pytest.raises(ValueError):
        ConsumptionConfig(refine_step_km=0)
