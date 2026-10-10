import requests
from django.conf import settings
from django.shortcuts import render

from ev_trip.errors import EVTripError
from ev_trip.models import VehicleParams
from ev_trip.trip import run_trip

from .forms import TripForm
from .services import build_view_data


def index(request):
    """Formulaire en GET : l'URL d'un résultat est partageable."""
    form = TripForm(request.GET or None)
    context = {"form": form, "report": None, "error": None}

    if form.is_bound and form.is_valid():
        cd = form.cleaned_data
        try:
            vehicle = VehicleParams(cd["battery_kwh"], cd["start_soc"], cd["min_soc"])
            report = run_trip(
                cd["origin"], cd["destination"], cd["temp_c"], vehicle, use_overpass=cd["use_overpass"]
            )
        except ValueError as exc:
            form.add_error(None, str(exc))
        except (EVTripError, requests.RequestException) as exc:
            context["error"] = str(exc)
        else:
            data = build_view_data(report)
            data["tile_url"] = settings.TILE_URL
            data["tile_attribution"] = settings.TILE_ATTRIBUTION
            context.update(report=report, sim=report.simulation, data=data)

    return render(request, "trips/index.html", context)
