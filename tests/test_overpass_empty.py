"""An empty answer is retried on the other mirrors before being read as "no station"."""
from ev_trip.domain.models import Point, Route
from ev_trip.providers.overpass import OverpassClient, find_stations
from test_overpass_remark import _Http

EL = {"type": "node", "id": 1, "lat": 48.0, "lon": 2.0, "tags": {"amenity": "charging_station"}}
ROUTE = Route([Point(48.0, 2.0), Point(48.1, 2.0)], 11.0, 10.0)


def test_empty_answer_is_retried_on_another_mirror():
    http = _Http([{"elements": []}, {"elements": [EL]}])
    found = find_stations(ROUTE, 3.0, OverpassClient(http, ["u1", "u2"], pause_s=0))
    assert len(found) == 1 and len(set(http.urls)) == 2


def test_genuinely_empty_area_stops_after_the_retries():
    http = _Http([{"elements": []}] * 3)
    assert find_stations(ROUTE, 3.0, OverpassClient(http, ["u1", "u2"], pause_s=0)) == [] and len(http.urls) == 3
