"""Route profiles: fast (shortest duration) and consumption (lowest energy within a duration tolerance); pure selection logic."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Sequence, TypeVar

T = TypeVar("T")
SAME_ROUTE_TOLERANCE = 0.005  # relative distance and duration gap under which two routes are the same


@dataclass(frozen=True)
class ProfileReports(Generic[T]):
    """One result per profile; `same` when both profiles use the same route."""

    fast: T
    eco: T

    @property
    def same(self) -> bool:
        return self.fast is self.eco


def distinct_indices(metrics: Sequence[tuple[float, float]], tol: float = SAME_ROUTE_TOLERANCE) -> list[int]:
    """Indices of the routes (distance km, duration min) not within `tol` of an earlier kept one."""
    kept: list[int] = []
    for i, (km, minutes) in enumerate(metrics):
        if not any(abs(km - metrics[k][0]) <= tol * metrics[k][0] and abs(minutes - metrics[k][1]) <= tol * metrics[k][1] for k in kept):
            kept.append(i)
    return kept


def choose_profiles(candidates: Sequence[tuple[float, float]], max_extra_pct: float) -> tuple[int, int]:
    """(fast, eco) indices from (duration min, energy kWh): eco = least energy among routes <= fast duration x (1 + pct / 100)."""
    if not candidates:
        raise ValueError("Aucun itinéraire à comparer.")
    fast = min(range(len(candidates)), key=lambda i: (candidates[i][0], candidates[i][1]))
    limit = candidates[fast][0] * (1.0 + max_extra_pct / 100.0)
    eco = min((i for i in range(len(candidates)) if candidates[i][0] <= limit), key=lambda i: (candidates[i][1], candidates[i][0]))
    return fast, eco
