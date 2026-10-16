"""Trip storage in the Django cache (implements `TripStore`); with several processes, configure a shared CACHES."""
from __future__ import annotations

from django.core.cache import cache

from ev_trip.application.jobs import TripEntry


class DjangoCacheStore:
    def __init__(self, prefix: str = "trip:", ttl_s: int = 3600) -> None:
        self.prefix, self.ttl_s = prefix, ttl_s

    def get(self, key: str) -> TripEntry | None:
        return cache.get(self.prefix + key)

    def set(self, key: str, entry: TripEntry) -> None:
        cache.set(self.prefix + key, entry, self.ttl_s)

    def acquire(self, key: str) -> bool:
        return cache.add(f"{self.prefix}{key}:lock", 1, self.ttl_s)
