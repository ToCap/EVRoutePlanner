"""Station table sorting: rows, header clicks, rank kept."""
from interfaces.dash_ui import components as ui

ROWS = [
    {"rank": 1, "score": 80, "km": 120.0, "detour_km": 3.0, "soc_arrival": 25},
    {"rank": 2, "score": 60, "km": 40.0, "detour_km": 1.0, "soc_arrival": None},
    {"rank": 3, "score": 40, "km": 200.0, "detour_km": 2.0, "soc_arrival": 12},
]


def test_default_order_is_score_descending_and_other_columns_have_their_natural_direction():
    assert [r["rank"] for r in ui.sort_rows(ROWS, None)] == [1, 2, 3]
    assert [r["rank"] for r in ui.sort_rows(ROWS, {"key": "km", "desc": False})] == [2, 1, 3]
    assert [r["rank"] for r in ui.sort_rows(ROWS, {"key": "detour_km", "desc": False})] == [2, 3, 1]


def test_unknown_values_go_last_whatever_the_direction():
    for desc in (True, False):
        assert ui.sort_rows(ROWS, {"key": "soc_arrival", "desc": desc})[-1]["rank"] == 2


def test_click_reverses_the_same_column_and_resets_the_direction_of_another():
    first = ui.next_sort(None, "km")
    assert first == {"key": "km", "desc": False}
    assert ui.next_sort(first, "km") == {"key": "km", "desc": True}
    assert ui.next_sort(first, "score") == {"key": "score", "desc": True}


def test_table_header_shows_the_arrow_and_keeps_the_score_rank():
    rows = [{**r, "name": "B", "operator": "", "charge_type": "rapide", "power_kw": None, "detour_estimated": False,
             "detour_min": 1, "detour_kwh": 0.1, "reachable": True, "add_kwh": 1.0, "charge_min": 10, "price": None,
             "cost": None, "price_source": "inconnu", "color": "#0a0", "sub": {"detour": 1, "need": 1, "charge": 1, "price": 1}, "notes": []} for r in ROWS]
    charging = {"found": 3, "rows": rows, "need_level": "nécessaire", "warnings": [], "stops": []}
    text = str(ui.stations_table(charging, True, {"key": "km", "desc": False}))
    assert "km ▲" in text and "note ▼" not in text
    assert text.index("120.0") > text.index("40.0")
