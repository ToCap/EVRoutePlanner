# ev_trip: Python module specification

Version 0.1 · Python >= 3.10 · runtime dependency: `requests` only (interfaces add `django`, `dash`).

## 1. Purpose and scope

Simulates the remaining charge of an electric vehicle along a road trip and suggests charging stations.

- **In scope:** geocoding, routing, road-type detection (OpenStreetMap), slicing, energy simulation in 3 scenarios, station search and scoring, background task management.
- **Out of scope:** integrating a charging stop into the simulation (stations are ranked suggestions only), live traffic, elevation, vehicle-specific models, payment.

## 2. Architecture rules

Four layers; dependencies only go downward. Enforced by `tests/test_architecture.py` (AST based).

| Package | Role | May import |
|---|---|---|
| `ev_trip.domain` | pure logic, **no I/O** (no network, files, clock) | standard library only |
| `ev_trip.providers` | Nominatim, OSRM, Overpass, JSON files | domain |
| `ev_trip.application` | use cases and task manager | domain, providers |
| `ev_trip.cli`, `ev_trip.report`, `ev_trip.presentation`, `interfaces.*` | delivery | everything above |

- Absolute imports only. External services are injected (`geocode_fn`, `route_fn`, `classifier`, `stations`, `detour_fn`) through the ports in `domain/ports.py`.
- User-visible strings are French; code, comments and docstrings are English, one line each.

## 3. Public API

### 3.1 Trip computation: `ev_trip.application.trip`

```python
run_trip(origin: str, destination: str, temp_c: float, vehicle: VehicleParams,
         cfg: ConsumptionConfig | None = None, *, geocode_fn=..., route_fn=...,
         classifier: RoadClassifier | None = None, use_overpass: bool = True,
         refine: bool = True) -> TripReport
refine_trip(report: TripReport, classifier=None, *, use_overpass=True) -> TripReport
plan_slices(route: Route, cfg: ConsumptionConfig, classifier=None) -> tuple[list[Slice], list[str]]
```

- `refine=False` returns a fast first estimate (`report.precision == "coarse"`, one Overpass point per road section); `refine_trip` returns the final result (`"fine"`). `report.refinable` tells whether a refinement can still help.
- Raises `GeocodingError`, `RoutingError` (both `EVTripError`). An Overpass failure is **not** raised: slices fall back to OSRM average speed and a warning is added to `report.warnings`.

### 3.2 Charging stations: `ev_trip.application.charging`

```python
plan_charging(report: TripReport, max_detour_km: float, *, tariffs: TariffTable | None = None,
              stations: StationSource | None = None, detour_fn=None, sleep=time.sleep,
              chargers: tuple[str, ...] | None = None, charge_window: tuple[float, float] | None = None,
              charge_minutes: dict[str, float] | None = None) -> ChargingPlan
```

- `max_detour_km` must be > 0 (`ValueError` otherwise). A station search failure is reported in `plan.warnings`, never raised.
- `chargers`: ids of `CHARGER_LEVELS` the vehicle accepts (`dc100`, `ac22`, `ac11`, `ac7_4`, `ac3_7`, `ac2_3`). `None`/empty: no filtering.
- `charge_window`, `charge_minutes`: optional timed charge profile (minutes to go from x % to y % per level id); sets the effective power per level and caps the recharge at y (see `docs/bornes.md`).

### 3.3 Task manager: `ev_trip.application.jobs`

```python
TripRequest(origin, destination, temp_c, battery_kwh, start_soc, min_soc,
            use_overpass=True, max_detour_km=0.0, chargers=DEFAULT_CHARGERS,
            charge_from_pct=15.0, charge_to_pct=80.0, charge_minutes=(), battery_health_pct=100.0)  # frozen; .key() = result id
TripService(store=None, runner=thread_runner, tariffs=lambda: None, *, run_trip, refine, plan)
service.start(request) -> (key, TripEntry)   # first estimate now, refinement + stations in the background
service.get(key) -> TripEntry | None
```

- `TripEntry.state` is `refining`, `done` or `failed`; a failed task keeps the first-pass result.
- The same request gives the same key (canonical `chargers` order); one background task per key.
- `TripStore` is a protocol (`get`, `set`, `acquire`): `MemoryStore` by default, a Django cache store in the web app.

## 4. Data model (`ev_trip.domain.models`, `trip_report`, `charging`)

| Type | Invariants |
|---|---|
| `VehicleParams(battery_kwh, start_soc_pct, min_soc_pct=10)` | capacity > 0 (the real one: nominal x `TripRequest.battery_health_pct`); percentages in [0, 100] of that capacity (`ValueError`) |
| `Route` | coordinates, `distance_km`, `duration_min`, optional `durations_s` and `steps` |
| `Slice(index, start_km, end_km, road_type)` | length <= `max_slice_km` (10); one road type |
| `SimulationResult` | `results` = slices actually driven; `completed=False` and an `Alert` if the reserve is reached |
| `TripReport` | `simulations` keyed `optimiste` / `moyenne` / `pessimiste`; `.simulation` is the mean scenario |
| `StationSuggestion` | `score` /100 and `sub_scores` (detour, need, charge, price), `reachable`, `notes` |

Units: km, minutes, kWh, kWh/100 km, EUR/kWh, charge in %. Road types: `ville`, `route`, `autoroute`.

## 5. Business rules

**Consumption** = base(road type, scenario) × (1 + temperature surcharge).

| Road type | optimiste | moyenne | pessimiste |
|---|---|---|---|
| ville | 11 | 12.5 | 14 |
| route | 14 | 14.8 | 15.6 |
| autoroute | 18 | 19.5 | 21 |

Surcharge: < -5 °C +35 %, [-5, 0[ +25 %, [0, 10[ +15 %, [10, 15[ +8 %, [15, 25[ 0 %, >= 25 °C +5 %. All values are overridable through a JSON file (`load_config`).

**Slicing:** the route is cut at each road-type change into slices of at most 10 km; a type change shorter than `min_run_km` surrounded by the same type is absorbed.

**Simulation:** the reserve is never dipped into: the slice that would cross it is truncated at the exact km and the trip stops there.

**Road-type detection:** pass 1 queries one Overpass point per OSRM road section; pass 2 queries one point per km on non-motorway sections. Overpass uses a User-Agent, mirrors, and honors `Retry-After` (capped at 30 s).

**Station score** = weighted mean of detour .30, recharge need .35, charge duration .20, price .15 (out of 100).

- Price priority: OSM `charge` tag > `fee=no` > operator tariff (`tariffs.json`) > unknown (neutral 50).
- Charge time uses min(station power, vehicle limit); the vehicle limit is the highest selected AC or DC level.
- A station is dropped if no selected level matches its type (DC for fast, AC for normal); unknown type is kept.
- With a timed charge profile, charge time uses the effective power of the highest timed level not above the station power, and the energy added never exceeds the window end y.
- A station not reachable before the reserve scores 0; one unreachable in the pessimistic scenario is multiplied by 0.75.

## 6. Acceptance examples (offline tests)

| Case | Expected |
|---|---|
| 24 kWh usable, autoroute only | range 133.3 / 123.1 / 114.3 km (optimiste / moyenne / pessimiste) |
| 10 kWh battery, reserve 50 %, mean city 12.5, 15 km slices | 5 kWh usable = 40 km: slices of 15 + 15 + 10 (truncated), `completed=False` |
| 100 km at 12.5 kWh/100 km, 20 kWh battery, start 100 % | 12.5 kWh used, arrival 37.5 % |
| 3 scenarios on the same slices | only the consumption changes |
| Station search with only AC levels selected | fast DC stations absent, warning "N borne(s) écartée(s)" |
| Two `TripRequest` with the same chargers in another order | same `key()` |

## 7. Errors

`EVTripError` ← `GeocodingError` | `RoutingError` | `RoadClassificationError`. Invalid parameters raise `ValueError` with a French message. Application code converts service failures into warnings when a degraded result is still useful.

## 8. Testing and quality

- All tests offline (OSM services mocked): `pytest` (222 tests).
- Architecture rules and layer boundaries tested; every public function has a nominal and an error test.
- New behavior comes with a test in the same change.

## 9. Packaging and operations

`pyproject.toml` (package `ev_trip` + `interfaces.*`), extras `[dev]` and `[web]`. Entry points: `python -m ev_trip` (CLI), `python manage.py runserver` (Django), `python -m interfaces.dash_ui` (Dash). Environment variables: `EV_TRIP_USER_AGENT`, `EV_TRIP_OVERPASS_URL`, `EV_TRIP_TILE_URL`, `EV_TRIP_TILE_ATTRIBUTION`, `EV_TRIP_HOST`, `EV_TRIP_PORT`, `EV_TRIP_DEBUG`, `EV_TRIP_TARIFFS`.

## 10. Decisions and open points

- **Decided:** stations are suggestions, not part of the simulation; one `TripService` shared by the Django and Dash interfaces; Dash keeps state in memory (single process).
- **Open:** user-editable score weights and tariffs in the interface; consumption tab (eco mode, heating, air conditioning); shared cache or task queue for multi-process deployment.
