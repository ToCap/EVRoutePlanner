"""Corridor queries run a few at a time, each on its own mirror; the result is merged in route order."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from ev_trip.domain.errors import RoadClassificationError
from ev_trip.domain.models import Point, Route
from ev_trip.providers.overpass import CORRIDOR_WORKERS, OverpassClient, find_stations

ROUTE = Route([Point(48.0 + i * 0.009, 2.0) for i in range(301)], 300.0, 200.0)


def _el(i):
    return {"type": "node", "id": i, "lat": 48.0, "lon": 2.0, "tags": {"amenity": "charging_station"}}


class _Client:
    """Records the concurrency; query k answers with station k (a station id seen twice is merged)."""
    pause_s = 0

    def __init__(self, fail_on=()):
        self.lock, self.running, self.peak, self.fail_on, self.seen = threading.Lock(), 0, 0, set(fail_on), []

    def run(self, query):
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
            k = len(self.seen)
            self.seen.append(query)
        time.sleep(0.03)
        with self.lock:
            self.running -= 1
        if k in self.fail_on:
            raise RoadClassificationError("Overpass indisponible : HTTP 504")
        return [_el(k % 2), _el(100 + k)]


def test_queries_run_in_parallel_up_to_the_worker_limit():
    client = _Client()
    found = find_stations(ROUTE, 3.0, client)
    assert 1 < client.peak <= CORRIDOR_WORKERS and len(client.seen) > 3
    assert len(found) == len({s.id for s in found})  # merged by id
    serial = _Client()
    find_stations(ROUTE, 3.0, serial, workers=1)
    assert serial.peak == 1


def test_one_failed_part_keeps_the_others_and_all_failing_is_an_error():
    assert find_stations(ROUTE, 3.0, _Client(fail_on={1}))
    with pytest.raises(RoadClassificationError, match="504"):
        find_stations(ROUTE, 3.0, _Client(fail_on=set(range(100))))


def test_concurrent_queries_start_on_different_mirrors():
    class Http:
        def __init__(self):
            self.urls = []
            self.lock = threading.Lock()

        def post(self, url, **kw):
            with self.lock:
                self.urls.append(url)
            time.sleep(0.02)
            return self

        status_code = 200
        headers: dict = {}

        def raise_for_status(self):
            pass

        def json(self):
            return {"elements": [_el(1)]}

    http = Http()
    client = OverpassClient(http, ["u1", "u2", "u3"], pause_s=0)
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(client.run, ["q"] * 3))
    assert sorted(http.urls) == ["u1", "u2", "u3"]
