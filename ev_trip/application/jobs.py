"""Task manager: quick trip computation, then refinement and stations in the background."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Callable, Protocol

from ev_trip.application.charging import plan_charging
from ev_trip.application.messages import user_message
from ev_trip.application.trip import add_exit_variants, refine_trip, run_trip, run_trip_profiles
from ev_trip.domain.charging import ChargingPlan, TariffTable
from ev_trip.domain.config import CHARGER_LEVELS, DEFAULT_CHARGERS, DEFAULT_SCENARIO, SCENARIOS, ConsumptionConfig
from ev_trip.domain.models import VehicleParams
from ev_trip.domain.route_profiles import ProfileReports
from ev_trip.domain.trip_report import TripReport

log = logging.getLogger(__name__)
RETRY_DELAYS_S = (2.0, 5.0, 10.0)  # waits between the attempts of the exit search (4 attempts)

# Entry states: refining = task running, done = finished, failed = task error (first-pass result stays available).
REFINING, DONE, FAILED = "refining", "done", "failed"
PROFILES = ("fast", "eco")  # route optimisation: fastest, lowest consumption
STAGE_VARIANTS = "Recherche des sorties d'autoroute…"
STAGE_REFINE = "Affinage des types de route…"
STAGE_STATIONS = "Recherche des bornes…"


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
        retry_delays: tuple[float, ...] = RETRY_DELAYS_S,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        production = run_trip is None and run_profiles is None
        if production:  # production: the fastest route alone, or the candidates of the consumption profile
            run_profiles = run_trip_profiles
        self.variants = variants or (add_exit_variants if production else None)  # exit variants: background task, with retries
        self.retry_delays = retry_delays
        self.sleep = sleep
        self.store = store or MemoryStore()
        self.runner = runner
        self.tariffs = tariffs
        self.run_trip = run_trip or (_default_run_trip if production else None)
        self.run_profiles = run_profiles
        self.refine = refine
        self.plan = plan

    def get(self, key: str) -> TripEntry | None:
        return self.store.get(key)

    def start(self, request: TripRequest) -> tuple[str, TripEntry]:
        """Trip result: the cached one, or a first estimate (background task started if useful)."""
        key = request.key()
        entry = self.store.get(key)
        if entry is not None:
            return key, entry
        eco = request.profile == "eco"
        later = request.eco_exit_variants if eco and self.variants is not None and request.use_overpass else 0  # exits: background task
        base, same, fast_is_eco = None, False, False
        if self.run_profiles is not None and (eco or self.run_trip is None):  # fast: one route when `run_trip` is available
            profiles = self.run_profiles(
                request.origin, request.destination, request.temp_c, request.vehicle(),
                ConsumptionConfig(eco_max_extra_duration_pct=request.eco_extra_pct, eco_exit_variants=0 if later else request.eco_exit_variants,
                                  scenario=request.scenario), use_overpass=request.use_overpass, refine=False,
            )
            report = profiles.eco if eco else profiles.fast
            base, same, fast_is_eco = (profiles.fast if later else None), profiles.same, eco and profiles.same and not later
        else:
            report = self.run_trip(
                request.origin, request.destination, request.temp_c, request.vehicle(),
                use_overpass=request.use_overpass, refine=False,
            )
        report = only_scenario(report, request.scenario)
        base = report if same and base is not None else (only_scenario(base, request.scenario) if base is not None else None)  # same route: same object
        busy = report.refinable or request.wants_charging or later > 0
        entry = TripEntry(
            report, REFINING if busy else DONE, None, False, request.max_detour_km, None, request.chargers,
            (request.charge_from_pct, request.charge_to_pct), request.charge_minutes, 1 if busy else 0, request.scenario,
            STAGE_VARIANTS if later else STAGE_REFINE if report.refinable else (STAGE_STATIONS if request.wants_charging else ""),
            later, base, fast_is_eco,
        )
        self.store.set(key, entry)
        if busy and self.store.acquire(key):  # one task per trip
            self.runner(lambda: self.run_job(key))
        return key, entry

    def _plan(self, entry: TripEntry, report: TripReport) -> tuple[ChargingPlan | None, list[str]]:
        """Stations of the route; (None, errors) if the search fails."""
        # the timed charge profile is passed only when set: other plans keep their usual call
        timed = {"charge_window": entry.charge_window, "charge_minutes": dict(entry.charge_minutes)} if entry.charge_minutes else {}
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
            plan, plan_errors = self._plan(entry, report)
            errors += plan_errors
            if plan is not None:
                changes["charging"] = plan
        self._finish(key, entry, errors, **changes)

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
