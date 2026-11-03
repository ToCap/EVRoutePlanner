"""Overpass client: failing mirror skipped for a while, read timeout follows the query limit."""
import requests

from ev_trip.providers.overpass import OverpassClient


class _Session:
    def __init__(self, bad):
        self.bad, self.calls = bad, []

    def post(self, url, data, headers, timeout):
        self.calls.append((url, timeout))
        resp = requests.Response()
        resp.status_code = 504 if url in self.bad else 200
        resp._content = b'{"elements": [{"id": 1}]}'
        return resp


def test_failing_mirror_is_skipped_by_later_queries():
    s = _Session({"b"})
    c = OverpassClient(s, ["a", "b", "c"], pause_s=0)
    c.run("[out:json][timeout:30];x")
    s.calls.clear()
    for _ in range(4):
        c.run("[out:json][timeout:30];x")
    assert "b" not in [u for u, _ in s.calls]


def test_read_timeout_follows_the_query_limit():
    s = _Session(set())
    c = OverpassClient(s, ["a"], pause_s=0)
    c.run("[out:json][timeout:30];x")
    c.run("no limit")
    assert [t for _, t in s.calls] == [40.0, 120.0]
