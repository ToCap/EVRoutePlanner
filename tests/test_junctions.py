"""Overpass junction search: parsing, retries on overloaded servers, mirrors."""
import pytest
import requests

from ev_trip.domain.errors import RoutingError
from ev_trip.domain.models import Point
from ev_trip.providers.junctions import MIRRORS, overpass_junctions

LINE = [Point(48.0, 2.0), Point(48.1, 2.0)]


class _Resp:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code, self._data, self.headers = status, data or {}, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error")

    def json(self):
        return self._data


class _Session:
    def __init__(self, *responses):
        self.responses, self.urls = list(responses), []

    def post(self, url, data=None, headers=None, timeout=None):
        self.urls.append(url)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


OK = _Resp(200, {"elements": [{"lat": 48.05, "lon": 2.0}, {"id": 1}]})


def test_junctions_are_parsed_and_short_lines_skipped():
    assert overpass_junctions(LINE, _Session(OK)) == [Point(48.05, 2.0)]
    assert overpass_junctions(LINE[:1], _Session()) == []


def test_overloaded_server_is_retried_on_the_next_mirror_after_the_announced_wait():
    waits = []
    session = _Session(_Resp(502), _Resp(429, headers={"Retry-After": "7"}), OK)
    assert overpass_junctions(LINE, session, sleep=waits.append) == [Point(48.05, 2.0)]
    assert session.urls == [MIRRORS[0], MIRRORS[1], MIRRORS[2]] and waits == [2.0, 7.0]


def test_network_error_is_retried_and_exhausted_attempts_raise():
    waits = []
    session = _Session(requests.Timeout("lent"), OK)
    assert len(overpass_junctions(LINE, session, sleep=waits.append)) == 1
    with pytest.raises(RoutingError, match="502"):
        overpass_junctions(LINE, _Session(*[_Resp(502)] * 4), sleep=lambda s: None)


def test_client_error_is_not_retried():
    session = _Session(_Resp(400))
    with pytest.raises(RoutingError):
        overpass_junctions(LINE, session, sleep=lambda s: None)
    assert len(session.urls) == 1
