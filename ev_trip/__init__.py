"""Remaining-charge simulator for electric vehicles (OSM/OSRM)."""
from ev_trip.domain.config import SCENARIOS, ConsumptionConfig
from ev_trip.domain.models import VehicleParams
from ev_trip.domain.simulation import simulate, simulate_all
from .application.trip import plan_slices, run_trip

__all__ = ["ConsumptionConfig", "SCENARIOS", "VehicleParams", "simulate", "simulate_all", "plan_slices", "run_trip"]
__version__ = "0.2.0"
