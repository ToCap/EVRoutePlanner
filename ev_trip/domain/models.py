"""Data structures shared by all modules."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float


@dataclass(frozen=True)
class RouteStep:
    """OSRM step: route portion on a single road (name / ref), in km from the start."""

    start_km: float
    end_km: float
    name: str = ""
    ref: str = ""


@dataclass
class Route:
    """Raw route returned by the routing service."""

    coordinates: list[Point]
    distance_km: float
    duration_min: float
    # Duration (s) of each segment between consecutive coordinates, if known.
    durations_s: list[float] | None = None
    # Steps (road name/ref), if the routing service provides them.
    steps: list[RouteStep] | None = None


@dataclass(frozen=True)
class MicroSegment:
    """Small section (~500 m) of a given road type."""

    start_km: float
    end_km: float
    road_type: str

    @property
    def length_km(self) -> float:
        return self.end_km - self.start_km


@dataclass(frozen=True)
class Slice:
    """Trip slice (<= 10 km, a single road type)."""

    index: int
    start_km: float
    end_km: float
    road_type: str

    @property
    def length_km(self) -> float:
        return self.end_km - self.start_km


@dataclass(frozen=True)
class VehicleParams:
    battery_kwh: float
    start_soc_pct: float
    min_soc_pct: float = 10.0
    arrival_soc_pct: float | None = None  # wanted charge at arrival (>= the minimum), None = only the minimum is required

    def __post_init__(self) -> None:
        if self.battery_kwh <= 0:
            raise ValueError("La capacité de la batterie doit être > 0 kWh.")
        for name, value in (("charge initiale", self.start_soc_pct), ("seuil minimum", self.min_soc_pct)):
            if not 0 <= value <= 100:
                raise ValueError(f"La {name} doit être comprise entre 0 et 100 %.")
        if self.arrival_soc_pct is not None and not self.min_soc_pct <= self.arrival_soc_pct <= 100:
            raise ValueError("La charge à l'arrivée doit être comprise entre le seuil minimum et 100 %.")


@dataclass(frozen=True)
class SliceResult:
    slice: Slice
    consumption_kwh_per_100km: float
    energy_kwh: float
    remaining_kwh: float
    soc_pct: float


@dataclass(frozen=True)
class Alert:
    km: float
    slice_index: int
    message: str


@dataclass
class SimulationResult:
    temp_c: float
    vehicle: VehicleParams
    # Slices actually driven (the last is truncated if the reserve is reached).
    results: list[SliceResult]
    total_km: float  # distance driven
    trip_km: float  # planned trip distance
    total_kwh: float
    final_remaining_kwh: float
    final_soc_pct: float
    completed: bool = True  # False if the trip is interrupted at the reserve
    alert: Alert | None = None
    scenario: str = "moyenne"

    @property
    def short_of_arrival(self) -> bool:
        """True if the trip ends but under the wanted charge at arrival."""
        target = self.vehicle.arrival_soc_pct
        return self.completed and target is not None and self.final_soc_pct < target - 1e-6
