"""A report cut to one scenario is still displayable."""
import pytest

from ev_trip.application.jobs import only_scenario
from ev_trip.domain.models import Point, Route, Slice, VehicleParams
from ev_trip.domain.simulation import simulate_all
from ev_trip.domain.trip_report import TripReport
from ev_trip.presentation import build_view_data


@pytest.mark.parametrize("scenario", ["optimiste", "moyenne", "pessimiste"])
def test_view_data_of_a_single_scenario_report(scenario):
    vehicle = VehicleParams(52.0, 90.0, 10.0)
    slices = [Slice(0, 0.0, 100.0, "autoroute")]
    route = Route([Point(48.0, 2.0), Point(49.0, 2.0)], 100.0, 90.0)
    report = only_scenario(TripReport("A", "B", route, slices, simulate_all(slices, vehicle, 15.0)), scenario)
    data = build_view_data(report)
    assert [s["key"] for s in data["scenarios"]] == [scenario]
    assert all(len(row["cells"]) == 1 for row in data["rows"])
