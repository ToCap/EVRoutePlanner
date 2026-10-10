import requests
import pytest

from ev_trip import cli
from ev_trip.config import ConsumptionConfig
from ev_trip.errors import GeocodingError, RoadClassificationError
from ev_trip.geocoding import geocode
from ev_trip.geo import haversine_km
from ev_trip.models import Point, Route, VehicleParams
from ev_trip.road_types import (
    OverpassRoadClassifier, best_way_type, classify_by_speed, classify_highway, parse_maxspeed,
)
from ev_trip.routing import osrm_route
from ev_trip.trip import run_trip


# --- tags OSM ------------------------------------------------------------------
@pytest.mark.parametrize(
    "tags, expected",
    [
        ({"highway": "motorway"}, "autoroute"),
        ({"highway": "motorway_link"}, "route"),
        ({"highway": "trunk", "maxspeed": "110"}, "autoroute"),
        ({"highway": "trunk", "maxspeed": "90"}, "route"),
        ({"highway": "trunk"}, "route"),
        ({"highway": "primary"}, "route"),
        ({"highway": "primary", "maxspeed": "50"}, "ville"),
        ({"highway": "secondary", "maxspeed": "FR:urban"}, "ville"),
        ({"highway": "tertiary", "maxspeed": "80"}, "route"),
        ({"highway": "residential"}, "ville"),
        ({"highway": "service"}, "ville"),
        ({"highway": "footway"}, None),
        ({}, None),
    ],
)
def test_classify_highway(tags, expected):
    assert classify_highway(tags) == expected


def test_parse_maxspeed():
    assert parse_maxspeed("50") == 50
    assert parse_maxspeed("30 mph") == pytest.approx(48.28, abs=0.01)
    assert parse_maxspeed("FR:rural") == 80
    assert parse_maxspeed("none") is None
    assert parse_maxspeed(None) is None


def test_classify_by_speed():
    cfg = ConsumptionConfig()
    assert classify_by_speed(30, cfg) == "ville"
    assert classify_by_speed(65, cfg) == "route"
    assert classify_by_speed(100, cfg) == "autoroute"
    assert classify_by_speed(None, cfg) == "route"


def way(highway, lat_lon, **tags):
    return {"tags": {"highway": highway, **tags}, "geometry": [{"lat": a, "lon": b} for a, b in lat_lon]}


def test_best_way_picks_nearest_and_ignores_far_or_non_drivable():
    p = Point(48.0, 2.0)
    near_service = way("service", [(48.0001, 1.999), (48.0001, 2.001)])  # ~11 m
    motorway = way("motorway", [(48.0, 1.999), (48.0, 2.001)])  # 0 m
    far = way("residential", [(48.01, 1.999), (48.01, 2.001)])
    path = way("footway", [(48.0, 1.999), (48.0, 2.001)])
    assert best_way_type(p, [near_service, motorway, far, path], 30) == "autoroute"
    assert best_way_type(p, [far, path], 30) is None


def test_best_way_prefers_important_road_on_near_tie():
    p = Point(48.0, 2.0)
    service = way("service", [(48.00002, 1.999), (48.00002, 2.001)])  # ~2 m
    motorway = way("motorway", [(47.99998, 1.999), (47.99998, 2.001)])  # ~2 m
    assert best_way_type(p, [service, motorway], 30) == "autoroute"


# --- Overpass (faux serveur) -------------------------------------------------------
class FakeResponse:
    def __init__(self, payload=None, status=200):
        self.payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def _next(self, *a, **k):
        self.calls.append((a, k))
        return self.responses.pop(0)

    get = post = _next


def test_overpass_classifier_batches_and_classifies():
    pts = [Point(48.0, 2.0 + i * 0.001) for i in range(5)]
    elements = [way("motorway", [(48.0, 1.99), (48.0, 2.02)])]
    sess = FakeSession([FakeResponse({"elements": elements}), FakeResponse({"elements": []})])
    clf = OverpassRoadClassifier(session=sess, chunk_points=3, pause_s=0)
    assert clf.classify(pts) == ["autoroute"] * 3 + [None, None]
    assert len(sess.calls) == 2
    query = sess.calls[0][1]["data"]["data"]
    assert "around:30" in query and "48.000000,2.000000" in query and "out tags geom" in query
    # Overpass répond 406 sans User-Agent explicite
    assert sess.calls[0][1]["headers"]["User-Agent"].startswith("ev-trip-simulator")


def test_overpass_switches_server_after_406():
    sess = FakeSession([FakeResponse(status=406), FakeResponse({"elements": []})])
    clf = OverpassRoadClassifier(session=sess, url=["https://a/api", "https://b/api"], pause_s=0)
    assert clf.classify([Point(48, 2)]) == [None]
    assert [c[0][0] for c in sess.calls] == ["https://a/api", "https://b/api"]


def test_overpass_failure_raises_after_retries():
    sess = FakeSession([FakeResponse(status=504)] * 2)
    clf = OverpassRoadClassifier(session=sess, retries=2, pause_s=0)
    with pytest.raises(RoadClassificationError):
        clf.classify([Point(48, 2)])
    assert len(sess.calls) == 2


# --- Nominatim / OSRM ------------------------------------------------------------------
def test_geocode_parses_coordinates_without_network():
    assert geocode("48.85, 2.35") == Point(48.85, 2.35)


def test_geocode_via_nominatim_and_not_found():
    ok = FakeSession([FakeResponse([{"lat": "49.1155", "lon": "-1.0903"}])])
    assert geocode("Saint-Lô", session=ok) == Point(49.1155, -1.0903)
    with pytest.raises(GeocodingError):
        geocode("zzzz", session=FakeSession([FakeResponse([])]))


def test_osrm_route_parsing():
    payload = {
        "code": "Ok",
        "routes": [{
            "distance": 2000.0, "duration": 120.0,
            "geometry": {"coordinates": [[2.0, 48.0], [2.0, 48.01], [2.0, 48.02]]},
            "legs": [{"annotation": {"duration": [60.0, 60.0]}}],
        }],
    }
    r = osrm_route(Point(48.0, 2.0), Point(48.02, 2.0), session=FakeSession([FakeResponse(payload)]))
    assert r.coordinates[1] == Point(48.01, 2.0)
    assert r.distance_km == 2.0 and r.duration_min == 2.0 and r.durations_s == [60.0, 60.0]


# --- bout en bout -----------------------------------------------------------------------
def straight_route(km):
    n = 200
    pts = [Point(48.0 + (km / 111.195) * i / n, 0.0) for i in range(n + 1)]
    return Route(pts, km, km / 90 * 60, durations_s=[km / 90 * 3600 / n] * n)


class KmClassifier:
    """Ville sur les 7 premiers km, autoroute ensuite."""

    def __init__(self, origin):
        self.origin = origin

    def classify(self, points):
        return ["ville" if haversine_km(self.origin, p) < 7 else "autoroute" for p in points]


class BrokenClassifier:
    def classify(self, points):
        raise RoadClassificationError("Overpass indisponible")


def make_trip(classifier, **kw):
    route = straight_route(60)
    return run_trip(
        "A", "B", 20, VehicleParams(52, 90, 10),
        geocode_fn=lambda q: route.coordinates[0], route_fn=lambda a, b: route,
        classifier=classifier, **kw,
    )


def test_run_trip_end_to_end():
    route = straight_route(60)
    rep = make_trip(KmClassifier(route.coordinates[0]))
    types = [s.road_type for s in rep.slices]
    assert types[0] == "ville" and set(types[1:]) == {"autoroute"}
    assert rep.slices[0].length_km == pytest.approx(7, abs=0.6)
    assert all(s.length_km <= 10 + 1e-9 for s in rep.slices)
    assert rep.simulation.total_km == pytest.approx(60, abs=0.01)
    expected = 7 * 12.5 / 100 + 53 * 19.5 / 100
    assert rep.simulation.total_kwh == pytest.approx(expected, rel=0.03)
    assert rep.simulation.final_soc_pct == pytest.approx(
        90 - 100 * rep.simulation.total_kwh / 52)
    assert rep.warnings == []
    # les 3 scénarios partagent les mêmes tranches, seule la consommation change
    sims = rep.simulations
    assert list(sims) == ["optimiste", "moyenne", "pessimiste"] and sims["moyenne"] is rep.simulation
    assert sims["optimiste"].total_kwh < sims["moyenne"].total_kwh < sims["pessimiste"].total_kwh
    assert sims["optimiste"].total_kwh == pytest.approx(7 * 11 / 100 + 53 * 18 / 100, rel=0.03)
    assert sims["pessimiste"].total_kwh == pytest.approx(7 * 14 / 100 + 53 * 21 / 100, rel=0.03)
    assert all(s.final_soc_pct > 0 and s.completed for s in sims.values())


def test_run_trip_falls_back_on_speed_when_osm_is_down():
    rep = make_trip(BrokenClassifier())
    assert rep.warnings and "Overpass" in rep.warnings[0]
    assert {s.road_type for s in rep.slices} == {"autoroute"}  # 90 km/h moyen -> autoroute


def test_run_trip_without_overpass_uses_speed():
    rep = make_trip(None, use_overpass=False)
    assert rep.slices and rep.warnings == []


# --- CLI ----------------------------------------------------------------------------------
def test_cli_prints_report_and_alert(monkeypatch, capsys, tmp_path):
    route = straight_route(60)
    real = cli.run_trip

    def fake(*a, **k):
        return real(*a, **{**k, "geocode_fn": lambda q: route.coordinates[0],
                           "route_fn": lambda x, y: route, "classifier": None, "use_overpass": False})

    monkeypatch.setattr(cli, "run_trip", fake)
    out = tmp_path / "t.csv"
    code = cli.main(["Paris", "Saint-Lô", "--temp", "8", "--battery", "10", "--soc", "90",
                     "--min-soc", "50", "--csv", str(out)])
    text = capsys.readouterr().out
    assert code == 0
    assert "ALERTE (optimiste)" in text and "ALERTE (pessimiste)" in text and "TRAJET INTERROMPU" in text
    assert "Opt. kWh/100" in text and "Pess. charge %" in text and "arrivée avec" not in text
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("#;début km;fin km;type;Opt. kWh/100;Opt. kWh;Opt. charge %;Moy.")
    assert any(l.startswith(";;;Statut;") and "interrompu" in l for l in lines)


def test_cli_rejects_bad_parameters(capsys):
    with pytest.raises(SystemExit):
        cli.main(["A", "B", "--temp", "8", "--battery", "50", "--soc", "150"])
