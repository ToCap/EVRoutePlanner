"""Stations of unknown type are flagged when the user filters on charger levels."""
from interfaces.dash_ui import components as ui
from test_station_speed_filter import _charging


def _data(filtered):
    data = _charging([22, 22, 22])
    data["rows"][0]["charge_type"] = "?"
    data["type_filtered"] = filtered
    return data


def test_unknown_type_is_flagged_only_when_charger_levels_are_selected():
    assert str(ui.stations_table(_data(True), True)).count("⚠ type inconnu") == 2  # the row and the footnote
    assert "⚠" not in str(ui.stations_table(_data(False), True))


def test_unknown_type_stop_is_flagged():
    stop = {"name": "B", "power_kw": None, "km": 10.0, "charge_min": 5, "soc_arrival": 20, "soc_after": 60, "charge_type": "?"}
    assert "⚠ type inconnu" in str(ui.stops_table([stop], True))
    assert "⚠" not in str(ui.stops_table([stop], False))
