"""Geocoding errors: a typo names the address and the end, an outage gives a short cause and no address."""
import pytest
import requests

from ev_trip.application.trip import locate
from ev_trip.domain.errors import GeocodingNotFound, GeocodingUnavailable
from ev_trip.domain.models import Point
from ev_trip.providers.nominatim import geocode


class _Http:
    def __init__(self, exc=None, results=None):
        self.exc, self.results = exc, results

    def get(self, *a, **k):
        if self.exc:
            raise self.exc
        return self

    def raise_for_status(self):
        pass

    def json(self):
        return self.results


def test_provider_tells_a_typo_from_an_outage():
    with pytest.raises(GeocodingNotFound, match="Adresse introuvable : « Bois-colombs »"):
        geocode("Bois-colombs", _Http(results=[]))
    for exc, cause in ((requests.ConnectionError("HTTPSConnectionPool(host='x')"), "problème de connexion"),
                       (requests.ReadTimeout("slow"), "problème de connexion"), (requests.HTTPError("503"), "service indisponible")):
        with pytest.raises(GeocodingUnavailable) as err:
            geocode("Bois-colombes", _Http(exc=exc))
        assert str(err.value) == cause and "Bois-colombes" not in str(err.value)


def _geo(failing, exc):
    def fn(query):
        if query == failing:
            raise exc
        return Point(48.0, 2.0)
    return fn


def test_locate_names_the_end_and_the_address_for_a_typo():
    with pytest.raises(GeocodingNotFound) as err:
        locate(_geo("Caem", GeocodingNotFound("Adresse introuvable : « Caem »")), "Paris", "Caem")
    assert str(err.value) == "Adresse introuvable : « Caem » (arrivée). Vérifiez l'orthographe."


def test_locate_gives_no_address_for_an_outage():
    with pytest.raises(GeocodingUnavailable) as err:
        locate(_geo("Paris", GeocodingUnavailable("problème de connexion")), "Paris", "Caen")
    assert str(err.value) == "Impossible de localiser le départ : problème de connexion. Réessayez dans un instant."
    assert locate(_geo("x", None), "Paris", "Caen") == (Point(48.0, 2.0), Point(48.0, 2.0))
