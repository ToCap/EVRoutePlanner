# Dash interface (`interfaces/dash_ui`)

Run: `python -m interfaces.dash_ui` from the project root (same venv as the project). Stack: Dash 4, dash-bootstrap-components 2, dash-leaflet 1.1.
The UI contains no business logic: it translates components to `TripRequest` and `TripService` results to components.

## Modules

| File | Role |
|---|---|
| `app.py` | `create_app(service)`: Dash app, `MemoryStore` service (single process only), env settings (`EV_TRIP_TILE_URL`, `EV_TRIP_TARIFFS`, `EV_TRIP_DEBUG`) |
| `layout.py` | Static layout: sidebar, map, results panel, styles of busy indicators |
| `callbacks.py` | `register(app, service)`: plain handlers (`compute`, `refresh`, `map_bounds`) wired to Dash callbacks |
| `components.py` | Pure builders: route and marker layers, banners, scenario cards, chart, detail and station tables |
| `params.py` | Form values <-> `TripRequest` <-> query string; `FieldError` (fields to highlight) |

## Layout

Flex row: `sidebar` (`dbc.Collapse`, `dimension="width"`, width `min(400px, 90vw)`) | main column (map 2/3, results 1/3).
- The sidebar pushes the main column: map and chart are resized by a clientside callback that dispatches `resize` events (50/200/400 ms).
- ✕ in the sidebar collapses it; ☰ (top left of the map) toggles it. It never disappears after a computation.
- Sidebar tabs: **Trajet** (accordion, `always_open`, both items open at load: Itinéraire and Charge: origin, destination, temperature, start and minimum charge sliders), **Bornes** (max detour), **Énergie** (accordion, `always_open`: Batterie and Recharge open at load, Consommation collapsed; Batterie holds the nominal capacity and the "Santé de la batterie" slider (50-100 %, default 100); Recharge holds the "Niveau de charge" range slider, the charger multi-select ("Type de recharge") and one duration input per level), **Configuration** (Overpass switch).
- Footer shared by all tabs: error area + **Calculer** button, pinned below the tabs; only the tabs area (`side-scroll`) scrolls.
- Results: one block of tabs, **Trajet calculé** first (summary line `trip-summary`, then the three scenario cards side by side), Charge restante (chart), Détail par tranche, Bornes de recharge. Map: zoom control top right, spinner centered, "Affinage en cours…" badge bottom left.

## Behaviors

- **Compute**: the button triggers `on_compute`; busy indicators (`running=`: disabled button + green spinner) are shown only while this callback runs, never at page load.
- **Validation**: invalid or missing fields get a red outline (`invalid=True`, via `FieldError.fields`), with no alert; editing a field clears its outline. Other errors (geocoding, routing) show a `dbc.Alert` in the footer.
- **Notifications**: info, success and warning messages (first estimate, search in progress, refined result, warnings) are toasts at the top right of the map; info/success close after 4 s, warnings and errors stay. The 🔔 badge (map, top left, hidden when empty) opens the last 3, kept by importance then recency (`merge_notifications`).
- **Route profiles**: two routes (fast, consumption; see [calcul.md](calcul.md)). Buttons *Rapide / Consommation* above the results tabs show each route's duration and energy and switch every tab and the map (the other route is grey and dashed, and the "Charge restante" chart adds its mean curve in grey). When both profiles are the same route the buttons give way to a note. The profile is in the shared link (`profile=`), not in the result key; the stations of the profile not shown first are searched on first selection. *Configuration* holds the accepted duration gap (`eco_extra`, 15 %).
- **Interrupted trip**: the red alert is shown in the "Trajet calculé" tab and floats at the bottom of the map (`map-alert`).
- **Result**: the first estimate appears immediately; a 3 s poll (`dcc.Interval`) refreshes the panels while the job refines the trip and searches stations, then stops. After each new `trip-key` the map fits the route (`boundsOptions` padding keeps it clear of the controls).
- **Shared link**: the query string holds all form values (`to_query` / `from_query`). Page load or URL change runs `on_url`: it restores the fields and starts the trip, without busy indicators.
- **Map markers**: start/end markers differ from station markers (diamond with ⚡, `dl.DivMarker`, colored by score).
- **Timed charge**: `charge-window` (RangeSlider, default 15-80 %) and one optional minutes input per level (`charge-min-<level id>`) give the time to go from x % to y % (see [bornes.md](bornes.md)). `on_chargers` greys out the input of every unselected level, and its value is ignored. Form keys: `charge_window`, `charge_min_<level id>`; URL: `charge_from`, `charge_to`, `charge_minutes=dc100:30,ac11:120`.
- **Battery health**: the `battery-health` slider gives the state of health; the simulated capacity is `nominal x health`, and the charge levels (start, reserve) apply to that real capacity (see [calcul.md](calcul.md)). Form key `battery_health_pct`, URL `battery_health`.
- **Chargers**: the multi-select (`dcc.Dropdown(multi=True)`) lists `CHARGER_LEVELS` and may be emptied (no filtering); selected levels filter stations and cap the vehicle charge power (see [bornes.md](bornes.md)).

## Pitfalls (Dash 4)

- A callback with `running=` also fires on initial/URL calls: keep two separate callbacks (`on_compute` for the button, `on_url` for the URL), otherwise the spinner is active at load.
- Several callbacks write the same outputs: use `allow_duplicate=True`, and `prevent_initial_call="initial_duplicate"` for the URL callback.
- `dbc.Collapse` needs `dimension="width"` and a fixed-width inner div, or the content reflows while animating.
- Leaflet and Plotly do not detect container size changes: dispatch a window `resize` event after the sidebar toggles.
- The service state lives in memory: run one process (no multi-worker server) or provide a shared `TripStore`.
- Adding a field: update `FIELD_IDS`, `FIELD_KEYS`, `DEFAULTS`, `request_from_values`, `to_query` / `from_query` and, if it can be invalid, `INVALIDABLE`.

## Tests

`tests/test_dashboard.py` (handlers, layout, markers, params; no browser). Visual checks need a browser; tiles are not reachable offline, so the map stays gray there.
