"""Speed filter of the station table: classes, bounds, display count (on top of the vehicle chargers)."""
import pytest

from interfaces.dash_ui import components as ui
from test_station_sort import ROWS


@pytest.mark.parametrize("kw, expected", [(None, "inconnue"), (3.7, "lente"), (9.9, "lente"), (10, "acceleree"), (22, "acceleree"),
                                           (50, "rapide"), (149.9, "rapide"), (150, "ultra"), (350, "ultra")])
def test_speed_class_includes_the_lower_bound(kw, expected):
    assert ui.speed_class(kw) == expected


def test_filter_keeps_selected_classes_only():
    rows = [{"power_kw": p} for p in (None, 7, 22, 50, 175)]
    assert [r["power_kw"] for r in ui.filter_rows(rows, ["rapide", "ultra"])] == [50, 175]
    assert len(ui.filter_rows(rows, None)) == 5 and ui.filter_rows(rows, []) == []


def _charging(powers):
    rows = [{**r, "name": "B", "operator": "", "charge_type": "rapide", "power_kw": p, "detour_estimated": False,
             "detour_min": 1, "detour_kwh": 0.1, "reachable": True, "add_kwh": 1.0, "charge_min": 10, "price": None,
             "cost": None, "price_source": "inconnu", "color": "#0a0", "sub": {"detour": 1, "need": 1, "charge": 1, "price": 1}, "notes": []}
            for r, p in zip(ROWS, powers)]
    return {"found": 3, "rows": rows, "need_level": "nécessaire", "warnings": [], "stops": []}


def test_table_shows_the_hidden_count_and_a_message_when_nothing_matches():
    charging = _charging([7, 22, 100])
    assert "2 borne(s) affichée(s) sur 3" in str(ui.stations_table(charging, True, None, ["acceleree", "rapide"]))
    assert "affichée(s) sur" not in str(ui.stations_table(charging, True, None, ui.ALL_SPEEDS))
    assert "Aucune borne ne correspond" in str(ui.stations_table(charging, True, None, ["ultra"]))
