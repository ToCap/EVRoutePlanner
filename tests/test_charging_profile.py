"""User-timed charge profile: effective power per level, recharge capped at the window end."""
from dataclasses import replace

import pytest

from ev_trip.application.charging import plan_charging
from ev_trip.domain import charging
from ev_trip.domain.charging import Station, apply_charge_profile, score_station
from ev_trip.domain.config import ChargingConfig
from ev_trip.providers.overpass import OverpassStationSource
from test_charging import FakeClient, element, make_report


def station(power_kw, charge_type):
    return Station("n/1", 48.0, 2.0, name="Borne", power_kw=power_kw, charge_type=charge_type)


def with_profile(report, minutes, window=(15, 80), chargers=None):
    cfg = apply_charge_profile(report.config.charging, report.simulation.vehicle.battery_kwh, window, minutes, chargers)
    return replace(report, config=replace(report.config, charging=cfg))


@pytest.fixture
def low_arrival(monkeypatch):
    """Arrival at 40 % with a huge energy need: the recharge is limited only by the cap."""
    monkeypatch.setattr(charging, "soc_at_km", lambda sim, km: 40.0)
    monkeypatch.setattr(charging, "energy_between", lambda *a, **k: 1e6)


def test_effective_power_comes_from_the_timing():
    cfg = apply_charge_profile(ChargingConfig(), 50.0, (20, 80), {"dc100": 30, "ac11": 120})
    assert cfg.level_kw == {"dc100": 60.0, "ac11": 15.0}  # 50 kWh x 60 % in 30 min / 120 min
    assert cfg.target_max_soc_pct == 80 and cfg.charge_window_pct == (20, 80)


def test_unselected_or_untimed_levels_are_ignored():
    cfg = apply_charge_profile(ChargingConfig(), 50.0, (20, 80), {"dc100": 30, "ac11": 120}, chargers=("dc100",))
    assert list(cfg.level_kw) == ["dc100"]
    base = ChargingConfig()
    assert apply_charge_profile(base, 50.0, (20, 80), {}) is base  # nothing timed: unchanged model


def test_invalid_profile_is_rejected():
    with pytest.raises(ValueError, match="charge_window_pct"):
        ChargingConfig(charge_window_pct=(80, 15))


def test_station_uses_the_highest_timed_level_not_above_its_power():
    cfg = apply_charge_profile(ChargingConfig(), 50.0, (15, 80), {"ac11": 60, "ac7_4": 90, "dc100": 30, "ac22": 45})
    kw = cfg.level_kw
    assert charging._effective_kw(station(22.0, "normale"), cfg) == kw["ac22"]
    assert charging._effective_kw(station(11.0, "normale"), cfg) == kw["ac11"]
    assert charging._effective_kw(station(150.0, "rapide"), cfg) == kw["dc100"]  # AC levels do not apply to a fast station
    assert charging._effective_kw(station(50.0, "rapide"), cfg) is None  # no DC level at or below 50 kW: default model
    assert charging._effective_kw(station(3.0, "normale"), cfg) is None


def test_charge_time_follows_the_timed_power_and_the_recharge_is_capped_at_the_window_end(low_arrival):
    base = make_report(100)
    cap = base.simulation.vehicle.battery_kwh
    sug = score_station(with_profile(base, {"ac22": 40}), station(22.0, "normale"), 50, 1.0, 2.0, 5.0)
    assert sug.reachable and sug.soc_after_pct == pytest.approx(80)  # 40 % -> window end 80 %
    assert sug.add_kwh == pytest.approx((80 - sug.soc_arrival_pct) / 100 * cap)  # up to the window end, no further
    assert sug.charge_min == pytest.approx(sug.add_kwh / (cap * 0.65 / (40 / 60)) * 60)  # 15 -> 80 % in 40 min
    assert "recharge limitée à 80 %" in sug.notes


def test_cap_is_the_window_end_not_the_default(low_arrival):
    base = make_report(100)
    sug = score_station(with_profile(base, {"ac22": 40}, window=(15, 60)), station(22.0, "normale"), 50, 1.0, 2.0, 5.0)
    assert sug.soc_after_pct == pytest.approx(60) and "recharge limitée à 60 %" in sug.notes


def test_without_timing_the_model_and_the_notes_are_unchanged(low_arrival):
    base = make_report(100)
    sug = score_station(base, station(7.0, "normale"), 50, 1.0, 2.0, 5.0)
    cc = base.config.charging
    assert sug.charge_min == pytest.approx(sug.add_kwh / (min(7.0, cc.vehicle_max_ac_kw) * cc.charge_efficiency) * 60)
    assert not any("limitée" in n for n in sug.notes)


def test_plan_charging_applies_the_timed_profile():
    report = make_report(100)
    stations = OverpassStationSource(FakeClient([element(60, id=1, name="Borne A", **{"socket:type2_combo:output": "150 kW"})]))
    plan = plan_charging(report, 5, stations=stations, detour_fn=lambda *a: (1.0, 2.0), sleep=lambda s: None,
                         chargers=("dc100",), charge_window=(15, 80), charge_minutes={"dc100": 30})
    s = plan.suggestions[0]
    kw = report.simulation.vehicle.battery_kwh * 0.65 / 0.5  # 15 -> 80 % in 30 min
    assert s.charge_min == pytest.approx(s.add_kwh / kw * 60)
