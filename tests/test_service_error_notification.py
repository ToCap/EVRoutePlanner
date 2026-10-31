"""A service failure is an error notification with a short cause; other errors stay in the footer."""
from dash import no_update

from ev_trip.application.messages import user_message
from interfaces.dash_ui import callbacks, components as ui
from interfaces.dash_ui.params import DEFAULTS


def test_connection_failure_message_has_no_host_port_or_url():
    exc = RuntimeError("Géocodage de « Bois-colombes » impossible : HTTPSConnectionPool(host='nominatim.openstreetmap.org', "
                       "port=443): Max retries exceeded with url: /search?q=Bois-co…")
    assert user_message(exc) == "Géocodage de « Bois-colombes » impossible (connexion au serveur impossible)"
    slow = RuntimeError("Itinéraire impossible : HTTPSConnectionPool(host='x', port=443): Read timed out. (read timeout=30)")
    assert user_message(slow) == "Itinéraire impossible (délai dépassé)"
    assert user_message(RuntimeError("400 Client Error: Bad Request for url: http://x/y")) == "400 Client Error: Bad Request"


def test_error_notification_is_danger_and_repeated_failures_get_new_ids():
    first, second = ui.error_notification("KO"), ui.error_notification("KO")
    assert first[0]["level"] == "danger" and first[0]["text"] == "KO" and first[0]["id"] != second[0]["id"]


def test_service_alert_is_marked_so_that_the_handler_can_move_it_to_the_notifications(monkeypatch):
    class Svc:
        def start(self, request):
            raise callbacks.EVTripError("OSRM HS")

    values = {**DEFAULTS, "origin": "Paris", "destination": "Caen"}
    _, err, *_ = callbacks.compute(Svc(), "compute-btn", values, "", None)
    assert err.id == callbacks.SERVICE_ERROR and "OSRM HS" in err.children and err is not no_update
