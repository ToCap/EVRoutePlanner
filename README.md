# ev_trip

Remaining-charge simulator for electric vehicles, built on OpenStreetMap (Nominatim, OSRM, Overpass).
The route is split into slices of at most 10 km by road type; consumption depends on road type and temperature,
and the trip is simulated for 3 scenarios (optimistic / mean / pessimistic). Charging stations along the route are suggested and scored.

## Install

    python -m venv .venv
    source .venv/bin/activate          # Windows: .venv\Scripts\activate
    pip install -e ".[dev]"            # Python >= 3.10; add ".[web]" for Django, or use requirements.txt for Dash

In VS Code: open the folder, run the task **Installer : venv + dépendances**, then `F5` on a launch configuration.

## Usage

CLI:

    python -m ev_trip "Paris" "Saint-Lô" --temp 8 --battery 52 --soc 90 --min-soc 10 --csv trip.csv

| Option | Purpose |
|---|---|
| `--temp` | uniform outside temperature (°C) |
| `--battery` | battery capacity (kWh) |
| `--soc` | charge at departure (%) |
| `--min-soc` | minimum charge threshold (%), default 10 |
| `--config` | JSON consumption model (see `config.example.json`) |
| `--max-slice-km` | maximum slice length, default 10 |
| `--max-detour KM` | search and score charging stations within KM of detour |
| `--tariffs` | JSON of per-operator tariffs (see `tariffs.json`) |
| `--no-refine` / `--no-overpass` | fast first estimate only / road type from OSRM speed |

Web interfaces (same `TripService`, run a single process for Dash):

    python manage.py runserver         # Django: http://127.0.0.1:8000
    python -m interfaces.dash_ui       # Dash:   http://127.0.0.1:8050

## Layout

| Path | Role |
|---|---|
| `ev_trip/domain` | pure business logic, no I/O (models, consumption, simulation, slicing, charging, ports) |
| `ev_trip/providers` | Nominatim, OSRM, Overpass, JSON files |
| `ev_trip/application` | use cases (`run_trip`, `plan_charging`) and task manager (`TripService`) |
| `ev_trip/cli.py`, `ev_trip/report.py` | command line, console/CSV output |
| `interfaces/django_ui`, `interfaces/dash_ui` | web interfaces |
| `tests/` | offline tests (OSM services mocked) |

Dependencies only go downward, enforced by `tests/test_architecture.py`.

## Configuration

| Variable | Purpose |
|---|---|
| `EV_TRIP_USER_AGENT` | User-Agent sent to OSM services (add a contact) |
| `EV_TRIP_OVERPASS_URL` | preferred Overpass server (mirrors as fallback) |
| `EV_TRIP_TILE_URL`, `EV_TRIP_TILE_ATTRIBUTION` | map tiles (OpenStreetMap by default) |
| `EV_TRIP_HOST`, `EV_TRIP_PORT`, `EV_TRIP_DEBUG`, `EV_TRIP_TARIFFS` | Dash host, port, debug mode, tariffs file |
| `DJANGO_DEBUG`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` | Django production settings |

## Tests

    pytest        # 215 offline tests

## Docs

- [Architecture](docs/architecture.md)
- [Computation: sampling, consumption, simulation](docs/calcul.md)
- [Charging stations](docs/bornes.md)
