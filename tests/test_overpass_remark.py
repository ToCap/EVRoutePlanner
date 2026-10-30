"""Overpass answers HTTP 200 with a runtime-error remark and no elements: retried, never read as "no station"."""
import pytest

from ev_trip.domain.errors import RoadClassificationError
from ev_trip.providers.overpass import OverpassClient


class _Resp:
    status_code = 200
    headers: dict = {}

    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class _Http:
    def __init__(self, answers):
        self.answers, self.urls = list(answers), []

    def post(self, url, **kw):
        self.urls.append(url)
        return _Resp(self.answers.pop(0))


BAD = {"elements": [], "remark": "runtime error: Query timed out in \"query\" at line 1"}


def test_runtime_error_remark_is_retried_on_another_mirror():
    http = _Http([BAD, {"elements": [{"id": 1}]}])
    client = OverpassClient(http, ["u1", "u2"], pause_s=0)
    assert client.run("q") == [{"id": 1}] and http.urls == ["u1", "u2"]


def test_persistent_runtime_error_is_an_error_not_an_empty_result():
    client = OverpassClient(_Http([BAD, BAD, BAD]), ["u1", "u2"], pause_s=0)
    with pytest.raises(RoadClassificationError, match="interrompue"):
        client.run("q")
