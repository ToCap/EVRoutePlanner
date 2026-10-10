"""Simulateur de charge restante pour véhicule électrique (OSM/OSRM)."""
from .config import SCENARIOS, ConsumptionConfig
from .models import VehicleParams
from .simulation import simulate, simulate_all
from .trip import plan_slices, run_trip

__all__ = ["ConsumptionConfig", "SCENARIOS", "VehicleParams", "simulate", "simulate_all", "plan_slices", "run_trip"]
__version__ = "0.2.0"
