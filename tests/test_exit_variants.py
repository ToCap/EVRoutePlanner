"""Exit variants: routes leaving the motorway at an earlier exit than the fast route."""
import math

import pytest

from ev_trip.application.exit_variants import exit_variants, last_motorway_run
from ev_trip.domain.geo import cumulative_km
from ev_trip.domain.models import Point, Route, RouteStep

COORDS = [Point(48.0 + 0.01 * i, 2.0) for i in range(91)]  # ~100 km along a meridian
CUM = cumulative_km(COORDS)
L = CUM[-1]


def _fast(steps=None):
    steps = steps if steps is not None else [
        RouteStep(0.0, 5.0, "Rue", ""), RouteStep(5.0, 80.0, "Autoroute", "A 13"), RouteStep(80.0, L, "Route", "D 58")]
    return Route(COORDS, L, 100.0, None, steps)


def _node(km, off_lon=0.0005):
    i = min(range(len(CUM)), key=lambda j: abs(CUM[j] - km))
    return Point(COORDS[i].lat, COORDS[i].lon + off_lon)


def _leg(a, b, factor=1.2):
    km = math.hypot((b.lat - a.lat) * 111.2, (b.lon - a.lon) * 74.0) * factor
    return Route([a, b], km, km * 1.5, None, [RouteStep(0.0, cumulative_km([a, b])[-1], "D 1", "")])  # step km follow the polyline


NODES = [_node(3), _node(30), _node(52), _node(66), _node(73), _node(80), _node(60, off_lon=0.02)]  # before motorway / beyond 30 km / exits / fast exit / off route


def _variants(k, route=None, leg=_leg):
    return exit_variants(route or _fast(), COORDS[-1], k, junctions_fn=lambda pts: NODES, leg_fn=leg)


def test_last_motorway_run_and_no_motorway():
    assert last_motorway_run(_fast()) == (5.0, 80.0)
    assert last_motorway_run(_fast([RouteStep(0.0, L, "Route", "D 58")])) is None
    assert last_motorway_run(Route(COORDS, L, 100.0)) is None


def test_variants_leave_at_the_exits_just_before_the_fast_exit():
    variants = _variants(2)
    assert len(variants) == 2
    for v in variants:
        assert v.coordinates[0] == COORDS[0] and v.coordinates[-1] == COORDS[-1]
    near, far = variants  # exit 73 then exit 66: the later the exit, the shorter the last leg
    assert near.distance_km < far.distance_km or near.distance_km == pytest.approx(far.distance_km, rel=0.3)
    assert len(_variants(1)) == 1 and _variants(0) == []
    assert len(_variants(5)) == 3  # valid exits only: not before the search window, not at the fast exit, not off the route


def test_variant_is_a_consistent_route():
    v = _variants(1)[0]
    ends = [(s.start_km, s.end_km) for s in v.steps]
    assert all(a[1] == pytest.approx(b[0]) for a, b in zip(ends, ends[1:])) and ends[0][0] == 0.0
    assert ends[-1][1] == pytest.approx(cumulative_km(v.coordinates)[-1], rel=1e-6)
    assert any(s.ref == "A 13" for s in v.steps) and v.steps[-1].name == "D 1"
    assert v.duration_min > 0 and v.distance_km > 0


def test_too_long_variants_are_dropped():
    assert _variants(2, leg=lambda a, b: _leg(a, b, factor=50.0)) == []


def test_no_motorway_no_variant():
    assert _variants(2, route=_fast([RouteStep(0.0, L, "Route", "D 58")])) == []


def test_refs_are_matched_case_insensitively():
    assert last_motorway_run(_fast([RouteStep(0.0, 50.0, "x", "a-13"), RouteStep(50.0, L, "y", "D 1")])) == (0.0, 50.0)


def test_failing_leg_is_skipped_and_reported_only_when_nothing_is_left():
    from ev_trip.domain.errors import RoutingError

    calls = []

    def flaky(a, b):
        calls.append(1)
        if len(calls) == 1:
            raise RoutingError("exclude refusé")
        return _leg(a, b)

    assert len(_variants(2, leg=flaky)) == 1  # the second exit still gives a variant

    def always(a, b):
        raise RoutingError("exclude refusé")

    with pytest.raises(RoutingError, match="exclude refusé"):
        _variants(2, leg=always)
