"""Modèle de consommation : type de route x scénario x température."""
from __future__ import annotations

from .config import DEFAULT_SCENARIO, ConsumptionConfig


def consumption_kwh_per_100km(
    road_type: str, temp_c: float, cfg: ConsumptionConfig, scenario: str = DEFAULT_SCENARIO
) -> float:
    base = cfg.base_kwh_per_100km(road_type, scenario)
    return base * (1.0 + cfg.temperature_surcharge(temp_c))


def slice_energy_kwh(
    length_km: float, road_type: str, temp_c: float, cfg: ConsumptionConfig, scenario: str = DEFAULT_SCENARIO
) -> float:
    return length_km * consumption_kwh_per_100km(road_type, temp_c, cfg, scenario) / 100.0
