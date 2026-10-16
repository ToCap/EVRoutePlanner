"""Overpass provider (OpenStreetMap): robust HTTP client, road types, charging stations."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from ev_trip.domain.charging import Station, station_from_element
from ev_trip.domain.config import ChargingConfig
from ev_trip.domain.errors import RoadClassificationError
from ev_trip.domain.geo import cumulative_km, point_at_km
from ev_trip.domain.models import Point, Route
from ev_trip.domain.road_classification import best_way_type

USER_AGENT = os.environ.get("EV_TRIP_USER_AGENT", "ev-trip-simulator/0.1")
# Overpass returns 406 without a User-Agent; mirrors are the fallback; EV_TRIP_OVERPASS_URL forces a server.
OVERPASS_URLS = [
    *([os.environ["EV_TRIP_OVERPASS_URL"]] if os.environ.get("EV_TRIP_OVERPASS_URL") else []),
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
]
OVERPASS_URL = OVERPASS_URLS[0]


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Delay (s) requested by a Retry-After header: integer or HTTP date; None if absent/invalid."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - (now or datetime.now(timezone.utc))).total_seconds())


class OverpassClient:
    """Overpass client: User-Agent, rotating mirrors, Retry-After honored."""

    def __init__(
        self,
        session: requests.Session | None = None,
        url: str | list[str] | None = None,
        pause_s: float = 1.0,
        retries: int = 3,
        timeout: float = 120.0,
        max_retry_after_s: float = 30.0,
    ) -> None:
        self.http = session or requests
        self.urls = [url] if isinstance(url, str) else list(url or OVERPASS_URLS)
        self.headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        self.pause_s = pause_s
        self.retries = retries
        self.timeout = timeout
        self.max_retry_after_s = max_retry_after_s  # cap on the wait imposed by Retry-After

    def run(self, query: str) -> list[dict]:
        """Runs a query and returns the elements; RoadClassificationError if Overpass stays unavailable."""
        last_error: Exception | None = None
        for attempt in range(self.retries):
            retry_after: float | None = None
            try:
                resp = self.http.post(
                    self.urls[attempt % len(self.urls)],  # a different server on each attempt
                    data={"data": query},
                    headers=self.headers,
                    timeout=self.timeout,
                )
                if resp.status_code in (429, 502, 503, 504):
                    retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                    hint = f", Retry-After : {retry_after:g} s" if retry_after is not None else ""
                    raise requests.HTTPError(f"HTTP {resp.status_code}{hint}")
                resp.raise_for_status()
                return resp.json().get("elements", [])
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt >= self.retries - 1:
                    break
                if retry_after is not None:  # the server dictates the delay (capped)
                    time.sleep(min(retry_after, self.max_retry_after_s))
                elif self.pause_s:
                    time.sleep(self.pause_s * (attempt + 1) * 2)
        raise RoadClassificationError(f"Overpass indisponible : {last_error}")


class OverpassRoadClassifier(OverpassClient):
    """Queries Overpass in batches of consecutive route points."""

    def __init__(
        self,
        session: requests.Session | None = None,
        url: str | list[str] | None = None,
        radius_m: float = 30.0,
        chunk_points: int = 40,
        pause_s: float = 1.0,
        retries: int = 3,
        timeout: float = 120.0,
        max_retry_after_s: float = 30.0,
    ) -> None:
        super().__init__(session, url, pause_s, retries, timeout, max_retry_after_s)
        self.radius_m = radius_m
        self.chunk_points = chunk_points

    def classify(self, points: list[Point]) -> list[str | None]:
        out: list[str | None] = []
        for start in range(0, len(points), self.chunk_points):
            chunk = points[start : start + self.chunk_points]
            ways = self.run(self._query(chunk))
            out.extend(best_way_type(p, ways, self.radius_m) for p in chunk)
            if start + self.chunk_points < len(points) and self.pause_s:
                time.sleep(self.pause_s)
        return out

    def _query(self, chunk: list[Point]) -> str:
        # Union of one search per point: `around` with several coordinates would be read as a polyline.
        parts = "".join(
            f'way(around:{self.radius_m:g},{p.lat:.6f},{p.lon:.6f})["highway"];' for p in chunk
        )
        return f"[out:json][timeout:90];({parts});out tags geom;"


# --- charging stations ----------------------------------------------------


def corridor_query(route: Route, radius_m: float, step_km: float = 1.5) -> str:
    """Stations within `radius_m` of the route (polyline subsampled every `step_km`)."""
    cum = cumulative_km(route.coordinates)
    total = cum[-1]
    kms = [i * step_km for i in range(int(total // step_km) + 1)]
    if not kms or total - kms[-1] > 1e-6:
        kms.append(total)
    coords = ",".join(
        f"{p.lat:.5f},{p.lon:.5f}" for p in (point_at_km(route, k, cum) for k in kms)
    )
    around = f"around:{radius_m:g},{coords}"
    return (
        "[out:json][timeout:90];("
        f'node({around})["amenity"="charging_station"];'
        f'way({around})["amenity"="charging_station"];'
        ");out center tags;"
    )


def find_stations(
    route: Route, radius_km: float, client: OverpassClient | None = None, cfg: ChargingConfig | None = None
) -> list[Station]:
    """Usable stations within `radius_km` of the route."""
    client = client or OverpassClient()
    elements = client.run(corridor_query(route, radius_km * 1000.0))
    seen: dict[str, Station] = {}
    for el in elements:
        st = station_from_element(el, cfg)
        if st is not None:
            seen.setdefault(st.id, st)
    return list(seen.values())


class OverpassStationSource:
    """Implements the `StationSource` port with Overpass."""

    def __init__(self, client: OverpassClient | None = None, cfg: ChargingConfig | None = None) -> None:
        self.client, self.cfg = client or OverpassClient(), cfg

    def find(self, route: Route, radius_km: float) -> list[Station]:
        return find_stations(route, radius_km, self.client, self.cfg)
