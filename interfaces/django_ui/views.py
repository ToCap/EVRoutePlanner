"""Django views: HTTP <-> `TripService` translation, no business logic; the page polls `status` and reloads when done."""
from __future__ import annotations

import logging
import os

import requests
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render

from ev_trip.application.jobs import REFINING, TripRequest, TripService
from ev_trip.domain.charging import TariffTable
from ev_trip.domain.errors import EVTripError
from ev_trip.providers.files import load_tariffs

from interfaces.django_ui.cache_store import DjangoCacheStore
from interfaces.django_ui.forms import TripForm
from ev_trip.presentation import build_charging_data, build_view_data

log = logging.getLogger(__name__)


def _tariffs() -> TariffTable | None:
    """Per-operator tariffs (JSON file), reloaded per task; missing or invalid = no tariffs."""
    path = getattr(settings, "TARIFFS_FILE", None)
    if path and os.path.exists(path):
        try:
            return load_tariffs(path)
        except (OSError, ValueError) as exc:
            log.warning("Tarifs ignorés (%s) : %s", path, exc)
    return None


service = TripService(DjangoCacheStore(), tariffs=_tariffs)


def index(request):
    """GET form: a result's URL is shareable."""
    form = TripForm(request.GET or None)
    context = {"form": form, "report": None, "error": None, "refine": None}

    if form.is_bound and form.is_valid():
        cd = {**form.cleaned_data, "max_detour_km": form.cleaned_data.get("max_detour_km") or 0.0}
        key, entry = None, None
        try:
            key, entry = service.start(TripRequest(**cd))
        except ValueError as exc:
            form.add_error(None, str(exc))
        except (EVTripError, requests.RequestException) as exc:
            context["error"] = str(exc)

        if entry is not None:
            report = entry.report
            data = build_view_data(report)
            if entry.charging is not None:
                data["charging"] = build_charging_data(entry.charging)
            data["tile_url"] = settings.TILE_URL
            data["tile_attribution"] = settings.TILE_ATTRIBUTION
            context.update(
                report=report,
                sim=report.simulation,
                data=data,
                refine={
                    "state": entry.state, "error": entry.error, "refined": entry.refined, "key": key,
                    "refinable": report.refinable, "charging_requested": entry.max_detour_km > 0,
                },
                charging=entry.charging,
            )

    return render(request, "django_ui/index.html", context)


def status(request, key):
    """Background task state (polled by the page): refining / done / failed, or 404 if the result expired."""
    entry = service.get(key)
    if entry is None:
        return JsonResponse({"state": "unknown"}, status=404)
    return JsonResponse({"state": entry.state, "error": entry.error})
