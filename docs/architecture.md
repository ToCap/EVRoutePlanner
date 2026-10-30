# Architecture

Four layers; dependencies only go downward (checked by `tests/test_architecture.py`).

```
interfaces/django_ui, interfaces/dash_ui, ev_trip/cli.py, ev_trip/report.py, ev_trip/presentation.py
        │
ev_trip/application   run_trip, refine_trip, plan_charging, exit_variants, jobs (TripService)
        │
ev_trip/providers     nominatim, osrm, overpass, files        ev_trip/domain   (pure, no I/O)
        └──────────────────────────────────────────────────────────┘
```

| Package | Contents | Rule |
|---|---|---|
| `ev_trip.domain` | models, config, consumption, simulation, slicing, sampling, road classification, charging (stations, tariffs, scoring), recharge (multi-stop plan), route profiles, `TripReport`, ports, errors | no network, files or clock; standard library only |
| `ev_trip.providers` | `nominatim` (geocoding), `osrm` (route, detour path), `overpass` (road types, stations), `files` (JSON config and tariffs) | implement the domain ports |
| `ev_trip.application` | `trip.py` (`run_trip`, `refine_trip`, `plan_slices`), `charging.py` (`plan_charging`), `exit_variants.py`, `messages.py` (short user-facing error texts), `jobs.py` (`TripService`, `TripStore`, `TripRequest`) | orchestration only |
| `interfaces` | Django and Dash UIs, plus the CLI | translate between a UI and `TripService`; no business logic |

## Ports and injection

`domain/ports.py` defines `GeocodeFn`, `RouteFn`, `PathFn`, `RoadClassifier`, `StationSource`. Providers implement them; use cases receive them as arguments (`geocode_fn`, `route_fn`, `classifier`, `stations`, `detour_fn`). Tests replace them with fakes, so the whole suite runs offline.

## Request flow

1. An interface builds a `TripRequest` and calls `TripService.start`.
2. `start` runs `run_trip(refine=False)`: first estimate returned immediately (`precision="coarse"`).
3. If exit variants, a refinement or a station search is useful, one background task runs through the `runner` (thread by default): motorway exit variants, `refine_trip`, then `plan_charging` (stations and recharge plan). See [itineraire.md](itineraire.md).
4. The state (`refining`, `done`, `failed`) is stored in a `TripStore` (`MemoryStore`, or the Django cache). Interfaces poll it and redisplay.
5. `ev_trip.presentation` turns a report into display data (map layers, chart, tables) shared by both interfaces.

## Interfaces

- **Django** (`interfaces/django_ui`): GET form, shareable URL, results cached for 1 h, JavaScript polling of `/status`. Several processes need a shared cache (`CACHES`).
- **Dash** (`interfaces/dash_ui`): map on 2/3 of the height, routes on the last third, collapsible settings panel with 4 tabs (Trajet, Bornes, Énergie, Configuration). State is in memory: run a single process.
- **CLI** (`ev_trip/cli.py`): runs the pipeline synchronously.

## Testing

`tests/` mocks every OSM service. `test_architecture.py` forbids relative imports and any upward dependency, and checks that the domain does no I/O.
