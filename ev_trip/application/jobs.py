"""Task manager: quick trip computation, then refinement and stations in the background."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import asdict, dataclass, field, replace
from typing import Callable, Protocol

from ev_trip.application.charging import StationPrefetch, fetch_stations, plan_charging
from ev_trip.application.messages import user_message
from ev_trip.application.trip import add_exit_variants, refine_trip, run_trip, run_trip_profiles
from ev_trip.domain.charging import ChargingPlan, TariffTable
from ev_trip.domain.config import CHARGER_LEVELS, DEFAULT_CHARGERS, DEFAULT_SCENARIO, SCENARIOS, ConsumptionConfig
from ev_trip.domain.models import Route, VehicleParams
from ev_trip.domain.route_profiles import ProfileReports
from ev_trip.domain.simulation import simulate_all
from ev_trip.domain.trip_report import TripReport

log = logging.getLogger(__name__)
RETRY_DELAYS_S = (2.0, 5.0, 10.0)  # waits between the attempts of the exit search (4 attempts)

# Entry states: refining = task running, done = finished, failed = task error (first-pass result stays available).
REFINING, DONE, FAILED = "refining", "done", "failed"
PROFILES = ("fast", "eco")  # route optimisation: fastest, lowest consumption
STAGE_VARIANTS = "Recherche des sorties d'autoroute…"
STAGE_REFINE = "Affinage des types de route…"
STAGE_STATIONS = "Recherche des bornes…"


MAX_WAYPOINTS = 5  # intermediate stops of a trip
ROUTE_CACHE_SIZE = 10  # finished routes (and station searches) kept to recompute a trip with other environment or charge settings


@dataclass(frozen=True)
class TripRequest:
    """Trip parameters; `key()` identifies a result (same request = same key)."""

    origin: str
    destination: str
    temp_c: float
    battery_kwh: float
    start_soc: float
    min_soc: float
    use_overpass: bool = True
    max_detour_km: float = 0.0  # 0 = no station search
    chargers: tuple[str, ...] = DEFAULT_CHARGERS  # charging levels the vehicle accepts (ids of `CHARGER_LEVELS`)
    charge_from_pct: float = 15.0  # timed charge window: start (%)
    charge_to_pct: float = 80.0  # timed charge window: end (%), also the recharge cap when a duration is given
    charge_minutes: tuple[tuple[str, float], ...] = ()  # (level id, minutes to go from -> to) of the selected levels
    battery_health_pct: float = 100.0  # state of health: real capacity = nominal x health; charge levels apply to the real one
    eco_extra_pct: float = 15.0  # consumption profile: accepted duration gap (%) over the fastest route
    eco_exit_variants: int = 2  # consumption profile: motorway exits tried before the fast route's own exit (0-2)
    scenario: str = DEFAULT_SCENARIO  # the one consumption scenario shown and used (optimiste / moyenne / pessimiste)
    profile: str = "fast"  # route optimisation: "fast" (shortest duration) or "eco" (least energy)
    arrival_soc: float | None = None  # wanted charge at arrival (%), None = only the minimum is required
    waypoints: tuple[str, ...] = ()  # intermediate stops, in order (plain passing points)

    def __post_init__(self) -> None:
        known = [level.id for level in CHARGER_LEVELS]
        unknown = [c for c in self.chargers if c not in known]
        if unknown:
            raise ValueError(f"Niveau de recharge inconnu : {', '.join(unknown)}.")
        object.__setattr__(self, "chargers", tuple(c for c in known if c in self.chargers))  # canonical order: same key
        if self.scenario not in SCENARIOS:
            raise ValueError(f"Estimation inconnue : {self.scenario}.")
        if self.profile not in PROFILES:
            raise ValueError(f"Profil inconnu : {self.profile}.")
        if not 0 <= self.eco_exit_variants <= 2:
            raise ValueError("Sorties étudiées : la valeur doit être comprise entre 0 et 2.")
        if self.eco_extra_pct < 0:
            raise ValueError("Écart de durée : la valeur doit être >= 0 %.")
        if self.arrival_soc is not None and not self.min_soc <= self.arrival_soc <= 100:
            raise ValueError("Charge à l'arrivée : la valeur doit être comprise entre le seuil minimum et 100 %.")
        if len(self.waypoints) > MAX_WAYPOINTS or any(not w.strip() for w in self.waypoints):
            raise ValueError(f"Étapes : {MAX_WAYPOINTS} au maximum, chacune renseignée.")
        object.__setattr__(self, "waypoints", tuple(w.strip() for w in self.waypoints))
        if not 0 < self.battery_health_pct <= 100:
            raise ValueError("Santé de la batterie : la valeur doit être > 0 et <= 100 %.")
        timed = dict(self.charge_minutes)
        bad = [c for c in timed if c not in known]
        if bad:
            raise ValueError(f"Niveau de recharge inconnu : {', '.join(bad)}.")
        if not 0 <= self.charge_from_pct < self.charge_to_pct <= 100:
            raise ValueError("Niveau de charge : le minimum doit être inférieur au maximum (entre 0 et 100 %).")
        if any(m <= 0 for m in timed.values()):
            raise ValueError("Durée de recharge : la valeur doit être > 0.")
        object.__setattr__(self, "charge_minutes",  # selected levels only, canonical order: same key
                           tuple((c, float(timed[c])) for c in known if c in timed and c in self.chargers))

    def key(self) -> str:
        return hashlib.sha1(json.dumps(asdict(self), sort_keys=True).encode("utf-8")).hexdigest()

    def route_key(self) -> str:
        """Identifies the route alone: the same key means the same itinerary, whatever the environment or charge settings."""
        parts = (self.origin, self.destination, self.waypoints, self.use_overpass, self.profile, self.eco_extra_pct, self.eco_exit_variants)
        return hashlib.sha1(json.dumps(parts).encode("utf-8")).hexdigest()

    def vehicle(self) -> VehicleParams:
        return VehicleParams(self.battery_kwh * self.battery_health_pct / 100.0, self.start_soc, self.min_soc, self.arrival_soc)  # real capacity

    @property
    def wants_charging(self) -> bool:
        return self.max_detour_km > 0


@dataclass
class TripEntry:
    """Trip state: current report, background task progress, stations found."""

    report: TripReport
    state: str = DONE
    error: str | None = None
    refined: bool = False
    max_detour_km: float = 0.0
    charging: ChargingPlan | None = None
    chargers: tuple[str, ...] = DEFAULT_CHARGERS
    charge_window: tuple[float, float] = (15.0, 80.0)
    charge_minutes: tuple[tuple[str, float], ...] = ()
    pending: int = 0  # running background tasks
    scenario: str = DEFAULT_SCENARIO  # scenario kept in the report
    stage: str = ""  # what the background task is doing now (shown in the busy badge)
    variants: int = 0  # motorway exit variants still to search by the background task (0 = none)
    base: TripReport | None = None  # fast route, kept only until the exit search has run
    fast_is_eco: bool = False  # consumption profile: the fastest route is also the least energy one
    route_key: str = ""  # `TripRequest.route_key()`: entries of the same route share the cached itinerary


def only_scenario(report: TripReport, scenario: str) -> TripReport:
    """Report limited to one consumption scenario (its simulation is also the reference of the charging search)."""
    full = report.simulations if len(report.simulations) > 1 else (report.full_simulations or report.simulations)
    return replace(report, config=replace(report.config, scenario=scenario), simulations={scenario: full[scenario]}, full_simulations=full)


def for_planning(report: TripReport) -> TripReport:
    """Report with every scenario again: the station scoring needs the average and pessimistic simulations."""
    return replace(report, simulations=report.full_simulations) if report.full_simulations else report


class TripStore(Protocol):
    def get(self, key: str) -> TripEntry | None: ...

    def set(self, key: str, entry: TripEntry) -> None: ...

    def acquire(self, key: str) -> bool:
        """Reserves this trip's task: False if it was already started."""


class MemoryStore:
    """In-process memory store (CLI, tests, single-process dashboard)."""

    def __init__(self) -> None:
        self._entries: dict[str, TripEntry] = {}
        self._locks: set[str] = set()
        self._mutex = threading.Lock()

    def get(self, key: str) -> TripEntry | None:
        with self._mutex:
            return self._entries.get(key)

    def set(self, key: str, entry: TripEntry) -> None:
        with self._mutex:
            self._entries[key] = entry

    def acquire(self, key: str) -> bool:
        with self._mutex:
            if key in self._locks:
                return False
            self._locks.add(key)
            return True


def thread_runner(job: Callable[[], None]) -> None:
    """Runs `job` in a daemon thread (replaceable: task queue, immediate execution in tests)."""
    threading.Thread(target=job, daemon=True, name="ev-trip-job").start()


class TripService:
    """Use case "compute a trip": instant first estimate, the rest in the background."""

    def __init__(
        self,
        store: TripStore | None = None,
        runner: Callable[[Callable[[], None]], None] = thread_runner,
        tariffs: Callable[[], TariffTable | None] = lambda: None,
        *,
        run_trip: Callable[..., TripReport] | None = None,
        run_profiles: Callable[..., ProfileReports] | None = None,
        refine: Callable[[TripReport], TripReport] = refine_trip,
        plan: Callable[..., ChargingPlan] = plan_charging,
        variants: Callable[..., ProfileReports] | None = None,
        prefetch: Callable[..., StationPrefetch] | None = None,
        retry_delays: tuple[float, ...] = RETRY_DELAYS_S,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        production = run_trip is None and run_profiles is None
        if production:  # production: the fastest route alone, or the candidates of the consumption profile
            run_profiles = run_trip_profiles
        self.variants = variants or (add_exit_variants if production else None)  # exit variants: background task, with retries
        self.prefetch = prefetch or (fetch_stations if production else None)  # corridor and detours, fetched while the trip is refined
        self.retry_delays = retry_delays
        self.sleep = sleep
        self.store = store or MemoryStore()
        self.runner = runner
        self.tariffs = tariffs
        self.run_trip = run_trip or (_default_run_trip if production else None)
        self.run_profiles = run_profiles
        self.refine = refine
        self.plan = plan
        self._routes: OrderedDict[str, TripEntry] = OrderedDict()  # route key -> finished entry (its route and slices are reused)
        self._stations: OrderedDict[tuple[str, float], StationPrefetch] = OrderedDict()  # (route key, max detour) -> stations
        self._cache_lock = threading.Lock()

    def get(self, key: str) -> TripEntry | None:
        return self.store.get(key)

    def start(self, request: TripRequest) -> tuple[str, TripEntry]:
        """Trip result: the cached one, or a first estimate (background task started if useful)."""
        key = request.key()
        entry = self.store.get(key)
        if entry is not None:
            return key, entry
        cached = self._cached_route(request.route_key())
        if cached is not None:  # same itinerary, other environment or charge settings: simulate again, no network
            return key, self._start_from_route(key, request, cached)
        eco = request.profile == "eco"
        later = request.eco_exit_variants if eco and self.variants is not None and request.use_overpass and not request.waypoints else 0  # exits: background task
        via = {"via": request.waypoints} if request.waypoints else {}  # passed only with stops: plain runners keep their call
        base, same, fast_is_eco = None, False, False
        if self.run_profiles is not None and (eco or self.run_trip is None):  # fast: one route when `run_trip` is available
            profiles = self.run_profiles(
                request.origin, request.destination, request.temp_c, request.vehicle(),
                ConsumptionConfig(eco_max_extra_duration_pct=request.eco_extra_pct, eco_exit_variants=0 if later else request.eco_exit_variants,
                                  scenario=request.scenario), use_overpass=request.use_overpass, refine=False, **via,
            )
            report = profiles.eco if eco else profiles.fast
            base, same, fast_is_eco = (profiles.fast if later else None), profiles.same, eco and profiles.same and not later and not request.waypoints
        else:
            report = self.run_trip(
                request.origin, request.destination, request.temp_c, request.vehicle(),
                use_overpass=request.use_overpass, refine=False, **via,
            )
        report = only_scenario(report, request.scenario)
        base = report if same and base is not None else (only_scenario(base, request.scenario) if base is not None else None)  # same route: same object
        busy = report.refinable or request.wants_charging or later > 0
        entry = TripEntry(
            report, REFINING if busy else DONE, None, False, request.max_detour_km, None, request.chargers,
            (request.charge_from_pct, request.charge_to_pct), request.charge_minutes, 1 if busy else 0, request.scenario,
            STAGE_VARIANTS if later else STAGE_REFINE if report.refinable else (STAGE_STATIONS if request.wants_charging else ""),
            later, base, fast_is_eco, request.route_key(),
        )
        self.store.set(key, entry)
        if busy and self.store.acquire(key):  # one task per trip
            self.runner(lambda: self.run_job(key))
        else:
            self._remember(key)
        return key, entry

    def _cached_route(self, route_key: str) -> TripEntry | None:
        with self._cache_lock:
            entry = self._routes.get(route_key)
            if entry is not None:
                self._routes.move_to_end(route_key)
            return entry

    def _remember(self, key: str) -> None:
        """Keeps a finished entry (final route, no pending task) for the next trip on the same itinerary."""
        entry = self.store.get(key)
        if entry is None or entry.state != DONE or entry.pending or entry.variants or entry.report.refinable or not entry.route_key:
            return
        with self._cache_lock:
            self._routes[entry.route_key] = entry
            self._routes.move_to_end(entry.route_key)
            while len(self._routes) > ROUTE_CACHE_SIZE:
                self._routes.popitem(last=False)

    def _start_from_route(self, key: str, request: TripRequest, cached: TripEntry) -> TripEntry:
        """Entry on the cached itinerary: new simulation with the request's vehicle, then the plan only (stations come from the cache)."""
        base = cached.report
        sims = simulate_all(base.slices, request.vehicle(), request.temp_c, base.config)
        report = only_scenario(replace(base, simulations=sims, full_simulations=None), request.scenario)
        busy = request.wants_charging
        entry = TripEntry(
            report, REFINING if busy else DONE, None, cached.refined, request.max_detour_km, None, request.chargers,
            (request.charge_from_pct, request.charge_to_pct), request.charge_minutes, 1 if busy else 0, request.scenario,
            STAGE_STATIONS if busy else "", 0, None, cached.fast_is_eco, request.route_key(),
        )
        self.store.set(key, entry)
        if busy and self.store.acquire(key):
            self.runner(lambda: self.run_job(key))
        else:
            self._remember(key)
        return entry

    def _start_prefetch(self, entry: TripEntry, route: Route, cfg) -> Future | None:
        """Starts the network part of the station search in its own thread (it depends on the route geometry only)."""
        if self.prefetch is None or entry.max_detour_km <= 0:
            return None
        future: Future = Future()
        cache_key = (entry.route_key, entry.max_detour_km)
        with self._cache_lock:
            hit = self._stations.get(cache_key) if entry.route_key else None
        if hit is not None:  # same route and detour: no network call
            future.set_result(hit)
            return future

        def work() -> None:
            try:
                result = self.prefetch(route, entry.max_detour_km, cfg)
                if entry.route_key and result.error is None and result.osrm_error is None:  # failures are never kept
                    with self._cache_lock:
                        self._stations[cache_key] = result
                        while len(self._stations) > ROUTE_CACHE_SIZE:
                            self._stations.popitem(last=False)
                future.set_result(result)
            except Exception as exc:  # the planner then searches by itself
                log.exception("Préchargement des bornes impossible")
                future.set_exception(exc)

        threading.Thread(target=work, daemon=True, name="ev-trip-stations").start()
        return future

    def _plan(self, entry: TripEntry, report: TripReport, fetched: Future | None = None) -> tuple[ChargingPlan | None, list[str]]:
        """Stations of the route; (None, errors) if the search fails."""
        # the timed charge profile is passed only when set: other plans keep their usual call
        timed = {"charge_window": entry.charge_window, "charge_minutes": dict(entry.charge_minutes)} if entry.charge_minutes else {}
        if fetched is not None and fetched.exception() is None:  # waits for the prefetch thread
            timed["prefetch"] = fetched.result()
        try:
            return self.plan(for_planning(report), entry.max_detour_km, tariffs=self.tariffs(), chargers=entry.chargers, **timed), []
        except Exception as exc:
            log.exception("Recherche des bornes impossible")
            return None, [user_message(exc)]

    def _finish(self, key: str, entry: TripEntry, errors: list[str], **changes) -> None:
        """Publishes a task result on the latest entry; the state stays `refining` while another task runs."""
        latest = self.store.get(key) or entry
        pending = max(latest.pending - 1, 0)
        state = REFINING if pending else (FAILED if errors else DONE)
        stage = latest.stage if pending else ""  # cleared when the last task ends
        self.store.set(key, replace(latest, state=state, error="; ".join(errors) or None, pending=pending, stage=stage, **changes))

    def _stage(self, key: str, entry: TripEntry, text: str) -> None:
        self.store.set(key, replace(self.store.get(key) or entry, stage=text))

    def run_job(self, key: str) -> None:
        """Background task: motorway exits (consumption profile), route-type refinement (if useful), then charging stations."""
        entry = self.store.get(key)
        if entry is None:
            return
        report, refined, errors, note, fast_is_eco = entry.report, entry.refined, [], None, entry.fast_is_eco
        if entry.variants:  # exit variants first: they may change the least-energy route
            self._stage(key, entry, STAGE_VARIANTS)
            found, note = self._search_variants(ProfileReports(entry.base or report, report), entry.variants)
            report, fast_is_eco = only_scenario(found.eco, entry.scenario), found.same
            self.store.set(key, replace(self.store.get(key) or entry, report=report, variants=0, base=None, fast_is_eco=fast_is_eco))
        fetched = self._start_prefetch(entry, report.route, report.config.charging)  # stations: in parallel with the refinement
        try:
            if report.refinable:
                self._stage(key, entry, STAGE_REFINE)
                report = only_scenario(self.refine(report), entry.scenario)
                refined = True
        except Exception as exc:  # the task must never stop without publishing its state
            log.exception("Affinage du trajet impossible")
            errors.append(user_message(exc))
        if note:  # added after the refinement, which rebuilds the warnings
            report.warnings.append(note)
        changes: dict = {"report": report, "refined": refined}
        if entry.max_detour_km > 0:
            self._stage(key, entry, STAGE_STATIONS)
            plan, plan_errors = self._plan(entry, report, fetched)
            errors += plan_errors
            if plan is not None:
                changes["charging"] = plan
        self._finish(key, entry, errors, **changes)
        if not errors and not note:  # a failed exit search is retried by the next computation, not cached
            self._remember(key)

    def _search_variants(self, profiles: ProfileReports, k: int) -> tuple[ProfileReports, str | None]:
        """Exit variants, retried after each failure; (profiles, warning) once every attempt has failed."""
        for attempt in range(len(self.retry_delays) + 1):
            try:
                return self.variants(profiles, k), None
            except Exception as exc:
                log.warning("Recherche des sorties d'autoroute : échec %d/%d : %s", attempt + 1, len(self.retry_delays) + 1, exc)
                last = exc
                if attempt < len(self.retry_delays):
                    self.sleep(self.retry_delays[attempt])
        return profiles, f"Variantes de sortie non étudiées ({user_message(last)})."


def _default_run_trip(*args, **kwargs) -> TripReport:
    return run_trip(*args, **kwargs)
