"""A failed part of the corridor search keeps the stations of the other parts; all parts failing is an error."""
import pytest

from ev_trip.domain.errors import RoadClassificationError
from ev_trip.domain.models import Point, Route
from ev_trip.providers.overpass import find_stations

EL = {"type": "node", "id": 1, "lat": 48.0, "lon": 2.0, "tags": {"amenity": "charging_station"}}
ROUTE = Route([Point(48.0 + i * 0.009, 2.0) for i in range(301)], 300.0, 200.0)


class _Client:
    pause_s = 0

    def __init__(self, fail):
        self.fail, self.n = fail, 0

    def run(self, query):
        self.n += 1
        if self.fail(self.n):
            raise RoadClassificationError("Overpass indisponible : HTTP 504")
        return [EL]


def test_one_failed_part_keeps_the_other_stations():
    assert len(find_stations(ROUTE, 3.0, _Client(lambda n: n == 2))) == 1


def test_every_part_failing_is_an_error():
    with pytest.raises(RoadClassificationError, match="504"):
        find_stations(ROUTE, 3.0, _Client(lambda n: True))
