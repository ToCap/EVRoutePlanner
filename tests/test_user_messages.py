"""Errors shown to the user: a short cause without URL; the full error goes to the log."""
import logging

from ev_trip.application.charging import plan_charging
from ev_trip.application.jobs import FAILED, MemoryStore, TripRequest, TripService
from ev_trip.application.messages import MAX_LEN, user_message
from ev_trip.domain.charging import Station
from ev_trip.domain.errors import EVTripError, RoutingError
from ev_trip.domain.route_profiles import ProfileReports
from interfaces.dash_ui import callbacks
from interfaces.dash_ui.components import notifications
from interfaces.dash_ui.params import DEFAULTS
from test_station_pool import _report as pool_report
from test_profiles import _report

URL_ERROR = "Calcul d'itinéraire impossible : 400 Client Error: Bad Request for url: https://router.project-osrm.org/route/v1/driving/-1.1,48.9;-1.2,48.9?overview=full&exclude=motorway"


def test_user_message_drops_the_url_and_caps_the_length():
    assert user_message(RoutingError(URL_ERROR)) == "Calcul d'itinéraire impossible : 400 Client Error: Bad Request"
    assert user_message(RuntimeError("  a\n b  ")) == "a b" and user_message(RuntimeError("")) == "RuntimeError"
    assert len(user_message(RuntimeError("x" * 500))) == MAX_LEN


def test_failed_station_search_shows_a_short_cause_and_keeps_the_details_in_the_log(caplog):
    def plan(*a, **k):
        raise RuntimeError("Overpass KO for url: https://overpass-api.de/api/interpreter")

    svc = TripService(MemoryStore(), runner=lambda job: job(), run_profiles=lambda *a, **k: ProfileReports(_report(200), _report(200)),
                      refine=lambda r: r, plan=plan)
    with caplog.at_level(logging.INFO):
        key, _ = svc.start(TripRequest("Paris", "Caen", 15.0, 52.0, 90.0, 10.0, False, 5.0))
    entry = svc.get(key)
    assert entry.state == FAILED and entry.error == "Overpass KO"
    assert any("overpass-api.de" in r.getMessage() or "overpass-api.de" in str(r.exc_info) for r in caplog.records)  # full detail logged
    text = next(n["text"] for n in notifications(entry, key) if n["level"] == "danger")
    assert text == "Recherche des bornes impossible (Overpass KO) : résultat partiel conservé." and "http" not in text


def test_compute_error_is_friendly_and_logged_in_full(caplog):
    class Failing:
        def start(self, request):
            raise RoutingError(URL_ERROR)

    values = {**DEFAULTS, "origin": "Paris", "destination": "Caen"}
    with caplog.at_level(logging.WARNING):
        _, error, *_ = callbacks.compute(Failing(), "compute-btn", values, "", None)
    assert error.children == "Calcul d'itinéraire impossible : 400 Client Error: Bad Request"
    assert any("router.project-osrm.org" in r.getMessage() for r in caplog.records)


def test_station_warnings_have_no_url(caplog):
    class Failing:
        def find(self, route, radius_km):
            raise EVTripError("Overpass KO for url: https://overpass-api.de/api/interpreter")

    plan = plan_charging(pool_report(2.0), 5.0, stations=Failing())
    assert plan.warnings == ["Recherche des bornes impossible (Overpass KO)."]
    plan = plan_charging(pool_report(2.0), 5.0, pool=[Station("near", 48.45, 2.0003)],
                         detour_fn=lambda *a: (_ for _ in ()).throw(RoutingError(URL_ERROR)), sleep=lambda s: None)
    assert plan.warnings == ["Détours estimés à vol d'oiseau (OSRM indisponible : Calcul d'itinéraire impossible : 400 Client Error: Bad Request)."]
