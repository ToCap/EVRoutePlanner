"""Consumption model parameters (editable via a JSON file)."""
from __future__ import annotations

from dataclasses import dataclass, field

ROAD_TYPES = ("ville", "route", "autoroute")
# Optimistic = low estimate, pessimistic = high estimate, mean = midpoint.
SCENARIOS = ("optimiste", "moyenne", "pessimiste")
DEFAULT_SCENARIO = "moyenne"


def _default_brackets() -> list[tuple[float, float]]:
    # (exclusive upper bound in °C, overconsumption)
    return [(-5.0, 0.35), (0.0, 0.25), (10.0, 0.15), (15.0, 0.08), (25.0, 0.0)]


@dataclass(frozen=True)
class ChargerLevel:
    """Charging level a vehicle can accept (AC or DC) with the kind of outlet it corresponds to."""

    id: str
    label: str
    kw: float
    current: str  # "AC" | "DC"
    description: str


# Highest power first; a DC level is a vehicle limit (a more powerful station still works, capped at `kw`).
CHARGER_LEVELS = (
    ChargerLevel("dc100", "100 kW DC (350 A)", 100.0, "DC", "borne de charge rapide"),
    ChargerLevel("ac22", "22 kW AC (3x32 A)", 22.0, "AC", "borne de recharge domestique"),
    ChargerLevel("ac11", "11 kW AC (3x16 A)", 11.0, "AC", "borne de recharge domestique"),
    ChargerLevel("ac7_4", "7,4 kW AC (32 A)", 7.4, "AC", "borne de recharge domestique"),
    ChargerLevel("ac3_7", "3,7 kW AC (16 A)", 3.7, "AC", "prise domestique renforcée"),
    ChargerLevel("ac2_3", "2,3 kW AC (10 A)", 2.3, "AC", "prise domestique"),
)
DEFAULT_CHARGERS = tuple(level.id for level in CHARGER_LEVELS)


@dataclass
class ChargingConfig:
    """Station search and scoring parameters."""

    # Weights of the 4 score criteria (normalized: only the ratio matters).
    weight_detour: float = 0.30
    weight_need: float = 0.35
    weight_charge: float = 0.20
    weight_price: float = 0.15

    # Search
    max_candidates: int = 25  # stations whose real detour is computed (OSRM)
    max_results: int = 15  # stations displayed
    detour_anchor_km: float = 15.0  # detour is measured between two route points at ± this km
    osrm_pause_s: float = 1.0  # pause between two OSRM requests (demo server)
    corridor_factor: float = 0.6  # Overpass search radius = max detour x this factor (km)
    road_factor: float = 1.3  # estimated detour = 2 x crow-flies distance x this factor

    # Power and charge time
    fast_kw: float = 50.0  # from this power: fast charge
    assumed_fast_kw: float = 50.0  # assumed power of a fast station with no data
    assumed_ac_kw: float = 11.0  # assumed power of a normal station with no data
    vehicle_max_ac_kw: float = 11.0
    vehicle_max_dc_kw: float = 100.0
    charge_efficiency: float = 0.85
    charge_time_ref_min: float = 90.0  # charge time giving score 0

    # Recharge need
    arrival_margin_pct: float = 5.0  # desired margin above the reserve on arrival
    target_max_soc_pct: float = 80.0  # never charge beyond this
    nominal_top_up_pct: float = 20.0  # typical recharge when not required
    need_window_pct: float = 25.0  # arrival at station within x % above the reserve: max score
    need_floor_score: float = 30.0
    pessimistic_unreachable_factor: float = 0.75

    # Vehicle charge profile timed by the user (set per trip by `apply_charge_profile`, not a JSON setting)
    charge_window_pct: tuple[float, float] | None = None  # (from %, to %) the timings refer to
    level_kw: dict[str, float] = field(default_factory=dict)  # level id -> battery-side kW measured over the window

    # Price
    price_max_eur_kwh: float = 0.80  # price giving score 0
    neutral_score: float = 50.0  # score of an unknown criterion

    def __post_init__(self) -> None:
        weights = (self.weight_detour, self.weight_need, self.weight_charge, self.weight_price)
        if any(w < 0 for w in weights) or sum(weights) <= 0:
            raise ValueError("Les poids de la note doivent être >= 0, avec une somme > 0.")
        positive = (
            "detour_anchor_km", "corridor_factor", "road_factor", "fast_kw", "assumed_fast_kw",
            "assumed_ac_kw", "vehicle_max_ac_kw", "vehicle_max_dc_kw", "charge_efficiency",
            "charge_time_ref_min", "price_max_eur_kwh", "need_window_pct", "nominal_top_up_pct",
        )
        for name in positive:
            if getattr(self, name) <= 0:
                raise ValueError(f"charging.{name} doit être > 0.")
        if self.max_candidates < 1 or self.max_results < 1:
            raise ValueError("charging.max_candidates et charging.max_results doivent être >= 1.")
        if not 0 < self.charge_efficiency <= 1:
            raise ValueError("charging.charge_efficiency doit être dans ]0 ; 1].")
        if not 0 < self.target_max_soc_pct <= 100:
            raise ValueError("charging.target_max_soc_pct doit être dans ]0 ; 100].")
        if not 0 <= self.pessimistic_unreachable_factor <= 1:
            raise ValueError("charging.pessimistic_unreachable_factor doit être dans [0 ; 1].")
        for name in ("need_floor_score", "neutral_score"):
            if not 0 <= getattr(self, name) <= 100:
                raise ValueError(f"charging.{name} doit être dans [0 ; 100].")
        if self.charge_window_pct is not None:
            low, high = self.charge_window_pct
            if not 0 <= low < high <= 100:
                raise ValueError("charging.charge_window_pct doit vérifier 0 <= début < fin <= 100.")
        if any(kw <= 0 for kw in self.level_kw.values()):
            raise ValueError("charging.level_kw doit contenir des puissances > 0.")

    @classmethod
    def from_dict(cls, data: dict) -> "ChargingConfig":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class ConsumptionConfig:
    # Range (low, high) in kWh/100 km at 20 °C, per road type.
    consumption_range_kwh_per_100km: dict[str, tuple[float, float]] = field(
        default_factory=lambda: {"ville": (11.0, 14.0), "route": (14.0, 15.6), "autoroute": (18.0, 21.0)}
    )
    # Temperature brackets: t < bound -> associated overconsumption.
    temperature_brackets: list[tuple[float, float]] = field(default_factory=_default_brackets)
    # Overconsumption beyond the last bracket (air conditioning).
    temperature_above_last: float = 0.05

    # Slicing
    max_slice_km: float = 10.0
    sample_step_km: float = 0.5
    # A type change shorter than this, surrounded by the same type, is absorbed (OSM matching noise); 0 disables.
    min_run_km: float = 1.0

    # Per-section sampling (OSRM steps): pass 1 = 1 Overpass point per section, pass 2 = 1 point every `refine_step_km` off motorways.
    refine_step_km: float = 1.0
    # Absorption threshold for isolated type changes in this sampling (less noisy).
    min_run_steps_km: float = 0.5

    # Fallback if OSM is unavailable: classification from OSRM average speed.
    fallback_urban_max_kmh: float = 45.0
    fallback_motorway_min_kmh: float = 88.0

    # Consumption route profile: accepted duration gap (%) over the fastest route
    eco_max_extra_duration_pct: float = 15.0
    # Consumption route profile: motorway exits tried before the fast route's own exit (0 = OSRM alternatives only, max 2)
    eco_exit_variants: int = 2

    # Scenario that estimates a trip (optimiste / moyenne / pessimiste): consumption, charge need, route ranking
    scenario: str = DEFAULT_SCENARIO

    # Charging stations (search, scoring)
    charging: ChargingConfig = field(default_factory=ChargingConfig)

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
        if self.scenario not in SCENARIOS:
            raise ValueError(f"Scénario inconnu : {self.scenario!r} (attendu : {', '.join(SCENARIOS)})")
        if not 0 <= self.eco_exit_variants <= 2:
            raise ValueError("eco_exit_variants doit être compris entre 0 et 2.")
        if self.eco_max_extra_duration_pct < 0:
            raise ValueError("eco_max_extra_duration_pct doit être >= 0.")
        if self.refine_step_km <= 0:
            raise ValueError("refine_step_km doit être > 0.")
        if isinstance(self.charging, dict):
            self.charging = ChargingConfig.from_dict(self.charging)
        self.temperature_brackets = sorted((float(b), float(s)) for b, s in self.temperature_brackets)

    def base_kwh_per_100km(self, road_type: str, scenario: str = DEFAULT_SCENARIO) -> float:
        """Consumption at 20 °C for a road type and scenario."""
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
    def from_dict(cls, data: dict) -> "ConsumptionConfig":
        """Builds the configuration from a dict (JSON reading: `providers.files.load_config`)."""
        defaults = cls()
        kwargs = {key: data[key] for key in defaults.__dataclass_fields__ if key in data}
        if "consumption_range_kwh_per_100km" in kwargs:
            kwargs["consumption_range_kwh_per_100km"] = {
                **defaults.consumption_range_kwh_per_100km,
                **kwargs["consumption_range_kwh_per_100km"],
            }
        return cls(**kwargs)
