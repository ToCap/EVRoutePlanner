"""Remaining-charge simulation, slice by slice; the reserve is never dipped into (the slice is truncated there)."""
from __future__ import annotations

from ev_trip.domain.config import DEFAULT_SCENARIO, SCENARIOS, ConsumptionConfig
from ev_trip.domain.consumption import consumption_kwh_per_100km
from ev_trip.domain.models import Alert, SimulationResult, Slice, SliceResult, VehicleParams

_EPS = 1e-9


def _stop_alert(vehicle: VehicleParams, km: float, index: int, cannot_start: bool) -> Alert:
    if cannot_start:
        msg = (
            f"Charge de départ ({vehicle.start_soc_pct:g} %) inférieure au seuil de réserve "
            f"({vehicle.min_soc_pct:g} %) : impossible de démarrer."
        )
    elif vehicle.min_soc_pct <= 0:
        msg = f"Batterie vide au km {km:.1f} : impossible de poursuivre (tranche {index})."
    else:
        msg = (
            f"Seuil de réserve de {vehicle.min_soc_pct:g} % atteint au km {km:.1f} : "
            f"impossible de poursuivre (tranche {index})."
        )
    return Alert(km, index, msg)


def simulate(
    slices: list[Slice],
    vehicle: VehicleParams,
    temp_c: float,
    cfg: ConsumptionConfig | None = None,
    scenario: str = DEFAULT_SCENARIO,
) -> SimulationResult:
    cfg = cfg or ConsumptionConfig()
    remaining = vehicle.battery_kwh * vehicle.start_soc_pct / 100.0
    min_kwh = vehicle.battery_kwh * vehicle.min_soc_pct / 100.0

    alert: Alert | None = None
    results: list[SliceResult] = []
    total_kwh = 0.0

    for sl in slices:
        cons = consumption_kwh_per_100km(sl.road_type, temp_c, cfg, scenario)
        energy = sl.length_km * cons / 100.0
        after = remaining - energy

        if after < min_kwh - _EPS:
            usable = max(remaining - min_kwh, 0.0)  # energy available before dipping into the reserve
            stop_km = sl.start_km
            if usable > _EPS and energy > 0:
                stop_km = sl.start_km + sl.length_km * usable / energy
                remaining -= usable
                total_kwh += usable
                results.append(
                    SliceResult(
                        Slice(sl.index, sl.start_km, stop_km, sl.road_type),
                        cons, usable, remaining, 100.0 * remaining / vehicle.battery_kwh,
                    )
                )
            cannot_start = not results and usable <= _EPS and remaining < min_kwh - _EPS
            alert = _stop_alert(vehicle, stop_km, sl.index, cannot_start)
            break

        remaining = after
        total_kwh += energy
        results.append(SliceResult(sl, cons, energy, remaining, 100.0 * remaining / vehicle.battery_kwh))

    return SimulationResult(
        temp_c=temp_c,
        vehicle=vehicle,
        results=results,
        total_km=results[-1].slice.end_km if results else 0.0,
        trip_km=slices[-1].end_km if slices else 0.0,
        total_kwh=total_kwh,
        final_remaining_kwh=remaining,
        final_soc_pct=100.0 * remaining / vehicle.battery_kwh,
        completed=alert is None,
        alert=alert,
        scenario=scenario,
    )


def simulate_all(
    slices: list[Slice],
    vehicle: VehicleParams,
    temp_c: float,
    cfg: ConsumptionConfig | None = None,
) -> dict[str, SimulationResult]:
    """One simulation per scenario (optimistic, mean, pessimistic) over the same slices."""
    cfg = cfg or ConsumptionConfig()
    return {name: simulate(slices, vehicle, temp_c, cfg, name) for name in SCENARIOS}
