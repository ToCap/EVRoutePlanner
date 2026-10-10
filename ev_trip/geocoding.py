"""Géocodage via Nominatim (OpenStreetMap)."""
from __future__ import annotations

import os
import re

import requests

from .errors import GeocodingError
from .models import Point

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = os.environ.get("EV_TRIP_USER_AGENT", "ev-trip-simulator/0.1")

_COORDS = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[,;]\s*(-?\d+(?:\.\d+)?)\s*$")


def geocode(query: str, session: requests.Session | None = None, timeout: float = 20.0) -> Point:
    """Transforme « Saint-Lô » (ou « 48.85,2.35 ») en coordonnées."""
    m = _COORDS.match(query)
    if m:
        return Point(float(m.group(1)), float(m.group(2)))

    http = session or requests
    try:
        resp = http.get(
            NOMINATIM_URL,
            params={"q": query, "format": "jsonv2", "limit": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        resp.raise_for_status()
        results = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise GeocodingError(f"Géocodage de « {query} » impossible : {exc}") from exc

    if not results:
        raise GeocodingError(f"Adresse introuvable : « {query} »")
    return Point(float(results[0]["lat"]), float(results[0]["lon"]))
