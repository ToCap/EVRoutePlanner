"""Multi-stop recharge plan: portion score, couples, chaining, charge duration."""
from ev_trip.domain.charging import Station, StationSuggestion
from ev_trip.domain.models import Route, Slice, VehicleParams
from ev_trip.domain.recharge import plan_stops, portion_score
from ev_trip.domain.simulation import simulate_all
from ev_trip.domain.trip_report import TripReport


def _report(km, start=100.0, arrival=None):
    vehicle = VehicleParams(52.0, start, 10.0, arrival)
    slices = [Slice(i, i * 10.0, (i + 1) * 10.0, "autoroute") for i in range(int(km // 10))]
    return TripReport("A", "B", Route([], km, km), slices, simulate_all(slices, vehicle, 15.0))


def _sug(km, detour=1.0, kw=50.0):
    st = Station(f"n/{km}", 0.0, 0.0, name=f"B{km:g}", power_kw=kw, charge_type="rapide")
    return StationSuggestion(st, km, detour, detour, detour * 0.2, False, None, None, None, True, True, 0.0, None,
                             None, "inconnu", None, 50.0, {})


def test_portion_score_is_linear_between_window_top_and_minimum():
    assert portion_score(90, 10, 80) == 0 and portion_score(80, 10, 80) == 0 and portion_score(10, 10, 80) == 100
    assert round(portion_score(45, 10, 80)) == 50


def test_no_stop_when_the_trip_is_fine():
    assert plan_stops(_report(100.0), [_sug(50)], 5.0) == ([], [])


def test_one_stop_is_charged_to_cover_the_rest_of_the_trip():
    report = _report(260.0)
    stops, warns = plan_stops(report, [_sug(100), _sug(150)], 5.0)
    assert not warns and len(stops) == 1
    s = stops[0]
    assert s.suggestion.route_km == 150 and s.soc_after_pct > s.soc_arrival_pct and s.charge_min > 0
    assert s.soc_arrival_pct >= 15.0 and s.soc_after_pct <= 80.0 + 1e-6


def test_stops_are_chained_and_each_covers_up_to_the_next_one():
    report = _report(600.0)
    stops, warns = plan_stops(report, [_sug(k) for k in range(100, 600, 50)], 5.0)
    assert len(stops) >= 2 and [s.suggestion.route_km for s in stops] == sorted(s.suggestion.route_km for s in stops)
    assert all(s.soc_arrival_pct >= 15.0 for s in stops)


def test_wanted_arrival_charge_increases_the_charge_of_the_last_stop():
    plain = plan_stops(_report(260.0), [_sug(150)], 5.0)[0][0]
    wanted = plan_stops(_report(260.0, arrival=30.0), [_sug(150)], 5.0)[0][0]
    assert wanted.soc_after_pct > plain.soc_after_pct


def test_no_reachable_station_gives_a_warning():
    stops, warns = plan_stops(_report(260.0), [_sug(255)], 5.0)
    assert stops == [] and "Aucune borne" in warns[0]


def test_alert_is_orange_when_the_plan_covers_the_trip_and_red_otherwise():
    from interfaces.dash_ui import components as ui

    data = {"trip_km": 312.8, "min_soc": 10.0, "arrival_soc": None, "scenarios": [
        {"key": "moyenne", "label": "Moyenne", "completed": False, "final_soc": 10.0, "alert": {"km": 216.1}}]}
    assert ui.banners(None, data)[0].color == "danger"
    plan = {"covered": True, "stops": [{"km": 171.4}]}
    alert = ui.banners(None, {**data, "charging": plan})[0]
    assert alert.color == "warning" and "Trajet interrompu au km 216.1 sans recharge, " in str(alert) and "1 arrêt planifié (km 171.4)" in str(alert)
    assert ui.banners(None, {**data, "charging": {"covered": False, "stops": []}})[0].color == "danger"


def test_arrival_charge_with_stops_and_summary_line():
    from ev_trip.domain.recharge import final_soc_with_stops
    from interfaces.dash_ui import components as ui

    report = _report(260.0)
    stops, _ = plan_stops(report, [_sug(150)], 5.0)
    assert 15.0 <= final_soc_with_stops(report, stops) <= 30.0
    data = {"trip_km": 260.0, "min_soc": 10.0, "arrival_soc": None, "scenarios": [
        {"key": "moyenne", "label": "Moyenne", "completed": False, "final_soc": 10.0, "total_kwh": 40.0,
         "avg_kwh_per_100km": 19.0, "final_kwh": 5.2, "alert": {"km": 216.1}}]}
    assert len(ui.scenario_cards(data)) == 1
    data["charging"] = {"covered": True, "final_soc": 22.5, "charge_total_min": 32.0}
    assert "Avec recharge : " in str(ui.scenario_cards(data)[-1]) and "arrivée à 22.5 %, +32 min de charge" in str(ui.scenario_cards(data)[-1])


def test_interrupted_badge_is_not_red_when_the_plan_covers_the_trip():
    from interfaces.dash_ui import components as ui

    s = {"completed": False, "alert": {"km": 216.1}, "final_soc": 10.0}
    data = {"arrival_soc": None, "scenarios": [s]}
    assert ui._status_badge(s, data).color == "danger"
    covered = ui._status_badge(s, {**data, "charging": {"covered": True}})
    assert covered.color == "warning" and covered.children == "sans recharge"
