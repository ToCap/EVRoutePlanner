"""Structures de données partagées par tous les modules."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float


@dataclass
class Route:
    """Itinéraire brut renvoyé par le service de routage."""

    coordinates: list[Point]
    distance_km: float
    duration_min: float
    # Durée (s) de chaque segment entre deux coordonnées consécutives, si connue.
    durations_s: list[float] | None = None


@dataclass(frozen=True)
class MicroSegment:
    """Petit tronçon (~500 m) d'un type de route donné."""

    start_km: float
    end_km: float
    road_type: str

    @property
    def length_km(self) -> float:
        return self.end_km - self.start_km


@dataclass(frozen=True)
class Slice:
    """Tranche de trajet (<= 10 km, un seul type de route)."""

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

    def __post_init__(self) -> None:
        if self.battery_kwh <= 0:
            raise ValueError("La capacité de la batterie doit être > 0 kWh.")
        for name, value in (("charge initiale", self.start_soc_pct), ("seuil minimum", self.min_soc_pct)):
            if not 0 <= value <= 100:
                raise ValueError(f"La {name} doit être comprise entre 0 et 100 %.")


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
    # Tranches réellement parcourues (la dernière est tronquée si la réserve est atteinte).
    results: list[SliceResult]
    total_km: float  # distance parcourue
    trip_km: float  # distance du trajet prévu
    total_kwh: float
    final_remaining_kwh: float
    final_soc_pct: float
    completed: bool = True  # False si le trajet est interrompu à la réserve
    alert: Alert | None = None
    scenario: str = "moyenne"
