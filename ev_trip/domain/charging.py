"""Charging stations, pure domain part: station model, tariffs, charge along the trip, scoring (no I/O)."""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

from dataclasses import replace

from ev_trip.domain.config import CHARGER_LEVELS, ChargingConfig
from ev_trip.domain.consumption import consumption_kwh_per_100km
from ev_trip.domain.geo import cumulative_km
from ev_trip.domain.models import Point, Route, SimulationResult, Slice
from ev_trip.domain.trip_report import TripReport

_EPS = 1e-9
_FAST_SOCKETS = ("type2_combo", "chademo", "tesla_supercharger", "type1_combo")
_SLOW_SOCKETS = ("type2", "type1")
_CAR_SOCKET_RE = re.compile(r"^socket:(type1|type2|type1_combo|type2_combo|chademo|tesla_[a-z_]+)(:|$)")


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


# --------------------------------------------------------------------------- OSM stations


@dataclass(frozen=True)
class Station:
    id: str
    lat: float
    lon: float
    name: str = ""
    operator: str = ""
    network: str = ""
    brand: str = ""
    power_kw: float | None = None
    charge_type: str | None = None  # "rapide" | "normale" | None (unknown)
    capacity: int | None = None
    fee: str | None = None  # OSM fee tag (yes/no)
    osm_price_eur_kwh: float | None = None
    tariff_text: str = ""

    @property
    def point(self) -> Point:
        return Point(self.lat, self.lon)

    @property
    def label(self) -> str:
        return self.name or self.operator or self.network or self.brand or "Borne"

    @property
    def operator_label(self) -> str:
        return self.operator or self.network or self.brand


def parse_power_kw(value: str | None) -> float | None:
    """Power of an OSM tag in kW ("22 kW", "50", "22000" = W); several values: the largest."""
    if not value:
        return None
    best: float | None = None
    for part in re.split(r"[;/]", value.lower()):
        m = re.match(r"\s*(\d+(?:[.,]\d+)?)\s*(kw|w)?", part)
        if not m:
            continue
        num = float(m.group(1).replace(",", "."))
        unit = m.group(2)
        if unit == "w" or (unit is None and num >= 1000):
            num /= 1000.0
        if num > 0 and (best is None or num > best):
            best = num
    return best


def parse_price_eur_kwh(value: str | None) -> float | None:
    """Price in EUR/kWh from an OSM `charge` tag ("0.45 EUR/kWh", "0,40 €/kWh"); None if absent or other currency."""
    if not value:
        return None
    text = value.lower()
    if "kwh" not in text or re.search(r"\b(usd|gbp|chf)\b|[$£]", text):
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:€|eur\w*)?\s*(?:/|per)\s*kwh", text)
    if not m:
        return None
    price = float(m.group(1).replace(",", "."))
    return price if 0 <= price < 5 else None


def _tag_power(tags: dict[str, str]) -> float | None:
    powers = [
        parse_power_kw(v)
        for k, v in tags.items()
        if (k.startswith("socket:") and k.endswith(":output")) or k == "charging_station:output"
    ]
    powers = [p for p in powers if p is not None]
    return max(powers) if powers else None


def _socket_active(tags: dict[str, str], socket: str) -> bool:
    value = tags.get(f"socket:{socket}")
    return value is not None and value.strip().lower() not in ("0", "no", "")


def station_from_element(el: dict, cfg: ChargingConfig | None = None) -> Station | None:
    """Usable station from an Overpass element (node or way with `center`), else None."""
    cfg = cfg or ChargingConfig()
    tags = el.get("tags") or {}
    if tags.get("amenity") != "charging_station":
        return None
    if tags.get("access") in ("private", "no") or tags.get("motorcar") == "no":
        return None
    has_car_socket = any(_CAR_SOCKET_RE.match(k) for k in tags)
    if tags.get("bicycle") == "yes" and tags.get("motorcar") != "yes" and not has_car_socket:
        return None

    lat = el.get("lat", (el.get("center") or {}).get("lat"))
    lon = el.get("lon", (el.get("center") or {}).get("lon"))
    if lat is None or lon is None:
        return None

    power = _tag_power(tags)
    if power is not None:
        ctype: str | None = "rapide" if power >= cfg.fast_kw else "normale"
    elif any(_socket_active(tags, s) for s in _FAST_SOCKETS):
        ctype = "rapide"
    elif any(_socket_active(tags, s) for s in _SLOW_SOCKETS):
        ctype = "normale"
    else:
        ctype = None

    capacity: int | None = None
    try:
        capacity = int(tags["capacity"]) if "capacity" in tags else None
    except ValueError:
        pass

    return Station(
        id=f"{el.get('type', 'node')}/{el.get('id', '?')}",
        lat=float(lat),
        lon=float(lon),
        name=tags.get("name", ""),
        operator=tags.get("operator", ""),
        network=tags.get("network", ""),
        brand=tags.get("brand", ""),
        power_kw=power,
        charge_type=ctype,
        capacity=capacity,
        fee=tags.get("fee"),
        osm_price_eur_kwh=parse_price_eur_kwh(tags.get("charge")),
        tariff_text=tags.get("charge", ""),
    )


# --------------------------------------------------------------------------- vehicle chargers


def _levels(chargers) -> list:
    wanted = set(chargers or ())
    return [lv for lv in CHARGER_LEVELS if lv.id in wanted]


def station_compatible(station: Station, chargers) -> bool:
    """Fast (DC) stations need a DC level, normal (AC) ones an AC level; unknown type: kept; no selection: all kept."""
    levels = _levels(chargers)
    if not levels or station.charge_type is None:
        return True
    return any(lv.current == ("DC" if station.charge_type == "rapide" else "AC") for lv in levels)


def apply_chargers(cfg: ChargingConfig, chargers) -> ChargingConfig:
    """Config whose vehicle power limits are the highest selected AC / DC levels (others unchanged)."""
    levels = _levels(chargers)
    ac = [lv.kw for lv in levels if lv.current == "AC"]
    dc = [lv.kw for lv in levels if lv.current == "DC"]
    return replace(cfg, vehicle_max_ac_kw=max(ac) if ac else cfg.vehicle_max_ac_kw,
                   vehicle_max_dc_kw=max(dc) if dc else cfg.vehicle_max_dc_kw)


def apply_charge_profile(cfg: ChargingConfig, battery_kwh: float, window: tuple[float, float],
                         minutes: dict[str, float], chargers=None) -> ChargingConfig:
    """Config capped at the window end, with the battery-side kW of each selected level the user timed (minutes over the window)."""
    low, high = window
    selected = {lv.id for lv in _levels(chargers)} if chargers else {lv.id for lv in CHARGER_LEVELS}
    kw = {lv.id: battery_kwh * (high - low) / 100.0 / (minutes[lv.id] / 60.0)
          for lv in CHARGER_LEVELS if lv.id in selected and minutes.get(lv.id)}
    return replace(cfg, target_max_soc_pct=high, charge_window_pct=(low, high), level_kw=kw) if kw else cfg


# --------------------------------------------------------------------------- tariffs


class TariffTable:
    """Tariffs in EUR/kWh by operator and charge type (editable JSON); case-insensitive substring match, longest key wins."""

    def __init__(self, operators: dict[str, dict[str, float]] | None = None) -> None:
        self.operators = {k.strip().lower(): v for k, v in (operators or {}).items() if k.strip()}

    @classmethod
    def from_dict(cls, data: dict) -> "TariffTable":
        """Builds the table from JSON content (file reading: `providers.files.load_tariffs`)."""
        ops: dict[str, dict[str, float]] = {}
        for key, entry in (data.get("operators") or {}).items():
            if key.startswith("_"):
                continue
            if isinstance(entry, (int, float)):
                entry = {"any": entry}
            if not isinstance(entry, dict):
                raise ValueError(f"Tarif invalide pour « {key} ».")
            prices: dict[str, float] = {}
            for kind, price in entry.items():
                if kind not in ("rapide", "normale", "any"):
                    continue
                if not isinstance(price, (int, float)) or price < 0:
                    raise ValueError(f"Prix invalide pour « {key} / {kind} » : il faut un nombre >= 0.")
                prices[kind] = float(price)
            ops[key] = prices
        return cls(ops)

    def lookup(self, station: Station) -> float | None:
        texts = [t.lower() for t in (station.name, station.operator, station.network, station.brand) if t]
        for key in sorted(self.operators, key=len, reverse=True):
            if any(key in t for t in texts):
                prices = self.operators[key]
                if station.charge_type and station.charge_type in prices:
                    return prices[station.charge_type]
                if "any" in prices:
                    return prices["any"]
        return None


def resolve_price(station: Station, tariffs: TariffTable | None) -> tuple[float | None, str]:
    """(price EUR/kWh, source): OSM `charge` tag > free (fee=no) > operator tariff > unknown."""
    if station.osm_price_eur_kwh is not None:
        return station.osm_price_eur_kwh, "osm"
    if station.fee == "no":
        return 0.0, "gratuit"
    if tariffs is not None:
        price = tariffs.lookup(station)
        if price is not None:
            return price, "tarif"
    return None, "inconnu"


# --------------------------------------------------------------------------- energy and charge along the trip


def soc_at_km(sim: SimulationResult, km: float) -> float | None:
    """Charge (%) at a given km (linear interpolation per slice); None beyond the simulation stop."""
    if km <= 0:
        return sim.vehicle.start_soc_pct
    cap = sim.vehicle.battery_kwh
    for r in sim.results:
        sl = r.slice
        if km <= sl.end_km + _EPS:
            before = r.soc_pct + 100.0 * r.energy_kwh / cap
            length = sl.end_km - sl.start_km
            frac = 0.0 if length <= 0 else _clamp((km - sl.start_km) / length, 0.0, 1.0)
            return before - frac * (before - r.soc_pct)
    return sim.final_soc_pct if sim.results and km <= sim.total_km + _EPS else None


def energy_between(
    slices: list[Slice], cfg, temp_c: float, scenario: str, start_km: float, end_km: float
) -> float:
    """Energy (kWh) consumed between two km, by road type of each slice."""
    total = 0.0
    for sl in slices:
        overlap = min(sl.end_km, end_km) - max(sl.start_km, start_km)
        if overlap > 0:
            total += overlap * consumption_kwh_per_100km(sl.road_type, temp_c, cfg, scenario) / 100.0
    return total


def _road_type_at(slices: list[Slice], km: float) -> str:
    starts = [s.start_km for s in slices]
    i = max(0, min(bisect.bisect_right(starts, km) - 1, len(slices) - 1))
    return slices[i].road_type


def arrival_floor_pct(vehicle, margin_pct: float) -> float:
    """Charge (%) a trip should end with: the wanted arrival charge if it is above the minimum, else the minimum plus the margin."""
    target = vehicle.arrival_soc_pct
    return target if target is not None and target > vehicle.min_soc_pct else vehicle.min_soc_pct + margin_pct


def need_level(report: TripReport) -> str:
    """Need level: "nécessaire" (mean scenario fails or ends under the wanted charge), "prudent" (pessimistic tight or fails), else "optionnelle"."""
    sim = report.simulation
    if not sim.completed or sim.short_of_arrival:
        return "nécessaire"
    pess = report.simulations["pessimiste"]
    if not pess.completed or pess.final_soc_pct < arrival_floor_pct(pess.vehicle, report.config.charging.arrival_margin_pct):
        return "prudent"
    return "optionnelle"


# --------------------------------------------------------------------------- detour


def route_time_min(route: Route, cum: list[float], km: float) -> float:
    """Time (min) to reach abscissa `km` along the route."""
    total = cum[-1]
    if route.durations_s is not None and len(route.durations_s) == len(cum) - 1:
        i = max(0, min(bisect.bisect_right(cum, km) - 1, len(cum) - 2))
        t = sum(route.durations_s[:i])
        seg = cum[i + 1] - cum[i]
        frac = 0.0 if seg <= 0 else _clamp((km - cum[i]) / seg, 0.0, 1.0)
        return (t + frac * route.durations_s[i]) / 60.0
    return route.duration_min * (km / total if total > 0 else 0.0)


def detour_anchors_km(route_km: float, total_km: float, cfg: ChargingConfig) -> tuple[float, float]:
    """Route abscissas A and B between which the detour is measured (± `detour_anchor_km`)."""
    return max(0.0, route_km - cfg.detour_anchor_km), min(total_km, route_km + cfg.detour_anchor_km)


def detour_from_via(
    route: Route, cum: list[float], a_km: float, b_km: float, via_km: float, via_min: float
) -> tuple[float, float]:
    """Detour (km, min): route A -> station -> B minus the direct route A -> B."""
    direct_min = route_time_min(route, cum, b_km) - route_time_min(route, cum, a_km)
    return max(0.0, via_km - (b_km - a_km)), max(0.0, via_min - direct_min)


def estimate_detour(distance_km: float, cfg: ChargingConfig) -> tuple[float, float]:
    """Detour (km, min) estimated from crow-flies distance, when routing is unavailable."""
    km = 2.0 * distance_km * cfg.road_factor
    return km, km * 1.5


# --------------------------------------------------------------------------- scoring


@dataclass
class StationSuggestion:
    station: Station
    route_km: float  # station abscissa along the route
    detour_km: float
    detour_min: float
    detour_kwh: float  # extra consumption (round trip, mean scenario)
    detour_estimated: bool  # detour estimated as the crow flies (OSRM not used)
    soc_route_pct: float | None  # charge on the route, at the station abscissa (mean)
    soc_arrival_pct: float | None  # charge on arrival at the station (mean)
    soc_after_pct: float | None  # charge after recharging
    reachable: bool
    reachable_pessimiste: bool
    add_kwh: float
    charge_min: float | None
    price_eur_kwh: float | None
    price_source: str  # osm | gratuit | tarif | inconnu
    cost_eur: float | None
    score: float
    sub_scores: dict[str, float]  # detour / need / charge / price, out of 100
    notes: list[str] = field(default_factory=list)


def _effective_kw(station: Station, cfg: ChargingConfig) -> float | None:
    """Timed battery-side power of the highest timed level not above the station power (same current type), else None."""
    if not cfg.level_kw:
        return None
    fast = station.charge_type == "rapide"
    station_kw = station.power_kw or (cfg.assumed_fast_kw if fast else cfg.assumed_ac_kw)
    current = None if station.charge_type is None else ("DC" if fast else "AC")
    fits = [lv for lv in CHARGER_LEVELS
            if lv.id in cfg.level_kw and current in (None, lv.current) and lv.kw <= station_kw + _EPS]
    return cfg.level_kw[max(fits, key=lambda lv: lv.kw).id] if fits else None


def _charge_time_min(station: Station, add_kwh: float, cfg: ChargingConfig) -> float | None:
    if add_kwh <= _EPS:
        return 0.0
    if station.power_kw is None and station.charge_type is None:
        return None
    timed = _effective_kw(station, cfg)
    if timed is not None:  # user-timed profile: already battery-side, no efficiency factor
        return add_kwh / timed * 60.0
    fast = station.charge_type == "rapide"
    kw = station.power_kw or (cfg.assumed_fast_kw if fast else cfg.assumed_ac_kw)
    cap = cfg.vehicle_max_dc_kw if fast else cfg.vehicle_max_ac_kw
    return add_kwh / (min(kw, cap) * cfg.charge_efficiency) * 60.0


def score_station(
    report: TripReport,
    station: Station,
    route_km: float,
    detour_km: float,
    detour_min: float,
    max_detour_km: float,
    *,
    level: str | None = None,
    tariffs: TariffTable | None = None,
    estimated: bool = False,
) -> StationSuggestion:
    cc = report.config.charging
    moy = report.simulations["moyenne"]
    pess = report.simulations["pessimiste"]
    v = moy.vehicle
    cap = v.battery_kwh
    temp = moy.temp_c
    slices = report.slices
    level = level or need_level(report)
    notes: list[str] = []

    trip_km = moy.trip_km
    cons = consumption_kwh_per_100km(_road_type_at(slices, route_km), temp, report.config, "moyenne")
    detour_kwh = detour_km * cons / 100.0
    half_kwh = detour_kwh / 2.0

    # Reachability: charge on arrival at the station >= reserve (mean; pessimistic = flag only).
    soc_route = soc_at_km(moy, route_km)
    soc_arr = None if soc_route is None else soc_route - 100.0 * half_kwh / cap
    reachable = soc_arr is not None and soc_arr >= v.min_soc_pct - 1e-6

    p_route = soc_at_km(pess, route_km)
    p_cons = consumption_kwh_per_100km(_road_type_at(slices, route_km), temp, report.config, "pessimiste")
    p_arr = None if p_route is None else p_route - 100.0 * (detour_km / 2.0) * p_cons / 100.0 / cap
    reachable_pess = p_arr is not None and p_arr >= v.min_soc_pct - 1e-6

    # Detour criterion
    s_detour = _clamp(100.0 * (1.0 - detour_km / max_detour_km)) if max_detour_km > 0 else 100.0

    # Recharge need and energy to add
    add_kwh = 0.0
    s_need = 0.0
    if reachable and soc_arr is not None:
        arr_kwh = cap * soc_arr / 100.0
        reserve_kwh = cap * arrival_floor_pct(v, cc.arrival_margin_pct) / 100.0
        to_finish = energy_between(slices, report.config, temp, "moyenne", route_km, trip_km) + half_kwh
        missing = max(0.0, to_finish + reserve_kwh - arr_kwh)
        headroom = max(0.0, cap * cc.target_max_soc_pct / 100.0 - arr_kwh)
        if missing > _EPS:
            add_kwh = min(missing, headroom)
            if cc.charge_window_pct is not None and missing > headroom + _EPS:
                notes.append(f"recharge limitée à {cc.target_max_soc_pct:g} %")
        else:
            add_kwh = min(cap * cc.nominal_top_up_pct / 100.0, headroom)

        margin = soc_arr - v.min_soc_pct
        span = max(1.0, 100.0 - v.min_soc_pct - cc.need_window_pct)
        if margin <= cc.need_window_pct:
            base = 100.0
        else:
            base = cc.need_floor_score + (100.0 - cc.need_floor_score) * max(
                0.0, 1.0 - (margin - cc.need_window_pct) / span
            )
        if level == "optionnelle":
            s_need = 10.0
        else:
            factor = 1.0 if level == "nécessaire" else 0.8
            s_need = 10.0 + (base - 10.0) * factor
    s_need = _clamp(s_need)

    # Charge-type criterion: recharge duration
    charge_min = _charge_time_min(station, add_kwh, cc) if reachable else None
    if not reachable:
        s_charge = 0.0
    elif charge_min is None:
        s_charge = cc.neutral_score
        notes.append("puissance inconnue")
    else:
        s_charge = _clamp(100.0 * (1.0 - charge_min / cc.charge_time_ref_min))

    # Price criterion
    price, source = resolve_price(station, tariffs)
    if price is None:
        s_price = cc.neutral_score
        notes.append("prix inconnu")
    else:
        s_price = _clamp(100.0 * (1.0 - price / cc.price_max_eur_kwh))
    cost = None if price is None or not reachable else add_kwh / cc.charge_efficiency * price

    total_w = cc.weight_detour + cc.weight_need + cc.weight_charge + cc.weight_price
    score = (
        cc.weight_detour * s_detour + cc.weight_need * s_need
        + cc.weight_charge * s_charge + cc.weight_price * s_price
    ) / total_w
    if not reachable:
        score = 0.0
        notes.insert(0, "hors d'atteinte (réserve franchie avant la borne)")
    elif not reachable_pess:
        score *= cc.pessimistic_unreachable_factor
        notes.insert(0, "hors d'atteinte en scénario pessimiste")
    if estimated:
        notes.append("détour estimé")

    soc_after = None if soc_arr is None or not reachable else soc_arr + 100.0 * add_kwh / cap
    return StationSuggestion(
        station=station, route_km=route_km, detour_km=detour_km, detour_min=detour_min,
        detour_kwh=detour_kwh, detour_estimated=estimated, soc_route_pct=soc_route,
        soc_arrival_pct=soc_arr, soc_after_pct=soc_after, reachable=reachable,
        reachable_pessimiste=reachable_pess, add_kwh=add_kwh, charge_min=charge_min,
        price_eur_kwh=price, price_source=source, cost_eur=cost, score=round(score, 1),
        sub_scores={"detour": round(s_detour, 1), "need": round(s_need, 1),
                    "charge": round(s_charge, 1), "price": round(s_price, 1)},
        notes=notes,
    )


# --------------------------------------------------------------------------- plan


@dataclass
class ChargingPlan:
    suggestions: list[StationSuggestion]
    found: int  # stations found in the corridor
    evaluated: int  # stations whose detour was evaluated
    max_detour_km: float
    need_level: str
    warnings: list[str] = field(default_factory=list)
    stops: list = field(default_factory=list)  # chained recharge stops (`ChargeStop`), only when a recharge is useful
    covered: bool = False  # the stops let the trip reach its arrival
