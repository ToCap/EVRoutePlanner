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


def test_single_scenario_is_one_line_not_a_card():
    s = {"key": "moyenne", "label": "Moyenne", "color": "#000", "completed": False, "alert": {"km": 216.1}, "total_km": 216.1,
         "total_kwh": 41.6, "avg_kwh_per_100km": 19.3, "final_soc": 10.0, "final_kwh": 5.2}
    text = str(ui.scenario_cards({"scenarios": [s]}))
    assert "Card" not in text and "Moyenne" not in text and "41.6 kWh" in text and "interrompu km 216.1" in text
    assert "Card" in str(ui.scenario_cards({"scenarios": [s, {**s, "key": "optimiste", "label": "Optimiste"}]}))


def test_single_scenario_chart_and_table_do_not_name_it():
    s = {"key": "moyenne", "label": "Moyenne", "color": "#000", "chart": [{"x": 0, "y": 90}, {"x": 100, "y": 50}]}
    data = {"scenarios": [s], "trip_km": 100.0, "min_soc": 10.0, "rows": []}
    assert [t.name for t in ui.charge_figure(data).data] == ["Charge restante", "Seuil minimum"]
    assert "Moyenne" not in str(ui.detail_table(data))
    assert "Moyenne" in str(ui.detail_table({**data, "scenarios": [s, {**s, "label": "Optimiste"}]}))


def test_detail_table_has_a_narrow_index_column_and_content_sized_table():
    s = {"key": "moyenne", "label": "Moyenne", "color": "#000"}
    row = {"index": 3, "type": "autoroute", "start_km": 10.0, "end_km": 20.0,
           "cells": [{"cons": 20.0, "kwh": 2.0, "soc": 80.0, "stop": False, "stop_km": 20.0}]}
    table = ui.detail_table({"scenarios": [s], "rows": [row]}).children
    assert "w-auto" in table.className
    index_head = table.children[0].children[-1].children[0]  # the "#" header of the last header row
    assert index_head.children == "#" and index_head.style["width"] == "2.5rem" and "text-center" in index_head.className
