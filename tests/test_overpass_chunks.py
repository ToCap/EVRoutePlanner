"""Long routes: the station search is split in several short queries, merged by station id."""
from ev_trip.domain.models import Point, Route
from ev_trip.providers.overpass import corridor_queries, find_stations


def _route(km):
    return Route([Point(48.0 + i * 0.009, 2.0) for i in range(int(km / 1.0) + 1)], float(km), float(km))


class _Client:
    pause_s = 0

    def __init__(self):
        self.queries = []

    def run(self, query):
        self.queries.append(query)
        return [{"type": "node", "id": 1, "lat": 48.0, "lon": 2.0, "tags": {"amenity": "charging_station"}}]


def test_long_route_is_split_and_chunks_overlap_by_one_point():
    queries = corridor_queries(_route(300), 3000.0)
    assert len(queries) > 3 and all(q.count(",") < 200 for q in queries)
    assert len(corridor_queries(_route(30), 3000.0)) == 1


def test_stations_of_all_chunks_are_merged_without_duplicates():
    client = _Client()
    found = find_stations(_route(300), 3.0, client)
    assert len(client.queries) > 3 and len(found) == 1
