"""Paramètres du modèle de consommation (modifiables via un fichier JSON)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

ROAD_TYPES = ("ville", "route", "autoroute")
# Optimiste = estimation basse, pessimiste = estimation haute, moyenne = milieu des deux.
SCENARIOS = ("optimiste", "moyenne", "pessimiste")
DEFAULT_SCENARIO = "moyenne"


def _default_brackets() -> list[tuple[float, float]]:
    # (borne supérieure exclue en °C, surconsommation)
    return [(-5.0, 0.35), (0.0, 0.25), (10.0, 0.15), (15.0, 0.08), (25.0, 0.0)]


@dataclass
class ConsumptionConfig:
    # Fourchette (basse, haute) en kWh / 100 km à 20 °C, par type de route.
    consumption_range_kwh_per_100km: dict[str, tuple[float, float]] = field(
        default_factory=lambda: {"ville": (11.0, 14.0), "route": (14.0, 15.6), "autoroute": (18.0, 21.0)}
    )
    # Paliers de température : t < borne -> surconsommation associée.
    temperature_brackets: list[tuple[float, float]] = field(default_factory=_default_brackets)
    # Surconsommation au-delà du dernier palier (climatisation).
    temperature_above_last: float = 0.05

    # Découpage
    max_slice_km: float = 10.0
    sample_step_km: float = 0.5
    # Un changement de type plus court que ça, entouré du même type, est absorbé
    # (bruit d'appariement OSM). 0 pour désactiver.
    min_run_km: float = 1.0

    # Repli si OSM est indisponible : classification d'après la vitesse moyenne OSRM.
    fallback_urban_max_kmh: float = 45.0
    fallback_motorway_min_kmh: float = 88.0

    def __post_init__(self) -> None:
        missing = [t for t in ROAD_TYPES if t not in self.consumption_range_kwh_per_100km]
        if missing:
            raise ValueError(f"Consommation manquante pour : {', '.join(missing)}")
        ranges = {}
        for road_type in ROAD_TYPES:
            low, high = (float(v) for v in self.consumption_range_kwh_per_100km[road_type])
            if not 0 < low <= high:
                raise ValueError(f"Fourchette invalide pour « {road_type} » : il faut 0 < basse <= haute.")
            ranges[road_type] = (low, high)
        self.consumption_range_kwh_per_100km = ranges
        self.temperature_brackets = sorted((float(b), float(s)) for b, s in self.temperature_brackets)

    def base_kwh_per_100km(self, road_type: str, scenario: str = DEFAULT_SCENARIO) -> float:
        """Consommation à 20 °C pour un type de route et un scénario."""
        low, high = self.consumption_range_kwh_per_100km[road_type]
        if scenario == "optimiste":
            return low
        if scenario == "pessimiste":
            return high
        if scenario == "moyenne":
            return (low + high) / 2.0
        raise ValueError(f"Scénario inconnu : {scenario!r} (attendu : {', '.join(SCENARIOS)})")

    def temperature_surcharge(self, temp_c: float) -> float:
        for upper, surcharge in self.temperature_brackets:
            if temp_c < upper:
                return surcharge
        return self.temperature_above_last

    @classmethod
    def from_json(cls, path: str | Path) -> "ConsumptionConfig":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        defaults = cls()
        kwargs = {}
        for key in defaults.__dataclass_fields__:
            if key in data:
                kwargs[key] = data[key]
        if "consumption_range_kwh_per_100km" in kwargs:
            kwargs["consumption_range_kwh_per_100km"] = {
                **defaults.consumption_range_kwh_per_100km,
                **kwargs["consumption_range_kwh_per_100km"],
            }
        return cls(**kwargs)
