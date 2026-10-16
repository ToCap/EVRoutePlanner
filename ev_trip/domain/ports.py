"""Ports: what the domain and application need from outside (implemented in `providers`)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Protocol

from ev_trip.domain.models import Point, Route

if TYPE_CHECKING:  # avoids a cycle (charging -> trip_report -> sampling -> ports)
    from ev_trip.domain.charging import Station

GeocodeFn = Callable[[str], Point]  # address -> coordinates
RouteFn = Callable[[Point, Point], Route]  # start, end -> route
PathFn = Callable[[list[Point]], tuple[float, float]]  # waypoints -> (km, min)


class RoadClassifier(Protocol):
    """Road type (ville / route / autoroute, None if unknown) of each point."""

    def classify(self, points: list[Point]) -> list[str | None]: ...


class StationSource(Protocol):
    """Charging stations within `radius_km` of the route."""

    def find(self, route: Route, radius_km: float) -> list[Station]: ...
