"""Computed trip result (data structure shared by the application and the interfaces)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ev_trip.domain.config import DEFAULT_SCENARIO, ConsumptionConfig
from ev_trip.domain.models import Route, SimulationResult, Slice
from ev_trip.domain.sampling import CoarsePlan


@dataclass
class TripReport:
    origin: str
    destination: str
    route: Route
    slices: list[Slice]
    simulations: dict[str, SimulationResult]  # key: optimiste / moyenne / pessimiste
    warnings: list[str] = field(default_factory=list)
    config: ConsumptionConfig = field(default_factory=ConsumptionConfig)
    # "coarse": first estimate (1 Overpass point per section), "fine": final result
    precision: str = "fine"
    coarse: CoarsePlan | None = None  # pass-1 data, used by the refinement

    @property
    def simulation(self) -> SimulationResult:
        """Mean scenario ("moyenne"), the reference."""
        return self.simulations[DEFAULT_SCENARIO]

    @property
    def refinable(self) -> bool:
        """True if a refinement (refine_trip) can still improve the result."""
        return self.precision == "coarse"
