# Charging stations

With a maximum detour (`--max-detour` or the "Détour max" field, 0 = disabled), OpenStreetMap stations near the route are searched, their real detour is computed, and each one gets a **score out of 100**. The simulation itself is unchanged (it ignores stops). When a recharge is useful, a **recharge plan** of chained stops is built on top of the ranking (see below).

## Steps (`application/charging.plan_charging`)

1. **Search (Overpass):** stations within `max_detour × corridor_factor` (0.6) km of the route, in several short queries (25 route points each, merged by station id). An empty answer is retried on other mirrors; if only some queries fail, the stations found are kept. Private or non-car stations are ignored.
2. **Vehicle filter:** stations are dropped if no selected charger level matches their type (see below); a warning gives the count.
3. **Preselection:** detour estimated as the crow flies (`2 × distance × road_factor`, 1.3), provisional score; the best `max_candidates` (25) are kept.
4. **Real detour (OSRM):** route A → station → B minus the direct route A → B, with A and B at ± `detour_anchor_km` (15) on the route; one request per second (demo server). If OSRM fails, remaining detours stay estimated (flagged "détour estimé").
5. **Ranking:** stations with a detour above the maximum are dropped; the best `max_results` (15) are shown.
6. **Recharge plan:** if the need level is not "optionnelle", `plan_stops` chains the stops (below).

## Score

Weighted mean of four criteria (weights in `ChargingConfig`, only the ratio matters):

| Criterion | Weight | Rule |
|---|---|---|
| Detour | .30 | `100 × (1 − detour / max_detour)` |
| Recharge need | .35 | from the mean-scenario charge on arrival at the station (see below) |
| Charge duration | .20 | `100 × (1 − minutes / 90)`; unknown power: neutral 50; unreachable: 0 |
| Price | .15 | `100 × (1 − EUR/kWh / 0.80)`; unknown price: neutral 50 |

**Reachability:** charge on arrival must stay above the reserve (mean scenario). Not reachable: score 0. Reachable in the mean scenario but not in the pessimistic one: score × 0.75 and a note.

**Arrival floor:** the wanted arrival charge when it is enabled (Trajet tab, Charge), else the reserve + 5 %.

**Need level** (whole trip): "nécessaire" if the configured scenario fails or ends under the wanted arrival charge, "prudent" if the pessimistic one ends under the arrival floor, otherwise "optionnelle" (need sub-score fixed at 10). Otherwise the need score is highest when the charge on arrival is within 25 % above the reserve and decreases to a floor of 30.

**Energy to add:** the missing energy to finish with a 5 % margin, capped at 80 % of the battery (or at the end `y` of the timed charge profile, below); if nothing is missing, a nominal 20 % top-up (never above that cap).

## Recharge plan (`domain/recharge.plan_stops`)

Built only when a recharge is useful (need level "nécessaire" or "prudent"). The reference is the configured scenario; the pessimistic scenario is a safety check.

1. **Finish test:** from a position and a charge, the trip is finished when both scenarios arrive at or above the arrival floor.
2. **Portion score:** each slice ("portion") scores from the charge at its end: 0 at or above the top of the charge window (80 % by default), 100 at the minimum, linear in between.
3. **Station score:** detour, charge duration and price, with the weights above and without the "need" criterion.
4. **Couple:** for each station reachable with at least the reserve + 5 % on arrival (both scenarios), `portion score × station score / 100`, the portion being the slice holding the station. A portion without station scores 0. The best couple (score > 0) is the next stop.
5. **Charge:** the energy added covers the rest of the trip up to the arrival floor (pessimistic energy), or up to the next stop (searched from a charge at the cap), and never exceeds the cap. The duration comes from the timed levels below (best compatible level), else from the station power.
6. **Chaining:** the search restarts from the stop, up to the arrival (6 stops at most). Warnings: "Aucune borne atteignable avec la charge souhaitée sur la suite du trajet.", "Plan de recharge interrompu : trop d'arrêts nécessaires."

Output (`ChargingPlan`): `stops` (`ChargeStop`: station, arrival and departure charge, energy, duration, scores), `covered` (the stops reach the arrival), `final_soc_pct`, `charge_total_min`, `curve`. Limit: the stops are chosen among the candidates whose real detour was computed (25 at most).

## Price priority

OSM `charge` tag > `fee=no` (free) > operator tariff in `tariffs.json` > unknown. Tariffs are matched by case-insensitive substring on the station name, operator, network or brand (longest key wins):

```json
{"operators": {"ionity": {"rapide": 0.69, "normale": 0.45, "any": 0.5}}}
```

## Vehicle charger levels

The vehicle accepts a subset of `dc100` (100 kW DC), `ac22`, `ac11`, `ac7_4`, `ac3_7`, `ac2_3` (kW AC). All levels are selected by default.

- A **fast** station (power >= 50 kW, or CCS/CHAdeMO/Supercharger sockets) needs a DC level; a **normal** one needs an AC level. Unknown type: kept.
- Charge time uses `min(station power, vehicle limit)` where the limit is the highest selected AC or DC level, and a charging efficiency of 0.85. Without data, stations are assumed 50 kW (fast) or 11 kW (normal).
- No level selected (empty list): stations are not filtered and the vehicle limits stay at the `ChargingConfig` defaults (11 kW AC, 100 kW DC).
- Interfaces: Dash dropdown in the **Énergie** tab, `--chargers dc100,ac11` in the CLI.

## Timed charge profile (optional)

The user can time a charge from `x` % to `y` % (default 15 % -> 80 %) for each selected level (Dash, **Énergie** tab, Recharge). The battery-side power of a level is `battery_kWh x (y - x) / 100 / hours`.

- **Station -> level:** the highest timed level whose power does not exceed the station's (assumed 50 kW fast / 11 kW normal when unknown), among levels of the same current (DC for a fast station, AC for a normal one; unknown type: any). No such level: the model above applies.
- **Charge time:** `energy to add / effective power`, without the 0.85 efficiency (the timing already includes it). Without any timing, the model above is unchanged.
- **Cap (`y`):** the energy added never takes the battery above `y`; it replaces the fixed 80 % cap, and the nominal 20 % top-up never exceeds it. If the trip cannot be finished within the cap, the station gets the note « recharge limitée à y % ».
- **Scope:** used only when at least one duration is filled; the durations of unselected levels are ignored.
- **Limit:** a charge starting below `x` or ending above `y` is extrapolated linearly outside the measured window.

## Configuration

Weights and thresholds: key `charging` of the `--config` JSON (`ChargingConfig`). Limits worth knowing: the public OSRM server forces a pause between requests, hence at most 25 detours per search; in the web interfaces the search runs in the background after the first estimate and the page updates by itself.
