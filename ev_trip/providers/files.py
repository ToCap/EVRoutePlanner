"""Reads the JSON configuration and tariff files (I/O outside the domain)."""
from __future__ import annotations

import json
from pathlib import Path

from ev_trip.domain.charging import TariffTable
from ev_trip.domain.config import ConsumptionConfig


def load_config(path: str | Path) -> ConsumptionConfig:
    return ConsumptionConfig.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def load_tariffs(path: str | Path) -> TariffTable:
    return TariffTable.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
