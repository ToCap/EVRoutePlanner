"""Form values <-> `TripRequest` / query string (URL sharing); pure functions, no Dash."""
from __future__ import annotations

from urllib.parse import parse_qs, urlencode

from ev_trip.application.jobs import TripRequest

DEFAULTS = {
    "origin": "", "destination": "", "temp_c": 15.0, "battery_kwh": 52.0,
    "start_soc": 90.0, "min_soc": 10.0, "max_detour_km": 5.0, "use_overpass": True,
}
_RANGES = {  # field: (label, min, max, required)
    "temp_c": ("Température", -50, 60, True),
    "battery_kwh": ("Capacité de la batterie", 1, 300, True),
    "start_soc": ("Charge au départ", 0, 100, True),
    "min_soc": ("Seuil minimum", 0, 100, True),
    "max_detour_km": ("Détour max", 0, 50, False),
}


def request_from_values(values: dict) -> TripRequest:
    """Validates entered values; raises ValueError with a readable message."""
    origin = (values.get("origin") or "").strip()
    destination = (values.get("destination") or "").strip()
    if not origin or not destination:
        raise ValueError("Renseigner le départ et la destination.")
    numbers: dict[str, float] = {}
    for name, (label, lo, hi, required) in _RANGES.items():
        raw = values.get(name)
        if raw in (None, ""):
            if required:
                raise ValueError(f"{label} : valeur obligatoire.")
            numbers[name] = 0.0
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{label} : saisir une valeur numérique.") from None
        if not lo <= number <= hi:
            raise ValueError(f"{label} : la valeur doit être comprise entre {lo} et {hi}.")
        numbers[name] = number
    request = TripRequest(
        origin, destination, numbers["temp_c"], numbers["battery_kwh"], numbers["start_soc"],
        numbers["min_soc"], bool(values.get("use_overpass", True)), numbers["max_detour_km"],
    )
    request.vehicle()  # same check as the domain (capacity, percentages)
    return request


def to_query(request: TripRequest) -> str:
    params = {
        "origin": request.origin, "destination": request.destination, "temp_c": f"{request.temp_c:g}",
        "battery_kwh": f"{request.battery_kwh:g}", "start_soc": f"{request.start_soc:g}",
        "min_soc": f"{request.min_soc:g}", "max_detour_km": f"{request.max_detour_km:g}",
        "use_overpass": "1" if request.use_overpass else "0",
    }
    return "?" + urlencode(params)


def from_query(search: str | None) -> dict | None:
    """Form values from a query string; None if it does not describe a trip."""
    q = {k: v[0] for k, v in parse_qs((search or "").lstrip("?")).items() if v}
    if not q.get("origin") or not q.get("destination"):
        return None
    values: dict = {**DEFAULTS, "origin": q["origin"], "destination": q["destination"]}
    for name in _RANGES:
        if name in q:
            try:
                values[name] = float(q[name])
            except ValueError:
                values[name] = q[name]  # will be rejected by request_from_values
    if "use_overpass" in q:
        values["use_overpass"] = q["use_overpass"] not in ("0", "false", "")
    return values
