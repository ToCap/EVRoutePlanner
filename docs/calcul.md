# Computation: road sampling, consumption, simulation

## Pipeline

geocoding (Nominatim) → route with steps (OSRM) → road sections → road type per section (Overpass) → slices of at most 10 km → simulation in 3 scenarios.

## Road sections and sampling

OSRM returns the route **and its steps** (road name / reference). Consecutive steps on the same road ("A 13", "A13", "a-13") form one **section**.

- **Pass 1 (fast):** one Overpass query point per section, searched by position (never by name). A few dozen points instead of ~600. Section limits are the exact junctions, not rounded to 500 m. The result is displayed immediately (`precision="coarse"`).
- **Pass 2 (refinement):** a road can cross villages (50 km/h) then speed up again. On **non-motorway** sections, one point every `refine_step_km` (1 km) is queried; motorway sections keep their pass-1 type. If Overpass failed in pass 1, pass 2 acts as a retry (known types are kept if a query fails).
- **No OSRM steps:** regular sampling every `sample_step_km` (500 m).

## Road type of an OSM way (`classify_highway`)

| Way | Type |
|---|---|
| `motorway` | autoroute |
| `trunk` | ville if maxspeed <= 50, autoroute if >= 110, else route |
| any `*_link` | route |
| other, with `maxspeed` | ville if <= 50, autoroute if >= 110, else route |
| `residential`, `living_street`, `service`, `road` without maxspeed | ville |
| any other drivable way | route |

At a given point, the nearest way (within ~5 m) wins; ties go to the most important road class.

## Overpass robustness

A User-Agent is always sent (`EV_TRIP_USER_AGENT`). On 429/502/503/504 the client waits for `Retry-After` (capped at 30 s) or a growing delay, and switches mirror on each attempt (3 attempts). `EV_TRIP_OVERPASS_URL` forces a preferred server. If Overpass stays unavailable, types fall back to the OSRM average speed (< 45 km/h ville, >= 88 km/h autoroute, else route) and a warning is added.

## Slicing

Sections are cut at each road-type change into slices of at most `max_slice_km` (10). A type change shorter than `min_run_km` surrounded by the same type is absorbed (OSM matching noise); `min_run_steps_km` (0.5) applies to section-based sampling.

## Consumption

`consumption = base(road type, scenario) × (1 + temperature surcharge)`.

| Road type | optimiste | moyenne | pessimiste |
|---|---|---|---|
| ville | 11 | 12.5 | 14 |
| route | 14 | 14.8 | 15.6 |
| autoroute | 18 | 19.5 | 21 |

(kWh/100 km at 20 °C; "moyenne" is the midpoint.) Temperature surcharge: < -5 °C +35 %, [-5, 0[ +25 %, [0, 10[ +15 %, [10, 15[ +8 %, [15, 25[ 0 %, >= 25 °C +5 %. Ranges and brackets are editable in a JSON file (`--config`, see `config.example.json`).

## Simulation

For each slice: `energy = length × consumption / 100`, `remaining -= energy`. The reserve (`min_soc`) is never dipped into: the slice that would cross it is truncated at the exact km and the trip stops there (`completed=False`, with an `Alert`). With a threshold of 0 %, the stop happens at an empty battery.

Worked example: 10 kWh battery, reserve 50 %, mean city 12.5 kWh/100 km: 5 kWh usable = 40 km; with 15 km slices the result is 15 + 15 + 10 km (truncated).

The 3 scenarios use the same slices; only the consumption differs. Output (console, CSV with `;` separator, web) shows kWh/100 km, kWh and charge % per scenario, plus a summary (km driven, energy, final charge, status). A "–" cell is a slice never reached.
