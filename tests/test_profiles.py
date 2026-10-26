"""Route profiles in the service: one entry per trip, stations searched per profile on demand."""
import pytest

from ev_trip.application.jobs import DONE, FAILED, REFINING, MemoryStore, TripRequest, TripService, for_planning
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


def _service(profiles, plans, pools=None, fail_once=False):
    jobs, failed = [], []

    def plan(report, km, **k):
        if fail_once and report.route.duration_min == 215 and not failed:
            failed.append(1)
            raise RuntimeError("OSRM KO")
        plans.append((report.route.duration_min, k.get("pool")))
        return f"plan{len(plans)}"

    def find_pool(reports, km):
        if pools is not None:
            pools.append(([r.route.duration_min for r in reports], km))
        return ["st1", "st2"]

    svc = TripService(MemoryStore(), runner=jobs.append, run_profiles=lambda *a, **k: profiles, refine=lambda r: r, plan=plan,
                      find_pool=find_pool)
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


def test_stations_are_searched_once_for_both_routes_and_planned_per_profile():
    fast, eco, plans, pools = _report(200), _report(215), [], []
    svc, jobs = _service(ProfileReports(fast, eco), plans, pools)
    key, entry = svc.start(_request(profile="fast"))
    assert entry.report.route.duration_min == 200 and entry.eco_report.route.duration_min == 215 and entry.state == REFINING
    assert len(jobs) == 1
    jobs.pop()()
    entry = svc.get(key)
    assert pools == [([200, 215], 5.0)]  # one pool for both routes
    assert plans == [(200, ["st1", "st2"]), (215, ["st1", "st2"])]  # displayed profile first, same pool
    assert entry.charging == "plan1" and entry.charging_for("eco") == "plan2" and entry.state == DONE and entry.pending == 0
    svc.ensure_charging(key, "eco")  # already known: nothing to start
    assert not jobs


def test_first_profile_eco_is_planned_first():
    fast, eco, plans = _report(200), _report(215), []
    svc, jobs = _service(ProfileReports(fast, eco), plans)
    key, _ = svc.start(_request(profile="eco"))
    jobs.pop()()
    assert [minutes for minutes, _ in plans] == [215, 200]
    assert svc.get(key).charging_for("eco") == "plan1" and svc.get(key).charging == "plan2"


def test_single_route_has_no_pool_and_a_failed_profile_is_retried_on_selection():
    fast, plans = _report(200), []
    svc, jobs = _service(ProfileReports(fast, fast), plans)
    key, _ = svc.start(_request())
    jobs.pop()()
    assert plans == [(200, None)]  # one route: the plan does its own search
    fast, eco, plans = _report(200), _report(215), []
    svc, jobs = _service(ProfileReports(fast, eco), plans, fail_once=True)
    key, _ = svc.start(_request())
    jobs.pop()()
    entry = svc.get(key)
    assert entry.charging == "plan1" and entry.charging_for("eco") is None and entry.state == FAILED
    svc.ensure_charging(key, "eco")  # the failed profile is searched again when selected
    jobs.pop()()
    assert svc.get(key).charging_for("eco") == "plan2" and svc.get(key).state == DONE


def test_pool_failure_falls_back_to_one_search_per_route():
    fast, eco, plans = _report(200), _report(215), []
    svc, jobs = _service(ProfileReports(fast, eco), plans)
    svc.find_pool = lambda reports, km: (_ for _ in ()).throw(RuntimeError("Overpass KO"))
    key, _ = svc.start(_request())
    jobs.pop()()
    assert plans == [(200, None), (215, None)] and svc.get(key).state == DONE


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


def test_a_single_scenario_is_kept_per_profile():
    fast, eco = _report(200), _report(215)
    svc, _ = _service(ProfileReports(fast, eco), [])
    _, entry = svc.start(_request(scenario="pessimiste"))
    for report in (entry.report, entry.eco_report):
        assert list(report.simulations) == ["pessimiste"] and report.simulation is report.simulations["pessimiste"]
        assert report.config.scenario == "pessimiste"
    assert _request(scenario="optimiste").key() != _request().key()
    with pytest.raises(ValueError):
        _request(scenario="x")


def test_default_scenario_is_the_mean_and_survives_refinement():
    fast, eco = _report(200), _report(215)
    fast.precision = "coarse"  # refinable: the background task recomputes the three scenarios
    svc, jobs = _service(ProfileReports(fast, eco), [])
    svc.refine = lambda r: _report(r.route.duration_min)
    key, entry = svc.start(_request())
    assert list(entry.report.simulations) == ["moyenne"]
    jobs.pop()()
    assert list(svc.get(key).report.simulations) == ["moyenne"] and list(svc.get(key).eco_report.simulations) == ["moyenne"]


def test_scenario_in_the_query_string_and_the_config_field():
    request = request_from_values({**DEFAULTS, "origin": "a", "destination": "b", "scenario": "optimiste"})
    assert request.scenario == "optimiste" and from_query(to_query(request))["scenario"] == "optimiste"
    assert request_from_values({**DEFAULTS, "origin": "a", "destination": "b"}).scenario == "moyenne"
    from interfaces.dash_ui import layout

    def walk(n):
        yield n
        kids = getattr(n, "children", None)
        for k in kids if isinstance(kids, (list, tuple)) else [kids]:
            if hasattr(k, "to_plotly_json"):
                yield from walk(k)

    select = next(n for n in walk(layout.tab_configuration()) if getattr(n, "id", None) == "scenario")
    assert select.value == "moyenne" and [o["value"] for o in select.options] == ["optimiste", "moyenne", "pessimiste"]


def test_stations_tab_is_an_accordion_with_one_item_per_theme():
    import dash_bootstrap_components as dbc

    from interfaces.dash_ui.layout import STATIONS_OPEN, tab_stations

    def walk(n):
        yield n
        kids = getattr(n, "children", None)
        for k in kids if isinstance(kids, (list, tuple)) else [kids]:
            if hasattr(k, "to_plotly_json"):
                yield from walk(k)

    nodes = list(walk(tab_stations()))
    accordion = next(n for n in nodes if isinstance(n, dbc.Accordion))
    assert [i.title for i in accordion.children] == ["Recherche de bornes de recharge", "Pondérations de la note"]
    assert accordion.always_open is True and set(accordion.active_item) == set(STATIONS_OPEN) and "weights" not in accordion.active_item
    assert "max-detour" in {getattr(n, "id", None) for n in nodes}


def test_busy_badge_shows_the_current_stage_of_the_background_task():
    fast, eco = _report(200), _report(215)
    fast.precision = "coarse"
    seen, holder = [], {}

    def snap(label):
        seen.append((label, holder["svc"].get(holder["key"]).stage))

    jobs = []
    svc = TripService(MemoryStore(), runner=jobs.append, run_profiles=lambda *a, **k: ProfileReports(fast, eco),
                      refine=lambda r: snap("refine") or r, plan=lambda report, km, **k: snap(f"plan{report.route.duration_min:g}") or "plan",
                      find_pool=lambda reports, km: snap("pool") or [])
    holder["svc"] = svc
    holder["key"], entry = svc.start(_request())
    assert entry.stage == "Affinage des types de route…"
    jobs.pop()()
    assert seen == [("refine", "Affinage des types de route…"), ("pool", "Recherche des bornes (2 itinéraires)…"),
                    ("plan200", "Calcul des détours (profil Rapide)…"), ("plan215", "Calcul des détours (profil Consommation)…")]
    assert svc.get(holder["key"]).stage == ""  # cleared when the task ends


def test_single_route_stages_and_badge_text_id():
    from interfaces.dash_ui import layout

    fast = _report(200)
    svc, _ = _service(ProfileReports(fast, fast), [])
    _, entry = svc.start(_request())
    assert entry.stage == "Recherche des bornes…" and svc._detour_stage(entry, "fast") == "Calcul des détours…"

    def walk(n):
        yield n
        kids = getattr(n, "children", None)
        for k in kids if isinstance(kids, (list, tuple)) else [kids]:
            if hasattr(k, "to_plotly_json"):
                yield from walk(k)

    assert "refine-text" in {getattr(n, "id", None) for n in walk(layout.build_layout("https://t/{z}/{x}/{y}.png", "osm"))}


def test_station_search_receives_every_scenario_despite_the_single_displayed_one():
    seen = []
    fast = _report(200)
    svc, _ = _service(ProfileReports(fast, fast), seen)
    _, entry = svc.start(_request())
    assert list(entry.report.simulations) == ["moyenne"]
    assert set(for_planning(entry.report).simulations) == {"optimiste", "moyenne", "pessimiste"}
