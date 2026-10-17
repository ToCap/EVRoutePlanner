"""Energy tab: accordion with Batterie, Recharge and Consommation items."""
import dash_bootstrap_components as dbc

from interfaces.dash_ui.layout import ENERGY_OPEN, build_layout, tab_energy


def _walk(node):
    yield node
    children = getattr(node, "children", None)
    if isinstance(children, (list, tuple)):
        for child in children:
            yield from _walk(child)
    elif children is not None:
        yield from _walk(children)


def _accordion():
    return next(n for n in _walk(tab_energy()) if isinstance(n, dbc.Accordion))


def test_energy_tab_is_an_accordion_of_three_items():
    accordion = _accordion()
    assert [i.title for i in accordion.children] == ["Batterie", "Recharge", "Consommation"]
    assert accordion.always_open is True


def test_battery_and_recharge_open_at_load_consumption_collapsed():
    accordion = _accordion()
    assert set(accordion.active_item) == set(ENERGY_OPEN)
    assert "consumption" not in accordion.active_item


def test_energy_fields_keep_their_ids():
    ids = {getattr(n, "id", None) for n in _walk(build_layout("http://tiles/{z}/{x}/{y}.png", "osm"))}
    assert {"battery-kwh", "chargers", "eco-mode", "heating", "aircon", "energy-accordion"} <= ids
