"""The scenario name is left out of the messages when a single scenario is displayed."""
from interfaces.dash_ui import components as ui


def _data(names):
    return {"trip_km": 312.8, "min_soc": 10.0, "scenarios": [
        {"key": n, "label": n.capitalize(), "color": "#000", "completed": False, "alert": {"km": 216.1, "lat": 48.0, "lon": 2.0}} for n in names]}


def test_single_scenario_alert_has_no_scenario_name():
    text = str(ui.banners(None, _data(["moyenne"])))
    assert "km 216.1" in text and "Moyenne" not in text
    assert "Moyenne" not in str(ui.marker_layers(_data(["moyenne"])))


def test_several_scenarios_alert_keeps_names():
    assert "Optimiste : km 216.1" in str(ui.banners(None, _data(["optimiste", "moyenne"])))
