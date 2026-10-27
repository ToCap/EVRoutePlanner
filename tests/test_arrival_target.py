"""Wanted charge at arrival: validation, form/URL round trip, simulation flag and display."""
import pytest

from ev_trip.domain.models import Slice, VehicleParams
from ev_trip.domain.simulation import simulate
from interfaces.dash_ui import components as ui
from interfaces.dash_ui.params import DEFAULTS, from_query, request_from_values, to_query

VALUES = {**DEFAULTS, "origin": "Paris", "destination": "Caen"}


def test_vehicle_arrival_target_must_be_between_minimum_and_100():
    assert VehicleParams(50.0, 90.0, 10.0, 30.0).arrival_soc_pct == 30.0
    for bad in (5.0, 101.0):
        with pytest.raises(ValueError):
            VehicleParams(50.0, 90.0, 10.0, bad)


def test_trip_ending_under_the_wanted_charge_is_flagged_but_not_interrupted():
    slices = [Slice(0, 0.0, 200.0, "autoroute")]
    sim = simulate(slices, VehicleParams(50.0, 90.0, 10.0, 80.0), 15.0)
    assert sim.completed and sim.short_of_arrival
    assert not simulate(slices, VehicleParams(50.0, 90.0, 10.0, 11.0), 15.0).short_of_arrival
    assert not simulate(slices, VehicleParams(50.0, 90.0, 10.0), 15.0).short_of_arrival  # no wish: only the minimum counts


def test_arrival_target_is_used_only_when_ticked_and_changes_the_result_key():
    assert request_from_values(VALUES).arrival_soc is None
    on = request_from_values({**VALUES, "arrival_on": ["on"], "arrival_soc": 40})
    assert on.arrival_soc == 40.0 and on.vehicle().arrival_soc_pct == 40.0 and on.key() != request_from_values(VALUES).key()
    with pytest.raises(ValueError, match="Charge à l'arrivée"):
        request_from_values({**VALUES, "arrival_on": ["on"], "arrival_soc": 5})  # under the 10 % minimum
    with pytest.raises(ValueError, match="Charge à l'arrivée"):
        request_from_values({**VALUES, "arrival_on": ["on"], "arrival_soc": "x"})


def test_arrival_target_survives_the_url_round_trip():
    request = request_from_values({**VALUES, "arrival_on": ["on"], "arrival_soc": 35})
    values = from_query(to_query(request))
    assert values["arrival_on"] == ["on"] and values["arrival_soc"] == 35.0
    assert "arrival" not in to_query(request_from_values(VALUES)) and from_query(to_query(request_from_values(VALUES)))["arrival_on"] == []


def _data(final_soc, completed=True, target=30.0, names=("moyenne",)):
    return {"trip_km": 300.0, "min_soc": 10.0, "arrival_soc": target, "scenarios": [
        {"key": n, "label": n.capitalize(), "color": "#000", "completed": completed, "final_soc": final_soc,
         "alert": None if completed else {"km": 216.1, "lat": 48.0, "lon": 2.0}} for n in names]}


def test_alert_under_the_wanted_charge_is_a_single_orange_line():
    alert = ui.banners(None, _data(8.0))[0]
    assert alert.color == "warning" and "Arrivée sous la charge souhaitée (30 %)" in str(alert) and "8.0 %" in str(alert)
    assert ui.banners(None, _data(35.0)) == [] and ui.banners(None, _data(8.0, target=None)) == []
    assert ui.banners(None, _data(8.0, completed=False))[0].color == "danger"  # reaching the reserve stays the red alert
    assert "Optimiste : 8.0 %" in str(ui.banners(None, _data(8.0, names=("optimiste", "moyenne")))[0])


def _report(arrival):
    from ev_trip.domain.models import Route
    from ev_trip.domain.simulation import simulate_all
    from ev_trip.domain.trip_report import TripReport

    slices = [Slice(0, 0.0, 200.0, "autoroute")]
    return TripReport("A", "B", Route([], 200.0, 120.0), slices, simulate_all(slices, VehicleParams(50.0, 90.0, 10.0, arrival), 15.0))


def test_need_level_follows_the_wanted_arrival_charge():
    from ev_trip.domain.charging import need_level

    assert need_level(_report(None)) != "nécessaire"  # the trip ends above the minimum
    assert need_level(_report(80.0)) == "nécessaire"  # ... but far under the wanted charge


def test_recharge_is_sized_to_reach_the_wanted_charge_at_arrival():
    from ev_trip.domain.charging import Station, score_station

    station = Station("node/1", 48.0, 2.0, power_kw=150.0, charge_type="rapide")
    add = lambda arrival: score_station(_report(arrival), station, 100.0, 1.0, 2.0, 10.0, level="nécessaire").add_kwh
    assert add(60.0) > add(None)  # more energy to end at 60 % than at the minimum plus margin
    assert add(10.0) == add(None)  # a wish equal to the minimum changes nothing
