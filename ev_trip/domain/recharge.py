"""Multi-stop recharge plan: portion/station couples, charge time up to the next stop or the arrival (pure domain)."""
from __future__ import annotations

from dataclasses import dataclass

from ev_trip.domain.charging import (
    StationSuggestion, TariffTable, _charge_time_min, _clamp, arrival_floor_pct, energy_between, resolve_price,
)
from ev_trip.domain.trip_report import TripReport

_EPS = 1e-9
MAX_STOPS = 6


@dataclass
class ChargeStop:
    suggestion: StationSuggestion
    soc_arrival_pct: float  # charge on arrival at the station (mean)
    soc_after_pct: float  # charge once recharged
    add_kwh: float
    charge_min: float | None
    portion_score: float  # 0-100, from the charge at the end of the portion
    station_score: float  # 0-100, detour / charge duration / price
    score: float  # couple score = portion score x station score / 100
    cost_eur: float | None


def portion_score(soc_pct: float, min_pct: float, high_pct: float) -> float:
    """0 at or above the high end of the charge window, 100 at the minimum threshold, linear between."""
    if high_pct <= min_pct:
        return 0.0
    return _clamp(100.0 * (high_pct - soc_pct) / (high_pct - min_pct))


def _station_score(sug: StationSuggestion, charge_min: float | None, max_detour_km: float, report: TripReport,
                   tariffs: TariffTable | None) -> float:
    """Station score without the need criterion: detour, charge duration, price."""
    cc = report.config.charging
    s_detour = _clamp(100.0 * (1.0 - sug.detour_km / max_detour_km)) if max_detour_km > 0 else 100.0
    s_charge = cc.neutral_score if charge_min is None else _clamp(100.0 * (1.0 - charge_min / cc.charge_time_ref_min))
    price, _ = resolve_price(sug.station, tariffs)
    s_price = cc.neutral_score if price is None else _clamp(100.0 * (1.0 - price / cc.price_max_eur_kwh))
    total = cc.weight_detour + cc.weight_charge + cc.weight_price
    return (cc.weight_detour * s_detour + cc.weight_charge * s_charge + cc.weight_price * s_price) / total


def plan_stops(report: TripReport, suggestions: list[StationSuggestion], max_detour_km: float,
               tariffs: TariffTable | None = None) -> tuple[list[ChargeStop], list[str]]:
    """Chained stops until the arrival; (stops, warnings). Empty when no recharge is useful."""
    cc = report.config.charging
    moy, pess = report.simulation, report.simulations["pessimiste"]
    veh = moy.vehicle
    cap, temp, cfg, slices = veh.battery_kwh, moy.temp_c, report.config, report.slices
    total_km = moy.trip_km
    floor = arrival_floor_pct(veh, cc.arrival_margin_pct)
    reserve = veh.min_soc_pct + cc.arrival_margin_pct
    high = cc.target_max_soc_pct

    def soc_after_leg(soc: float, a: float, b: float, scenario: str) -> float:
        return soc - 100.0 * energy_between(slices, cfg, temp, scenario, a, b) / cap

    def finishes(pos: float, soc: float) -> bool:
        return (soc_after_leg(soc, pos, total_km, "moyenne") >= floor - 1e-6
                and soc_after_leg(soc, pos, total_km, "pessimiste") >= floor - 1e-6)

    def best_couple(pos: float, soc: float):
        """(couple score, portion score, station score, suggestion, arrival charge) of the best reachable couple."""
        best = None
        for sug in suggestions:
            if sug.route_km <= pos + 0.5:
                continue
            half = sug.detour_kwh / 2.0
            arr = soc_after_leg(soc, pos, sug.route_km, "moyenne") - 100.0 * half / cap
            arr_p = soc_after_leg(soc, pos, sug.route_km, "pessimiste") - 100.0 * half / cap
            if arr_p < reserve - 1e-6 or arr < reserve - 1e-6:
                continue
            sl = next((s for s in slices if s.start_km < sug.route_km <= s.end_km + _EPS), None)
            end = sl.end_km if sl else sug.route_km
            p_score = portion_score(soc_after_leg(soc, pos, end, "moyenne"), veh.min_soc_pct, high)
            minutes = _charge_time_min(sug.station, max(0.0, cap * (high - arr) / 100.0), cc)
            s_score = _station_score(sug, minutes, max_detour_km, report, tariffs)
            couple = p_score * s_score / 100.0
            if best is None or couple > best[0] + _EPS:
                best = (couple, p_score, s_score, sug, arr)
        return best

    stops: list[ChargeStop] = []
    warns: list[str] = []
    pos, soc = 0.0, veh.start_soc_pct
    while not finishes(pos, soc):
        if len(stops) >= MAX_STOPS:
            warns.append("Plan de recharge interrompu : trop d'arrêts nécessaires.")
            break
        cur = best_couple(pos, soc)
        if cur is None or cur[0] <= _EPS:
            warns.append("Aucune borne atteignable avec la charge souhaitée sur la suite du trajet.")
            break
        couple, p_score, s_score, sug, arr = cur
        half = sug.detour_kwh / 2.0
        # Provisional full charge to find the next stop, then charge just enough to reach it (or the arrival).
        leave_high = high - 100.0 * half / cap
        if finishes(sug.route_km, leave_high):
            need = floor + max(100.0 * energy_between(slices, cfg, temp, s, sug.route_km, total_km) / cap
                               for s in ("moyenne", "pessimiste"))
        else:
            nxt = best_couple(sug.route_km, leave_high)
            if nxt is None or nxt[0] <= _EPS:
                need = high
            else:
                n_sug = nxt[3]
                need = reserve + 100.0 * (energy_between(slices, cfg, temp, "pessimiste", sug.route_km, n_sug.route_km)
                                         + n_sug.detour_kwh / 2.0) / cap
        need += 100.0 * half / cap  # return leg of the detour
        after = _clamp(need, arr, max(arr, high))
        add_kwh = cap * (after - arr) / 100.0
        minutes = _charge_time_min(sug.station, add_kwh, cc)
        price, _ = resolve_price(sug.station, tariffs)
        cost = None if price is None else add_kwh / cc.charge_efficiency * price
        stops.append(ChargeStop(sug, arr, after, add_kwh, minutes, p_score, s_score, couple, cost))
        pos, soc = sug.route_km, after - 100.0 * half / cap
    return stops, warns
