"""Route optimisation (fast / consumption) in the service: one route per trip, stations searched for it."""
import pytest

from ev_trip.application.jobs import DONE, FAILED, REFINING, MemoryStore, TripRequest, TripService, for_planning
from ev_trip.domain.config import ConsumptionConfig
from ev_trip.domain.models import Point, Route, Slice, VehicleParams
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


def _service(profiles, plans, fail=False):
    jobs = []

    def plan(report, km, **k):
        if fail:
            raise RuntimeError("OSRM KO")
        plans.append(report.route.duration_min)
        return f"plan{len(plans)}"

    svc = TripService(MemoryStore(), runner=jobs.append, run_profiles=lambda *a, **k: profiles, refine=lambda r: r, plan=plan)
    return svc, jobs


def test_profile_is_part_of_the_result_key_and_is_validated():
    assert _request(profile="eco").key() != _request(profile="fast").key()
    assert _request(eco_extra_pct=10.0).key() != _request().key()
    with pytest.raises(ValueError):
        _request(profile="x")
    with pytest.raises(ValueError):
        _request(eco_extra_pct=-1.0)


def test_each_optimisation_shows_its_own_route():
    fast, eco = _report(200), _report(215)
    svc, _ = _service(ProfileReports(fast, eco), [])
    assert svc.start(_request(profile="fast"))[1].report.route.duration_min == 200
    entry = svc.start(_request(profile="eco"))[1]
    assert entry.report.route.duration_min == 215 and not entry.fast_is_eco


def test_consumption_profile_on_the_fastest_route_is_notified():
    from interfaces.dash_ui.components import notifications

    fast = _report(200)
    svc, _ = _service(ProfileReports(fast, fast), [])
    key, entry = svc.start(_request(profile="eco"))
    assert entry.fast_is_eco and any("aussi le plus économe" in n["text"] for n in notifications(entry, key))
    key, entry = svc.start(_request(profile="fast"))
    assert not entry.fast_is_eco and not any("économe" in n["text"] for n in notifications(entry, key))


def test_stations_are_searched_for_the_displayed_route_only():
    fast, eco, plans = _report(200), _report(215), []
    svc, jobs = _service(ProfileReports(fast, eco), plans)
    key, entry = svc.start(_request(profile="eco"))
    assert entry.state == REFINING and len(jobs) == 1
    jobs.pop()()
    entry = svc.get(key)
    assert plans == [215] and entry.charging == "plan1" and entry.state == DONE and entry.pending == 0


def test_failed_station_search_keeps_the_route():
    svc, jobs = _service(ProfileReports(_report(200), _report(215)), [], fail=True)
    key, _ = svc.start(_request())
    jobs.pop()()
    entry = svc.get(key)
    assert entry.charging is None and entry.state == FAILED and entry.report.route.duration_min == 200


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


def test_layout_has_profile_selector_in_the_route_block_and_eco_gap_field():
    from interfaces.dash_ui import layout

    def walk(n):
        yield n
        kids = getattr(n, "children", None)
        for k in kids if isinstance(kids, (list, tuple)) else [kids]:
            if hasattr(k, "to_plotly_json"):
                yield from walk(k)

    ids = {getattr(n, "id", None) for n in walk(layout.build_layout("https://t/{z}/{x}/{y}.png", "osm"))}
    assert {"profile", "eco-extra", "eco-exit"} <= ids and "profile-note" not in ids
    selector = next(n for n in walk(layout.tab_trip()) if getattr(n, "id", None) == "profile")
    assert [o["value"] for o in selector.options] == ["fast", "eco"] and selector.value == "fast"
    assert selector.clearable is False and not getattr(selector, "multi", False)  # a single choice, always set


def test_charge_figure_has_no_other_profile_curve():
    from interfaces.dash_ui import components

    chart = [{"x": 0, "y": 90}, {"x": 100, "y": 50}]
    data = {"scenarios": [{"chart": chart, "label": "Moyenne", "color": "#111"}], "trip_km": 100, "min_soc": 10}
    assert [t.name for t in components.charge_figure(data).data] == ["Charge restante", "Seuil minimum"]
    assert not hasattr(components, "alt_route_layer") and not hasattr(components, "profile_options")


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


def test_a_single_scenario_is_kept():
    svc, _ = _service(ProfileReports(_report(200), _report(215)), [])
    _, entry = svc.start(_request(scenario="pessimiste"))
    report = entry.report
    assert list(report.simulations) == ["pessimiste"] and report.simulation is report.simulations["pessimiste"]
    assert report.config.scenario == "pessimiste"
    assert _request(scenario="optimiste").key() != _request().key()
    with pytest.raises(ValueError):
        _request(scenario="x")


def test_default_scenario_is_the_mean_and_survives_refinement():
    fast = _report(200)
    fast.precision = "coarse"  # refinable: the background task recomputes the three scenarios
    svc, jobs = _service(ProfileReports(fast, fast), [])
    svc.refine = lambda r: _report(r.route.duration_min)
    key, entry = svc.start(_request())
    assert list(entry.report.simulations) == ["moyenne"]
    jobs.pop()()
    assert list(svc.get(key).report.simulations) == ["moyenne"]


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
    fast = _report(200)
    fast.precision = "coarse"
    seen, holder = [], {}

    def snap(label):
        seen.append((label, holder["svc"].get(holder["key"]).stage))

    jobs = []
    svc = TripService(MemoryStore(), runner=jobs.append, run_profiles=lambda *a, **k: ProfileReports(fast, fast),
                      refine=lambda r: snap("refine") or r, plan=lambda report, km, **k: snap("plan") or "plan")
    holder["svc"] = svc
    holder["key"], entry = svc.start(_request())
    assert entry.stage == "Affinage des types de route…"
    jobs.pop()()
    assert seen == [("refine", "Affinage des types de route…"), ("plan", "Recherche des bornes…")]
    assert svc.get(holder["key"]).stage == ""  # cleared when the task ends


def test_first_stage_and_badge_text_id():
    from interfaces.dash_ui import layout

    fast = _report(200)
    svc, _ = _service(ProfileReports(fast, fast), [])
    _, entry = svc.start(_request())
    assert entry.stage == "Recherche des bornes…"

    def walk(n):
        yield n
        kids = getattr(n, "children", None)
        for k in kids if isinstance(kids, (list, tuple)) else [kids]:
            if hasattr(k, "to_plotly_json"):
                yield from walk(k)

    assert "refine-text" in {getattr(n, "id", None) for n in walk(layout.build_layout("https://t/{z}/{x}/{y}.png", "osm"))}


def test_station_search_receives_every_scenario_despite_the_single_displayed_one():
    fast = _report(200)
    svc, _ = _service(ProfileReports(fast, fast), [])
    _, entry = svc.start(_request())
    assert list(entry.report.simulations) == ["moyenne"]
    assert set(for_planning(entry.report).simulations) == {"optimiste", "moyenne", "pessimiste"}


class _Classifier:
    """Fake road classifier: every point on a main road."""

    def classify(self, points):
        return ["route"] * len(points)


def _deferred(variants, sleeps, profile="eco"):
    """Service whose motorway exit search runs in the background (retries recorded in `sleeps`)."""
    fast = _report(200)
    jobs = []
    svc = TripService(MemoryStore(), runner=jobs.append, run_profiles=lambda *a, **k: ProfileReports(fast, fast),
                      refine=lambda r: r, variants=variants, retry_delays=(2.0, 5.0), sleep=sleeps.append)
    request = TripRequest("Paris", "Caen", 15.0, 52.0, 90.0, 10.0, True, 0.0, profile=profile)
    return svc, jobs, svc.start(request)


def test_exit_variants_are_searched_in_the_background_and_may_change_the_consumption_route():
    eco, sleeps = _report(215), []
    svc, jobs, (key, entry) = _deferred(lambda profiles, k: ProfileReports(profiles.fast, eco), sleeps)
    assert entry.report.route.duration_min == 200 and entry.variants == 2 and entry.base is not None
    assert entry.stage == "Recherche des sorties d'autoroute…" and len(jobs) == 1
    jobs.pop()()
    done = svc.get(key)
    assert done.report.route.duration_min == 215 and done.variants == 0 and done.base is None and not done.fast_is_eco
    assert done.state == DONE and sleeps == [] and not any("variantes" in w for w in done.report.warnings)


def test_no_better_exit_means_the_fastest_route_is_the_least_energy_one():
    svc, jobs, (key, _) = _deferred(lambda profiles, k: profiles, [])
    jobs.pop()()
    assert svc.get(key).fast_is_eco and svc.get(key).report.route.duration_min == 200


def test_fast_profile_does_not_search_exits():
    calls = []
    svc, jobs, (key, entry) = _deferred(lambda profiles, k: calls.append(k) or profiles, [], profile="fast")
    assert entry.variants == 0 and entry.base is None and not jobs and not calls


def test_exit_search_is_retried_and_succeeds_after_failures():
    eco, calls, sleeps = _report(215), [], []

    def variants(profiles, k):
        calls.append(k)
        if len(calls) < 3:
            raise RuntimeError("502 (serveur surchargé)")
        return ProfileReports(profiles.fast, eco)

    svc, jobs, (key, _) = _deferred(variants, sleeps)
    jobs.pop()()
    assert calls == [2, 2, 2] and sleeps == [2.0, 5.0]
    assert svc.get(key).report.route.duration_min == 215 and svc.get(key).state == DONE


def test_exit_search_warns_only_when_every_attempt_failed():
    sleeps = []

    def variants(profiles, k):
        raise RuntimeError("502 (serveur surchargé)")

    svc, jobs, (key, _) = _deferred(variants, sleeps)
    jobs.pop()()
    done = svc.get(key)
    assert sleeps == [2.0, 5.0] and done.report.route.duration_min == 200 and done.state == DONE  # the trip stays usable
    assert any("Variantes de sortie non étudiées (502 (serveur surchargé))." in w for w in done.report.warnings)


def test_add_exit_variants_ranks_the_variants_with_the_existing_routes(monkeypatch):
    from ev_trip.application import trip

    vehicle, cfg = VehicleParams(52.0, 90.0, 10.0), ConsumptionConfig()

    def estimate(origin, destination, route, temp_c, veh, config, classifier):  # one slice as long as the route
        slices = [Slice(0, 0.0, route.distance_km, "route")]
        return TripReport(origin, destination, route, slices, simulate_all(slices, veh, temp_c, config), [], config)

    monkeypatch.setattr(trip, "_estimate", estimate)
    line = [Point(48.0, 2.0), Point(49.0, 2.0)]
    fast = estimate("A", "B", Route(line, 110.0, 80.0), 15.0, vehicle, cfg, None)
    slow = Route(line, 100.0, 85.0)  # shorter and a little slower: less energy, within the duration gap
    found = trip.add_exit_variants(ProfileReports(fast, fast), 2, variants_fn=lambda route, end, k: [slow], classifier=_Classifier())
    assert found.fast is fast and found.eco.route.duration_min == 85.0 and not found.same
    unchanged = trip.add_exit_variants(ProfileReports(fast, fast), 2, variants_fn=lambda route, end, k: [], classifier=_Classifier())
    assert unchanged.same and "Aucune variante" in fast.warnings[-1]


def test_trip_tab_has_route_environment_and_charge_accordion_items():
    import dash_bootstrap_components as dbc

    from interfaces.dash_ui.layout import TRIP_OPEN, tab_trip

    def walk(n):
        yield n
        kids = getattr(n, "children", None)
        for k in kids if isinstance(kids, (list, tuple)) else [kids]:
            if hasattr(k, "to_plotly_json"):
                yield from walk(k)

    accordion = next(n for n in walk(tab_trip()) if isinstance(n, dbc.Accordion))
    assert [i.title for i in accordion.children] == ["Itinéraire", "Environnement", "Charge"] and set(accordion.active_item) == set(TRIP_OPEN)
    environment = next(i for i in accordion.children if i.title == "Environnement")
    assert {getattr(n, "id", None) for n in walk(environment)} >= {"temp-c"}
    route = next(i for i in accordion.children if i.title == "Itinéraire")
    assert "temp-c" not in {getattr(n, "id", None) for n in walk(route)}
