# Route computation

How the route of a trip is found, compared and chosen. The road types and the energy of the chosen route are in [calcul.md](calcul.md).

## Flow

1. **Geocoding** (Nominatim) of the origin and the destination.
2. **Candidates** (OSRM): the fastest route and its alternatives (`osrm_routes`, up to 3). OSRM only keeps alternatives clearly different from the fastest one. With intermediate stops there is a single route (see below).
3. **First estimate** of each candidate (one Overpass point per road section, see [calcul.md](calcul.md)).
4. **Choice** of the route shown, from the *Optimisation* parameter (below).
5. **Background task**: motorway exit variants (consumption only), refinement of the road types, then the station search and the recharge plan ([bornes.md](bornes.md)). The busy badge shows the current step.

## Intermediate stops

The *Itinéraire* accordion (Trajet tab) lets the user add up to 5 stops (`MAX_WAYPOINTS`): "+ Ajouter une étape" inserts a field just before the destination, and the ✕ at the right of a stop removes it. Stops cannot be reordered.

- A stop is a plain passing point: no duration, no recharge. The station search and the recharge plan work on the whole trip.
- Each stop is geocoded in order (`locate_via`) and sent to OSRM between the origin and the destination (`osrm_routes(..., via=...)`). An empty field is ignored.
- OSRM offers no alternative route with more than two points, so there is **one route**: the exit variants are skipped, the request profile is forced to `fast` and the *Optimisation* dropdown is greyed out.
- The report keeps the stops (`TripReport.via`); the map shows them as numbered blue markers.
- The shared link holds them in `via=Rouen|Lisieux`. They are part of the result key.
- Possible evolution: optimise leg by leg (alternatives and exit variants for each leg, then assemble), at the cost of more OSRM and Overpass requests.

## Optimisation

One route is shown; the *Optimisation* dropdown (Trajet tab, Itinéraire) selects it:

| Value | Route |
|---|---|
| Vitesse (`fast`, default) | shortest duration; no other candidate is compared |
| Consommation (`eco`) | least energy (selected scenario, whole route, independent of the battery) among the routes at most `eco_max_extra_duration_pct` (15 %) slower than the fastest |

- The profile is part of the result key and of the shared link (`profile=`).
- Near-identical candidates (distance and duration within 0.5 %) are merged before the choice.
- When the fastest route is also the least energy one, an info message says so.
- *Configuration* holds the accepted duration gap (`eco_extra`, 15 %) and the number of motorway exits tried (`eco_exit`, 0 to 2, default 2).
- The candidates (distance, duration, kWh, choice) are logged at INFO level.

## Motorway exit variants

OSRM alternatives never leave the motorway earlier than the fastest route does. For the consumption profile, `exit_variants` builds such routes:

1. The last motorway stretch of the fastest route is found from its steps (`ref` A, AP, E or M followed by a number; a non-motorway gap shorter than 3 km does not end the stretch).
2. Only its last 30 km are searched for exits (Overpass `highway=motorway_junction`, on the route within 150 m, at least 1 km before the fast route's own exit).
3. A variant follows the fast route up to the exit, then an OSRM route with `exclude=motorway` to the destination. Variants more than 30 % longer than the fastest route are dropped.
4. Cost: 1 Overpass request, then 1 OSRM request per variant.

The search runs in the background task after the first estimate, so the result appears at once. Each failure is retried after 2, 5 and 10 s. If it still fails, or without Overpass, the first result is kept and a short message says why ("Variantes de sortie non étudiées (...)").

Limit: the public OSRM demo server may refuse `exclude=motorway` (HTTP 400). The variants are then not studied.

## Failures

- Geocoding failure: a typo gives "Adresse introuvable : « X » (départ|étape n|arrivée). Vérifiez l'orthographe."; an outage gives "Impossible de localiser le départ|l'étape n|l'arrivée : problème de connexion|service indisponible. Réessayez dans un instant." (no address). Routing failure: a short cause. Both are shown as error notifications, no result.
- Overpass failure: the road types fall back to the OSRM average speed and a warning is shown (see [calcul.md](calcul.md)).
- User-facing messages give the impact and a short cause, never a URL or a host; the full error goes to the application log.
