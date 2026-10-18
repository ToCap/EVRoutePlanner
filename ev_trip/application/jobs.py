"""Task manager: quick trip computation, then refinement and stations in the background."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from typing import Callable, Protocol

from ev_trip.application.charging import plan_charging
from ev_trip.application.trip import refine_trip, run_trip
from ev_trip.domain.charging import ChargingPlan, TariffTable
from ev_trip.domain.config import CHARGER_LEVELS, DEFAULT_CHARGERS
from ev_trip.domain.models import VehicleParams
from ev_trip.domain.trip_report import TripReport

log = logging.getLogger(__name__)

# Entry states: refining = task running, done = finished, failed = task error (first-pass result stays available).
REFINING, DONE, FAILED = "refining", "done", "failed"


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

    def __post_init__(self) -> None:
        known = [level.id for level in CHARGER_LEVELS]
        unknown = [c for c in self.chargers if c not in known]
        if unknown:
            raise ValueError(f"Niveau de recharge inconnu : {', '.join(unknown)}.")
        object.__setattr__(self, "chargers", tuple(c for c in known if c in self.chargers))  # canonical order: same key
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
        return VehicleParams(self.battery_kwh * self.battery_health_pct / 100.0, self.start_soc, self.min_soc)  # real capacity

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
        run_trip: Callable[..., TripReport] = run_trip,
        refine: Callable[[TripReport], TripReport] = refine_trip,
        plan: Callable[..., ChargingPlan] = plan_charging,
    ) -> None:
        self.store = store or MemoryStore()
        self.runner = runner
        self.tariffs = tariffs
        self.run_trip = run_trip
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
        report = self.run_trip(
            request.origin, request.destination, request.temp_c, request.vehicle(),
            use_overpass=request.use_overpass, refine=False,
        )
        busy = report.refinable or request.wants_charging
        entry = TripEntry(
            report, REFINING if busy else DONE, None, False, request.max_detour_km, None, request.chargers,
            (request.charge_from_pct, request.charge_to_pct), request.charge_minutes,
        )
        self.store.set(key, entry)
        if busy and self.store.acquire(key):  # one task per trip
            self.runner(lambda: self.run_job(key))
        return key, entry

    def run_job(self, key: str) -> None:
        """Background task: route-type refinement (if useful), then charging stations (if requested)."""
        entry = self.store.get(key)
        if entry is None:
            return
        report, refined, charging = entry.report, entry.refined, entry.charging
        errors: list[str] = []
        try:
            if report.refinable:
                report = self.refine(report)
                refined = True
        except Exception as exc:  # the task must never stop without publishing its state
            log.exception("Affinage du trajet impossible")
            errors.append(f"affinage : {exc}")
        if entry.max_detour_km > 0:
            # the timed charge profile is passed only when set: other plans keep their usual call
            timed = {"charge_window": entry.charge_window, "charge_minutes": dict(entry.charge_minutes)} if entry.charge_minutes else {}
            try:
                charging = self.plan(report, entry.max_detour_km, tariffs=self.tariffs(), chargers=entry.chargers, **timed)
            except Exception as exc:
                log.exception("Recherche des bornes impossible")
                errors.append(f"bornes : {exc}")
        self.store.set(
            key,
            TripEntry(
                report, FAILED if errors else DONE, "; ".join(errors) or None, refined,
                entry.max_detour_km, charging, entry.chargers, entry.charge_window, entry.charge_minutes,
            ),
        )
