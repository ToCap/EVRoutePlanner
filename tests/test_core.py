import pytest

from ev_trip.config import SCENARIOS, ConsumptionConfig
from ev_trip.consumption import consumption_kwh_per_100km
from ev_trip.geo import haversine_km, resample
from ev_trip.models import MicroSegment, Point, Route, Slice, VehicleParams
from ev_trip.simulation import simulate, simulate_all
from ev_trip.slicing import build_slices, smooth_types

CFG = ConsumptionConfig()


# --- température -----------------------------------------------------------
@pytest.mark.parametrize(
    "t, expected",
    [(-10, 0.35), (-5, 0.25), (-0.1, 0.25), (0, 0.15), (9.9, 0.15), (10, 0.08),
     (15, 0.0), (20, 0.0), (24.9, 0.0), (25, 0.05), (35, 0.05)],
)
def test_temperature_surcharge(t, expected):
    assert CFG.temperature_surcharge(t) == expected


def test_consumption_at_20c_is_base_value():
    assert consumption_kwh_per_100km("autoroute", 20, CFG) == pytest.approx(19.5)
    assert consumption_kwh_per_100km("ville", 5, CFG) == pytest.approx(12.5 * 1.15)


# --- scénarios optimiste / moyenne / pessimiste -------------------------------------
@pytest.mark.parametrize(
    "road, low, high",
    [("ville", 11.0, 14.0), ("route", 14.0, 15.6), ("autoroute", 18.0, 21.0)],
)
def test_scenarios_use_low_mean_high_of_each_range(road, low, high):
    assert consumption_kwh_per_100km(road, 20, CFG, "optimiste") == pytest.approx(low)
    assert consumption_kwh_per_100km(road, 20, CFG, "pessimiste") == pytest.approx(high)
    assert consumption_kwh_per_100km(road, 20, CFG, "moyenne") == pytest.approx((low + high) / 2)
    assert consumption_kwh_per_100km(road, 20, CFG) == pytest.approx((low + high) / 2)  # défaut = moyenne


def test_temperature_applies_to_every_scenario():
    for name in SCENARIOS:
        assert consumption_kwh_per_100km("route", -10, CFG, name) == pytest.approx(
            CFG.base_kwh_per_100km("route", name) * 1.35)


def test_unknown_scenario_is_rejected():
    with pytest.raises(ValueError):
        CFG.base_kwh_per_100km("ville", "tres-optimiste")


def test_range_validation_and_json_merge(tmp_path):
    with pytest.raises(ValueError):
        ConsumptionConfig(consumption_range_kwh_per_100km={
            "ville": (14, 11), "route": (14, 15.6), "autoroute": (18, 21)})
    with pytest.raises(ValueError):
        ConsumptionConfig(consumption_range_kwh_per_100km={"ville": (11, 14)})
    f = tmp_path / "c.json"
    f.write_text('{"consumption_range_kwh_per_100km": {"ville": [10, 12]}}')
    cfg = ConsumptionConfig.from_json(f)
    assert cfg.consumption_range_kwh_per_100km["ville"] == (10.0, 12.0)
    assert cfg.consumption_range_kwh_per_100km["autoroute"] == (18.0, 21.0)  # valeurs par défaut conservées


def test_simulate_all_orders_scenarios_and_stops_earlier_when_pessimistic():
    veh = VehicleParams(30, 100, 20)
    sims = simulate_all(ten_km_slices(40, "autoroute"), veh, 20, CFG)
    assert tuple(sims) == SCENARIOS
    assert [sims[s].scenario for s in SCENARIOS] == list(SCENARIOS)
    # 24 kWh utilisables : 18 / 19.5 / 21 kWh/100 km -> 133.3 / 123.1 / 114.3 km
    assert sims["optimiste"].total_km == pytest.approx(24 / 18 * 100)
    assert sims["moyenne"].total_km == pytest.approx(24 / 19.5 * 100)
    assert sims["pessimiste"].total_km == pytest.approx(24 / 21 * 100)
    assert sims["optimiste"].total_km > sims["moyenne"].total_km > sims["pessimiste"].total_km
    assert all(s.trip_km == 400 and not s.completed for s in sims.values())


def test_scenarios_can_differ_on_completion():
    # 50 km d'autoroute : 9 / 9.75 / 10.5 kWh ; 12 kWh, départ 100 %, réserve 10 % (1.2 kWh) -> 10.8 utilisables
    veh = VehicleParams(12, 100, 10)
    sims = simulate_all(ten_km_slices(5, "autoroute"), veh, 20, CFG)
    assert all(s.completed for s in sims.values())
    sims = simulate_all(ten_km_slices(5, "autoroute"), VehicleParams(12, 100, 20), 20, CFG)  # 9.6 utilisables
    assert sims["optimiste"].completed and not sims["moyenne"].completed and not sims["pessimiste"].completed


# --- slicing -----------------------------------------------------------------
def segs(*items):
    out, pos = [], 0.0
    for length, rtype in items:
        out.append(MicroSegment(pos, pos + length, rtype))
        pos += length
    return out


def test_long_uniform_stretch_is_cut_every_10km():
    sl = build_slices(segs(*[(0.5, "route")] * 50))  # 25 km
    assert [round(s.length_km, 6) for s in sl] == [10, 10, 5]
    assert sl[0].start_km == 0 and sl[-1].end_km == pytest.approx(25)


def test_slice_ends_early_when_road_type_changes():
    sl = build_slices(segs(*([(0.5, "ville")] * 14 + [(0.5, "autoroute")] * 30)))  # 7 km ville, 15 km autoroute
    assert [(s.road_type, round(s.length_km, 6)) for s in sl] == [
        ("ville", 7), ("autoroute", 10), ("autoroute", 5)]
    assert [s.index for s in sl] == [1, 2, 3]


def test_slices_are_contiguous_and_cover_everything():
    sl = build_slices(segs((3.3, "ville"), (12.2, "route"), (0.7, "ville"), (40.1, "autoroute")))
    assert sl[0].start_km == 0
    for a, b in zip(sl, sl[1:]):
        assert b.start_km == pytest.approx(a.end_km)
    assert sl[-1].end_km == pytest.approx(56.3)
    assert all(s.length_km <= 10 + 1e-9 for s in sl)


def test_smoothing_absorbs_single_sample_blip():
    raw = segs(*([(0.5, "autoroute")] * 6 + [(0.5, "ville")] + [(0.5, "autoroute")] * 6))
    smoothed = smooth_types(raw, 1.0)
    assert {s.road_type for s in smoothed} == {"autoroute"}
    assert smoothed[-1].end_km == pytest.approx(6.5)


def test_smoothing_keeps_real_change_and_can_be_disabled():
    raw = segs((5, "autoroute"), (2, "ville"), (5, "autoroute"))
    assert len(smooth_types(raw, 1.0)) == 3
    blip = segs((5, "route"), (0.5, "ville"), (5, "route"))
    assert len(smooth_types(blip, 0)) == 3


# --- simulation ----------------------------------------------------------------
def ten_km_slices(n, rtype="ville"):
    return [Slice(i + 1, i * 10, (i + 1) * 10, rtype) for i in range(n)]


def test_soc_decreases_with_expected_energy():
    veh = VehicleParams(battery_kwh=50, start_soc_pct=100, min_soc_pct=10)
    res = simulate(ten_km_slices(10, "autoroute"), veh, 20, CFG)  # 100 km a 19.5
    assert res.total_kwh == pytest.approx(19.5)
    assert res.final_soc_pct == pytest.approx(100 - 19.5 / 50 * 100)
    assert res.alert is None and res.completed
    assert res.total_km == res.trip_km == 100
    assert res.results[0].soc_pct == pytest.approx(100 - 1.95 / 50 * 100)


def test_trip_stops_exactly_at_reserve():
    # 10 kWh, seuil 50 % -> 5 kWh utilisables ; ville 12.5 kWh/100 km (moyenne) -> arrêt au km 40
    veh = VehicleParams(10, 100, 50)
    res = simulate(ten_km_slices(10), veh, 20, CFG)
    stop_km = 5 / 12.5 * 100
    assert not res.completed
    assert res.alert is not None and res.alert.km == pytest.approx(stop_km)
    assert res.alert.slice_index == 5  # 4 tranches épuisent exactement l'énergie utilisable
    assert res.total_km == pytest.approx(stop_km) and res.trip_km == 100
    assert len(res.results) == 4  # rien n'est simulé au-delà de la réserve
    assert res.results[-1].slice.end_km == pytest.approx(stop_km)
    assert res.final_soc_pct == pytest.approx(50)
    assert res.total_kwh == pytest.approx(5)


def test_stop_inside_a_slice_truncates_it():
    # 10 kWh, réserve 50 % ; ville moyenne 12.5 -> 5 kWh = 40 km ; tranches de 15 km : 15 + 15 + 10 tronquée
    slices = [Slice(i + 1, i * 15, (i + 1) * 15, "ville") for i in range(5)]
    res = simulate(slices, VehicleParams(10, 100, 50), 20, CFG)
    assert [round(r.slice.end_km, 6) for r in res.results] == [15, 30, 40]
    assert res.alert.slice_index == 3 and res.alert.km == pytest.approx(40)


@pytest.mark.parametrize("min_soc", [0, 10, 35, 100])
def test_charge_never_goes_below_reserve_nor_zero(min_soc):
    res = simulate(ten_km_slices(40, "autoroute"), VehicleParams(30, 100, min_soc), -10, CFG)
    assert all(r.soc_pct >= min_soc - 1e-6 and r.soc_pct >= 0 for r in res.results)
    assert res.final_soc_pct >= min_soc - 1e-6


def test_zero_reserve_stops_when_battery_is_empty():
    res = simulate(ten_km_slices(30, "autoroute"), VehicleParams(20, 100, 0), 20, CFG)
    assert not res.completed and res.final_soc_pct == pytest.approx(0, abs=1e-6)
    assert res.total_km == pytest.approx(20 / 19.5 * 100)
    assert "Batterie vide" in res.alert.message


def test_reserve_reached_exactly_at_arrival_completes_trip():
    # 100 km a 12.5 kWh/100 km = 12.5 kWh ; 20 kWh de batterie, depart 100 % -> arrivee 37.5 %
    res = simulate(ten_km_slices(10), VehicleParams(20, 100, 37.5), 20, CFG)
    assert res.completed and res.alert is None and res.final_soc_pct == pytest.approx(37.5)


def test_cannot_start_below_reserve():
    res = simulate(ten_km_slices(3), VehicleParams(50, 5, 10), 20, CFG)
    assert not res.completed and res.results == [] and res.total_km == 0.0
    assert res.alert is not None and res.alert.km == 0.0 and "impossible de démarrer" in res.alert.message
    assert res.final_soc_pct == pytest.approx(5)


def test_cold_increases_consumption():
    veh = VehicleParams(50, 100, 10)
    warm = simulate(ten_km_slices(5), veh, 20, CFG)
    cold = simulate(ten_km_slices(5), veh, -10, CFG)
    assert cold.total_kwh == pytest.approx(warm.total_kwh * 1.35)


def test_vehicle_validation():
    with pytest.raises(ValueError):
        VehicleParams(0, 50, 10)
    with pytest.raises(ValueError):
        VehicleParams(50, 120, 10)
    with pytest.raises(ValueError):
        VehicleParams(50, 80, -1)


# --- géométrie -----------------------------------------------------------------
def test_haversine_one_degree_of_latitude():
    assert haversine_km(Point(0, 0), Point(1, 0)) == pytest.approx(111.195, abs=0.05)


def test_resample_covers_route_and_computes_speed():
    pts = [Point(48.0 + i * 0.01, 0.0) for i in range(101)]  # ~111 km
    total = haversine_km(pts[0], pts[-1])
    route = Route(pts, total, 60.0, durations_s=[36.0] * 100)  # 3600 s -> ~111 km/h
    samples = resample(route, 0.5)
    assert samples[0].start_km == 0
    assert samples[-1].end_km == pytest.approx(total)
    assert samples[0].avg_speed_kmh == pytest.approx(total / 1.0, rel=0.01)
    assert samples[0].mid.lat == pytest.approx(48.0 + 0.25 / 111.195, abs=1e-4)


def test_resample_has_no_ghost_sample_when_length_is_a_multiple_of_the_step():
    pts = [Point(48.0 + (140 / 111.195) * i / 200, 0.0) for i in range(201)]
    total = haversine_km(pts[0], pts[-1])
    samples = resample(Route(pts, total, 100.0), 0.5)
    assert len(samples) == 280 and samples[-1].end_km == pytest.approx(total, abs=1e-9)
    assert all(s.end_km - s.start_km > 0.4 for s in samples)
    assert all(b.start_km == pytest.approx(a.end_km) for a, b in zip(samples, samples[1:]))


def test_resample_rejects_degenerate_route():
    with pytest.raises(ValueError):
        resample(Route([Point(1, 1)], 0, 0))
    with pytest.raises(ValueError):
        resample(Route([Point(1, 1), Point(1, 1)], 0, 0))
