"""Route profiles in the service: one entry per trip, stations searched per profile on demand."""
import pytest

from ev_trip.application.jobs import DONE, REFINING, MemoryStore, TripRequest, TripService
from ev_trip.domain.models import Route, Slice, VehicleParams
from ev_trip.domain.route_profiles import ProfileReports
from ev_trip.domain.simulation import simulate_all
from ev_trip.domain.trip_report import TripReport
from interfaces.dash_ui.params import DEFAULTS, FieldError, from_query, request_from_values, to_query


def _report(minutes):
    vehicle = VehicleParams(52.0, 90.0, 10.0)
    slices = [Slice(0, 0.0, 100.0, "autoroute")]
    return TripReport("A", "B", Route([], 100.0, minutes), slices, simulate_all(slices, vehicle, 15.0))


def _request(**kw):
    return TripRequest("Paris", "Caen", 15.0, 52.0, 90.0, 10.0, False, 5.0, **kw)


def _service(profiles, plans):
    jobs = []
    svc = TripService(MemoryStore(), runner=jobs.append, run_profiles=lambda *a, **k: profiles,
                      refine=lambda r: r, plan=lambda report, km, **k: plans.append(report) or f"plan{len(plans)}")
    return svc, jobs


def test_profile_is_not_part_of_the_result_key_but_is_validated():
    assert _request(profile="eco").key() == _request(profile="fast").key()
    assert _request(eco_extra_pct=10.0).key() != _request().key()
    with pytest.raises(ValueError):
        _request(profile="x")
    with pytest.raises(ValueError):
        _request(eco_extra_pct=-1.0)


def test_same_route_keeps_a_single_report():
    fast = _report(200)
    svc, _ = _service(ProfileReports(fast, fast), [])
    _, entry = svc.start(_request())
    assert entry.eco_report is None and entry.report_for("eco") is entry.report and entry.profile_of("eco") == "fast"


def test_stations_are_searched_for_the_first_profile_then_on_demand():
    fast, eco, plans = _report(200), _report(215), []
    svc, jobs = _service(ProfileReports(fast, eco), plans)
    key, entry = svc.start(_request(profile="fast"))
    assert entry.report is fast and entry.eco_report is eco and entry.state == REFINING and len(jobs) == 1
    jobs.pop()()  # first task: fast profile
    entry = svc.get(key)
    assert plans == [fast] and entry.charging == "plan1" and entry.charging_for("eco") is None and entry.state == DONE
    svc.ensure_charging(key, "eco")
    assert svc.get(key).state == REFINING and len(jobs) == 1
    svc.ensure_charging(key, "eco")  # one task per trip and profile
    assert len(jobs) == 1
    jobs.pop()()
    entry = svc.get(key)
    assert plans == [fast, eco] and entry.charging_for("eco") == "plan2" and entry.charging_for("fast") == "plan1"
    assert entry.state == DONE and entry.pending == 0
    svc.ensure_charging(key, "eco")  # already known
    assert not jobs


def test_first_profile_eco_searches_eco_stations_first():
    fast, eco, plans = _report(200), _report(215), []
    svc, jobs = _service(ProfileReports(fast, eco), plans)
    key, _ = svc.start(_request(profile="eco"))
    jobs.pop()()
    assert plans == [eco] and svc.get(key).charging_for("eco") == "plan1" and svc.get(key).charging is None


def test_profile_and_eco_gap_in_the_query_string():
    request = request_from_values({**DEFAULTS, "origin": "Paris", "destination": "Caen", "profile": "eco", "eco_extra_pct": 20})
    assert request.profile == "eco" and request.eco_extra_pct == 20.0
    values = from_query(to_query(request))
    assert values["profile"] == "eco" and values["eco_extra_pct"] == 20.0
    assert request_from_values({**DEFAULTS, "origin": "a", "destination": "b"}).profile == "fast"
    assert request_from_values({**DEFAULTS, "origin": "a", "destination": "b"}).eco_exit_variants == 2
    request = request_from_values({**DEFAULTS, "origin": "a", "destination": "b", "eco_exit_variants": 1})
    assert from_query(to_query(request))["eco_exit_variants"] == 1
    for bad in (3, -1, "x"):
        with pytest.raises(FieldError):
            request_from_values({**DEFAULTS, "origin": "a", "destination": "b", "eco_exit_variants": bad})
    with pytest.raises(FieldError):
        request_from_values({**DEFAULTS, "origin": "a", "destination": "b", "eco_extra_pct": 150})


def test_layout_has_profile_selector_and_eco_gap_field():
    from interfaces.dash_ui import layout

    def walk(n):
        yield n
        kids = getattr(n, "children", None)
        for k in kids if isinstance(kids, (list, tuple)) else [kids]:
            if hasattr(k, "to_plotly_json"):
                yield from walk(k)

    ids = {getattr(n, "id", None) for n in walk(layout.build_layout("https://t/{z}/{x}/{y}.png", "osm"))}
    assert {"profile", "profile-note", "eco-extra", "eco-exit"} <= ids


def test_profile_options_and_overlay_helpers():
    from interfaces.dash_ui import components

    fast, eco = _report(200), _report(215)
    svc, _ = _service(ProfileReports(fast, eco), [])
    _, entry = svc.start(_request())
    options = components.profile_options(entry)
    assert [o["value"] for o in options] == ["fast", "eco"] and "200 min" in options[0]["label"] and "215 min" in options[1]["label"]
    chart = [{"x": 0, "y": 90}, {"x": 100, "y": 50}]
    data = {"scenarios": [{"chart": chart, "label": "Moyenne", "color": "#111"}], "trip_km": 100, "min_soc": 10}
    fig = components.charge_figure(data, {"scenarios": [{"chart": chart, "label": "x", "color": "#000"}] * 3})
    assert fig.data[0].name == "Autre profil (moyenne)" and len(fig.data) == 3
    assert components.alt_route_layer({"route_slices": [{"points": [[1, 2], [3, 4]]}]}).dashArray == "8 8"


def test_shared_link_follows_the_displayed_profile():
    from dash import no_update

    from interfaces.dash_ui.callbacks import with_profile

    assert with_profile("?origin=Paris&destination=Caen&profile=fast", "eco").endswith("profile=eco")
    assert with_profile("", "eco") is no_update


def test_configuration_tab_is_an_accordion_with_one_item_per_theme():
    import dash_bootstrap_components as dbc

    from interfaces.dash_ui.layout import CONFIG_OPEN, tab_configuration

    def walk(n):
        yield n
        kids = getattr(n, "children", None)
        for k in kids if isinstance(kids, (list, tuple)) else [kids]:
            if hasattr(k, "to_plotly_json"):
                yield from walk(k)

    nodes = list(walk(tab_configuration()))
    accordion = next(n for n in nodes if isinstance(n, dbc.Accordion))
    assert [i.title for i in accordion.children] == ["Calcul du trajet", "Profil consommation"]
    assert accordion.always_open is True and set(accordion.active_item) == set(CONFIG_OPEN)
    assert {"use-overpass", "eco-extra", "eco-exit"} <= {getattr(n, "id", None) for n in nodes}
