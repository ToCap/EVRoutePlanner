"""Form values <-> `TripRequest` / query string (URL sharing); pure functions, no Dash."""
from __future__ import annotations

from urllib.parse import parse_qs, urlencode

from ev_trip.application.jobs import TripRequest
from ev_trip.domain.config import DEFAULT_CHARGERS

DEFAULTS = {
    "origin": "", "destination": "", "temp_c": 15.0, "battery_kwh": 52.0,
    "start_soc": 90.0, "min_soc": 10.0, "max_detour_km": 5.0, "use_overpass": True, "chargers": list(DEFAULT_CHARGERS),
}
_RANGES = {  # field: (label, min, max, required)
    "temp_c": ("Température", -50, 60, True),
    "battery_kwh": ("Capacité de la batterie", 1, 300, True),
    "start_soc": ("Charge au départ", 0, 100, True),
    "min_soc": ("Seuil minimum", 0, 100, True),
    "max_detour_km": ("Détour max", 0, 50, False),
}


class FieldError(ValueError):
    """Validation error tied to form fields (`fields`: keys of `DEFAULTS`), highlighted in red by the UI."""

    def __init__(self, message: str, fields: tuple[str, ...]):
        super().__init__(message)
        self.fields = fields


def request_from_values(values: dict) -> TripRequest:
    """Validates entered values; raises ValueError with a readable message."""
    origin = (values.get("origin") or "").strip()
    destination = (values.get("destination") or "").strip()
    if not origin or not destination:
        raise FieldError("Renseigner le départ et la destination.", tuple(k for k, v in (("origin", origin), ("destination", destination)) if not v))
    numbers: dict[str, float] = {}
    for name, (label, lo, hi, required) in _RANGES.items():
        raw = values.get(name)
        if raw in (None, ""):
            if required:
                raise FieldError(f"{label} : valeur obligatoire.", (name,))
            numbers[name] = 0.0
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError):
            raise FieldError(f"{label} : saisir une valeur numérique.", (name,)) from None
        if not lo <= number <= hi:
            raise FieldError(f"{label} : la valeur doit être comprise entre {lo} et {hi}.", (name,))
        numbers[name] = number
    chargers = tuple(values.get("chargers") or ())
    if numbers["max_detour_km"] > 0 and not chargers:
        raise ValueError("Recharge : sélectionner au moins un niveau de recharge (onglet Énergie).")
    request = TripRequest(
        origin, destination, numbers["temp_c"], numbers["battery_kwh"], numbers["start_soc"],
        numbers["min_soc"], bool(values.get("use_overpass", True)), numbers["max_detour_km"], chargers,
    )
    request.vehicle()  # same check as the domain (capacity, percentages)
    return request


def to_query(request: TripRequest) -> str:
    params = {
        "origin": request.origin, "destination": request.destination, "temp_c": f"{request.temp_c:g}",
        "battery_kwh": f"{request.battery_kwh:g}", "start_soc": f"{request.start_soc:g}",
        "min_soc": f"{request.min_soc:g}", "max_detour_km": f"{request.max_detour_km:g}",
        "use_overpass": "1" if request.use_overpass else "0", "chargers": ",".join(request.chargers),
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
    if "chargers" in q:
        values["chargers"] = [c for c in q["chargers"].split(",") if c]  # unknown ids are rejected by TripRequest
    if "use_overpass" in q:
        values["use_overpass"] = q["use_overpass"] not in ("0", "false", "")
    return values
