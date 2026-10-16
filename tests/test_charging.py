import json
import math

import pytest
import requests

from ev_trip import cli
from ev_trip.application.charging import osrm_detour, plan_charging
from ev_trip.domain.charging import (
    ChargingPlan, Station, TariffTable, energy_between, need_level, parse_power_kw, parse_price_eur_kwh,
    resolve_price, score_station, soc_at_km, station_from_element,
)
from ev_trip.domain.config import ChargingConfig, ConsumptionConfig
from ev_trip.domain.errors import RoadClassificationError, RoutingError
from ev_trip.domain.geo import cumulative_km, nearest_on_route
from ev_trip.domain.models import Point, Route, VehicleParams
from ev_trip.report import format_charging
from ev_trip.providers.files import load_config, load_tariffs
from ev_trip.providers.overpass import (
    OverpassClient, OverpassRoadClassifier, OverpassStationSource, corridor_query, find_stations,
)
from ev_trip.application.trip import run_trip

KM_LAT = 1 / 111.195
KM_LON = 1 / (111.195 * math.cos(math.radians(48.0)))


def straight_route(km, n=None):
    n = n or int(km)
    pts = [Point(48.0 + KM_LAT * km * i / n, 2.0) for i in range(n + 1)]
    return Route(pts, km, km * 0.6, durations_s=None)


def make_report(km, soc=90.0, battery=52.0, min_soc=10.0, temp=20.0):
    route = straight_route(km)
    return run_trip(
        "A", "B", temp, VehicleParams(battery, soc, min_soc), ConsumptionConfig(),
        geocode_fn=lambda q: route.coordinates[0], route_fn=lambda a, b: route, use_overpass=False,
    )


def element(route_km, offset_km=1.0, id=1, **tags):
    base = {"amenity": "charging_station"}
    base.update(tags)
    return {"type": "node", "id": id, "lat": 48.0 + KM_LAT * route_km, "lon": 2.0 + KM_LON * offset_km, "tags": base}


def station(route_km=150, offset_km=1.0, **kw):
    el = element(route_km, offset_km, **{"capacity": "4"})
    st = station_from_element(el)
    from dataclasses import replace
    return replace(st, **kw) if kw else st


class FakeClient:
    def __init__(self, elements=None, error=None):
        self.elements, self.error, self.queries = elements or [], error, []

    def run(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.elements


# --- parsing ------------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("22 kW", 22), ("22", 22), ("22000", 22), ("7200 W", 7.2), ("50;150 kW", 150), ("11 kw", 11),
    ("11,5 kW", 11.5), (None, None), ("abc", None), ("", None),
])
def test_parse_power(value, expected):
    assert parse_power_kw(value) == expected


@pytest.mark.parametrize("value,expected", [
    ("0.45 EUR/kWh", 0.45), ("€0.35/kWh", 0.35), ("0,40 €/kWh", 0.40), ("EUR 0.50 per kWh", 0.50),
    ("0.30 USD/kWh", None), ("2 EUR/hour", None), ("free", None), (None, None), ("99 EUR/kWh", None),
])
def test_parse_price(value, expected):
    assert parse_price_eur_kwh(value) == expected


def test_station_power_and_type():
    fast = station_from_element(element(10, **{"socket:type2_combo:output": "150 kW", "name": "Rapide"}))
    assert fast.power_kw == 150 and fast.charge_type == "rapide" and fast.label == "Rapide"
    slow = station_from_element(element(10, **{"socket:type2:output": "22 kW"}))
    assert slow.charge_type == "normale"
    best = station_from_element(element(10, **{"socket:type2:output": "22 kW", "socket:chademo:output": "50 kW"}))
    assert best.power_kw == 50 and best.charge_type == "rapide"
    assert station_from_element(element(10, **{"charging_station:output": "7 kW"})).power_kw == 7


def test_station_type_inferred_from_sockets_when_no_power():
    assert station_from_element(element(1, **{"socket:chademo": "1"})).charge_type == "rapide"
    assert station_from_element(element(1, **{"socket:type2": "2"})).charge_type == "normale"
    assert station_from_element(element(1, **{"socket:type2_combo": "no", "socket:type2": "1"})).charge_type == "normale"
    unknown = station_from_element(element(1))
    assert unknown.charge_type is None and unknown.power_kw is None


def test_station_exclusions_and_geometry():
    assert station_from_element({"type": "node", "id": 1, "lat": 1, "lon": 1, "tags": {"amenity": "cafe"}}) is None
    assert station_from_element(element(1, access="private")) is None
    assert station_from_element(element(1, access="no")) is None
    assert station_from_element(element(1, motorcar="no")) is None
    assert station_from_element(element(1, bicycle="yes")) is None
    assert station_from_element(element(1, bicycle="yes", **{"socket:type2": "1"})) is not None
    assert station_from_element(element(1, access="yes", capacity="x")).capacity is None
    way = {"type": "way", "id": 9, "center": {"lat": 48.5, "lon": 2.5}, "tags": {"amenity": "charging_station"}}
    st = station_from_element(way)
    assert (st.lat, st.lon, st.id) == (48.5, 2.5, "way/9")
    assert station_from_element({"type": "way", "id": 1, "tags": {"amenity": "charging_station"}}) is None


# --- tariffs and prices -----------------------------------------------------------------------

def test_tariffs_matching(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps({
        "_aide": "x",
        "operators": {"Tesla": {"rapide": 0.45}, "tesla supercharger": 0.40, "ionity": {"rapide": 0.69, "any": 0.5}},
        "_exemple": {"foo": {"any": 9}},
    }), encoding="utf-8")
    t = load_tariffs(path)
    assert t.lookup(station(operator="IONITY", charge_type="rapide")) == 0.69
    assert t.lookup(station(operator="Ionity", charge_type="normale")) == 0.5  # fall back to "any"
    assert t.lookup(station(operator="Ionity", charge_type=None)) == 0.5
    assert t.lookup(station(network="Tesla Supercharger", charge_type="rapide")) == 0.40  # longest key
    assert t.lookup(station(name="Tesla Destination", charge_type="rapide")) == 0.45
    assert t.lookup(station(name="Tesla Destination", charge_type="normale")) is None
    assert t.lookup(station(operator="Inconnu")) is None
    assert TariffTable().lookup(station(operator="Ionity")) is None


def test_tariffs_invalid_file(tmp_path):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"operators": {"x": {"rapide": -1}}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_tariffs(p)
    p.write_text("pas du json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_tariffs(p)


def test_price_precedence():
    t = TariffTable({"ionity": {"any": 0.69}})
    assert resolve_price(station(operator="Ionity", osm_price_eur_kwh=0.30, fee="no"), t) == (0.30, "osm")
    assert resolve_price(station(operator="Ionity", fee="no"), t) == (0.0, "gratuit")
    assert resolve_price(station(operator="Ionity"), t) == (0.69, "tarif")
    assert resolve_price(station(operator="Autre"), t) == (None, "inconnu")
    assert resolve_price(station(operator="Ionity"), None) == (None, "inconnu")


# --- Overpass -----------------------------------------------------------------------------

def test_corridor_query():
    q = corridor_query(straight_route(10), 3000)
    assert "around:3000,48.00000,2.00000" in q and '"amenity"="charging_station"' in q
    assert q.startswith("[out:json]") and q.endswith("out center tags;") and "node(" in q and "way(" in q
    last = f"{48.0 + KM_LAT * 10:.5f},2.00000"
    assert last in q


def test_find_stations_dedupes_and_filters():
    client = FakeClient([element(5, id=1), element(5, id=1), element(6, id=2, access="private"), element(7, id=3)])
    found = find_stations(straight_route(20), 3.0, client)
    assert sorted(s.id for s in found) == ["node/1", "node/3"]
    assert "around:3000" in client.queries[0]


def test_overpass_client_and_union_query(monkeypatch):
    monkeypatch.setattr("ev_trip.providers.overpass.time.sleep", lambda s: None)

    class Resp:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            pass

        def json(self):
            return {"elements": [{"id": 1}]}

    class Sess:
        def __init__(self):
            self.calls = []

        def post(self, url, data, headers, timeout):
            self.calls.append(data["data"])
            return Resp()

    sess = Sess()
    assert OverpassClient(session=sess).run("q") == [{"id": 1}]
    clf = OverpassRoadClassifier(session=sess, radius_m=30)
    q = clf._query([Point(48, 2), Point(48.5, 2.5)])
    assert q.count("way(around:30,") == 2 and q.startswith("[out:json][timeout:90];(") and "out tags geom" in q
    assert "48.000000,2.000000)" in q and "48.500000,2.500000)" in q


def test_overpass_client_fails_with_clear_error(monkeypatch):
    monkeypatch.setattr("ev_trip.providers.overpass.time.sleep", lambda s: None)

    class Sess:
        def post(self, *a, **k):
            raise requests.ConnectionError("boom")

    with pytest.raises(RoadClassificationError):
        OverpassClient(session=Sess(), retries=2).run("q")


# --- geometry, energy -------------------------------------------------------------------

def test_nearest_on_route():
    route = straight_route(300)
    km, dist = nearest_on_route(route, Point(48.0 + KM_LAT * 100, 2.0 + KM_LON * 2))
    assert km == pytest.approx(100, abs=1.1) and dist == pytest.approx(2, abs=0.1)
    assert nearest_on_route(route, Point(47.0, 2.0))[0] == 0
    assert nearest_on_route(route, Point(60.0, 2.0))[0] == pytest.approx(300, abs=0.1)


def test_soc_and_energy_along_the_trip():
    report = make_report(100)
    moy = report.simulation
    assert soc_at_km(moy, 0) == 90
    assert soc_at_km(moy, 50) == pytest.approx(90 - 50 * 14.8 / 100 / 52 * 100, abs=0.01)
    assert soc_at_km(moy, 100) == pytest.approx(moy.final_soc_pct, abs=0.01)
    assert soc_at_km(moy, 101) is None
    assert energy_between(report.slices, report.config, 20, "moyenne", 10, 60) == pytest.approx(50 * 0.148)
    assert energy_between(report.slices, report.config, 20, "moyenne", 60, 10) == 0


def test_soc_beyond_the_stop_is_unknown():
    report = make_report(300)
    stop = report.simulation.total_km
    assert stop < 300 and soc_at_km(report.simulation, stop - 1) is not None
    assert soc_at_km(report.simulation, stop + 5) is None


def test_need_level():
    assert need_level(make_report(300)) == "nécessaire"
    assert need_level(make_report(255)) == "prudent"
    assert need_level(make_report(100)) == "optionnelle"


# --- scoring ---------------------------------------------------------------------------------

def sc(report, km=150, detour=1.0, st=None, level=None, tariffs=None, max_detour=5.0):
    return score_station(report, st or station(km), km, detour, detour * 1.5, max_detour, level=level, tariffs=tariffs)


def test_later_reachable_station_beats_early_one_when_charging_is_needed():
    report = make_report(300)
    early, late = sc(report, 100), sc(report, 220)
    assert early.reachable and late.reachable
    assert late.score > early.score and late.sub_scores["need"] > early.sub_scores["need"]


def test_unreachable_station_scores_zero():
    report = make_report(300)
    far = sc(report, 290)
    assert not far.reachable and far.score == 0 and far.soc_arrival_pct is None
    assert any("hors d'atteinte" in n for n in far.notes)


def test_pessimistic_unreachable_is_penalised_not_zeroed():
    report = make_report(300)
    # between the pessimistic stop and the mean stop
    p_stop, m_stop = report.simulations["pessimiste"].total_km, report.simulation.total_km
    km = (p_stop + m_stop) / 2
    s = sc(report, km)
    assert s.reachable and not s.reachable_pessimiste and 0 < s.score
    assert any("pessimiste" in n for n in s.notes)


def test_optional_charging_gives_a_low_need_score():
    report = make_report(100, soc=60)
    s = sc(report, 50)
    assert s.sub_scores["need"] == 10 and s.reachable
    assert s.add_kwh == pytest.approx(52 * 0.20)  # nominal recharge


def test_smaller_detour_and_lower_price_score_higher():
    report = make_report(300)
    assert sc(report, detour=0.5).score > sc(report, detour=4.0).score
    cheap = sc(report, st=station(150, osm_price_eur_kwh=0.20))
    dear = sc(report, st=station(150, osm_price_eur_kwh=0.70))
    assert cheap.score > dear.score and cheap.price_source == "osm"
    unknown = sc(report)
    assert unknown.sub_scores["price"] == 50 and unknown.price_eur_kwh is None and unknown.cost_eur is None
    assert "prix inconnu" in unknown.notes
    assert cheap.cost_eur == pytest.approx(cheap.add_kwh / 0.85 * 0.20)


def test_fast_charger_beats_slow_one():
    report = make_report(300)
    fast = sc(report, st=station(150, power_kw=150, charge_type="rapide"))
    slow = sc(report, st=station(150, power_kw=7, charge_type="normale"))
    assert fast.charge_min < slow.charge_min and fast.sub_scores["charge"] > slow.sub_scores["charge"]
    assert fast.score > slow.score
    unknown = sc(report, st=station(150, power_kw=None, charge_type=None))
    assert unknown.sub_scores["charge"] == 50 and "puissance inconnue" in unknown.notes


def test_charge_never_exceeds_target_soc_and_detour_energy_is_reported():
    report = make_report(300)
    s = sc(report, 100, detour=3.0)
    assert s.soc_after_pct <= 80 + 1e-6
    assert 0 < sc(make_report(100), 50).add_kwh <= 52 * 0.20  # capped by the max charge (80 %)
    assert s.detour_kwh == pytest.approx(3.0 * 14.8 / 100)
    assert s.soc_arrival_pct == pytest.approx(s.soc_route_pct - 100 * s.detour_kwh / 2 / 52)


def test_weights_change_the_ranking():
    report = make_report(300)
    a, b = station(150, osm_price_eur_kwh=0.10), station(150, osm_price_eur_kwh=0.70)
    base = sc(report, st=a).score - sc(report, st=b).score
    report.config.charging.weight_price = 3.0
    assert sc(report, st=a).score - sc(report, st=b).score > base


def test_charging_config_validation_and_json(tmp_path):
    with pytest.raises(ValueError):
        ChargingConfig(weight_detour=-1)
    with pytest.raises(ValueError):
        ChargingConfig(weight_detour=0, weight_need=0, weight_charge=0, weight_price=0)
    with pytest.raises(ValueError):
        ChargingConfig(charge_efficiency=1.5)
    with pytest.raises(ValueError):
        ChargingConfig(max_results=0)
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"charging": {"weight_price": 0.5, "inconnu": 1}}), encoding="utf-8")
    cfg = load_config(p)
    assert cfg.charging.weight_price == 0.5 and cfg.charging.weight_detour == 0.30


# --- OSRM detour --------------------------------------------------------------------------

def test_osrm_detour_uses_anchors_around_the_station():
    route = straight_route(300)  # 0.6 min / km
    seen = []

    def path_fn(points):
        seen.append(points)
        return 32.0, 25.0

    st = station(100)
    km, mn = osrm_detour(route, st, 100.0, ChargingConfig(), path_fn=path_fn)
    assert km == pytest.approx(2.0, abs=0.05) and mn == pytest.approx(25 - 30 * 0.6, abs=0.05)
    a, mid, b = seen[0]
    assert mid == st.point
    assert cumulative_km([a, b])[-1] == pytest.approx(30, abs=0.1)


def test_osrm_detour_clamped_at_route_ends_and_never_negative():
    route = straight_route(300)
    km, mn = osrm_detour(route, station(2), 2.0, ChargingConfig(), path_fn=lambda pts: (1.0, 0.1))
    assert km == 0 and mn == 0


# --- full plan -------------------------------------------------------------------------

def run_plan(report, elements, detours, max_detour=5.0, **kw):
    sleeps = []
    client = FakeClient(elements)

    def detour_fn(route, st, km, cfg, cum):
        d = detours[st.id]
        if isinstance(d, Exception):
            raise d
        return d, d * 1.5

    plan = plan_charging(report, max_detour, stations=OverpassStationSource(client), detour_fn=detour_fn, sleep=sleeps.append, **kw)
    return plan, client, sleeps


def test_plan_ranks_filters_and_pauses():
    report = make_report(300)
    els = [element(100, id=1), element(220, id=2), element(150, id=3), element(240, id=4, access="private")]
    plan, client, sleeps = run_plan(report, els, {"node/1": 1.0, "node/2": 1.5, "node/3": 9.0})
    ids = [s.station.id for s in plan.suggestions]
    assert "node/3" not in ids  # real detour > max
    assert ids == ["node/2", "node/1"]  # most useful (late on the trip) first
    assert plan.found == 3 and plan.evaluated == 3 and plan.need_level == "nécessaire"
    assert len(sleeps) == 2 and all(s == 1.0 for s in sleeps)  # pause between calls, not before the first
    assert "around:3000" in client.queries[0]
    assert all(not s.detour_estimated for s in plan.suggestions)


def test_plan_limits_candidates_and_results():
    report = make_report(300)
    report.config.charging.max_candidates = 3
    report.config.charging.max_results = 2
    els = [element(20 * i + 10, id=i) for i in range(1, 11)]
    plan, _, sleeps = run_plan(report, els, {f"node/{i}": 1.0 for i in range(1, 11)})
    assert plan.found == 10 and plan.evaluated == 3 and len(plan.suggestions) == 2 and len(sleeps) == 2


def test_plan_falls_back_to_estimates_when_osrm_fails():
    report = make_report(300)
    els = [element(100, offset_km=1.0, id=1), element(200, offset_km=1.0, id=2)]
    plan, _, _ = run_plan(report, els, {"node/1": RoutingError("HS"), "node/2": RoutingError("HS")})
    assert len(plan.suggestions) == 2 and all(s.detour_estimated for s in plan.suggestions)
    assert any("OSRM indisponible" in w for w in plan.warnings)
    assert all(s.detour_km == pytest.approx(2 * 1.3, abs=0.1) for s in plan.suggestions)  # 2 x 1 km x 1.3


def test_plan_reports_overpass_failure():
    report = make_report(100)
    plan = plan_charging(report, 5, stations=OverpassStationSource(FakeClient(error=RoadClassificationError("Overpass indisponible : x"))))
    assert plan.suggestions == [] and plan.found == 0
    assert plan.warnings and plan.warnings[0].startswith("Recherche des bornes impossible")


def test_plan_without_station_and_invalid_detour():
    report = make_report(100)
    plan = plan_charging(report, 5, stations=OverpassStationSource(FakeClient([])))
    assert plan.suggestions == [] and plan.warnings == []
    with pytest.raises(ValueError):
        plan_charging(report, 0, stations=OverpassStationSource(FakeClient([])))


def test_plan_uses_tariffs():
    report = make_report(300)
    els = [element(150, id=1, operator="Ionity")]
    plan, _, _ = run_plan(report, els, {"node/1": 1.0}, tariffs=TariffTable({"ionity": {"any": 0.69}}))
    assert plan.suggestions[0].price_eur_kwh == 0.69 and plan.suggestions[0].price_source == "tarif"


# --- console display / CLI --------------------------------------------------------------

def test_format_charging_table_and_empty_case():
    report = make_report(300)
    plan, _, _ = run_plan(report, [element(150, id=1, name="Ma borne", **{"socket:type2:output": "22 kW"})], {"node/1": 1.0})
    text = format_charging(plan)
    assert "Ma borne" in text and "normale" in text and "détour max 5" in text and "nécessaire" in text
    empty = format_charging(ChargingPlan([], 0, 0, 5.0, "optionnelle", ["Recherche des bornes impossible : x"]))
    assert "0 trouvée" in empty and "Attention : Recherche des bornes impossible" in empty


def test_cli_max_detour(monkeypatch, capsys, tmp_path):
    report = make_report(300)
    monkeypatch.setattr(cli, "run_trip", lambda *a, **k: report)
    calls = {}

    def fake_plan(rep, detour, tariffs=None):
        calls.update(detour=detour, tariffs=tariffs)
        return ChargingPlan([], 0, 0, detour, "nécessaire")

    monkeypatch.setattr(cli, "plan_charging", fake_plan)
    tar = tmp_path / "t.json"
    tar.write_text(json.dumps({"operators": {"ionity": {"any": 0.5}}}), encoding="utf-8")
    code = cli.main(["A", "B", "--temp", "8", "--battery", "52", "--soc", "90", "--max-detour", "4", "--tariffs", str(tar)])
    out = capsys.readouterr().out
    assert code == 0 and "Bornes de recharge (détour max 4 km)" in out
    assert calls["detour"] == 4 and calls["tariffs"].operators == {"ionity": {"any": 0.5}}
    cli.main(["A", "B", "--temp", "8", "--battery", "52", "--soc", "90"])
    assert "Bornes de recharge" not in capsys.readouterr().out


def test_cli_rejects_bad_detour(capsys):
    with pytest.raises(SystemExit):
        cli.main(["A", "B", "--temp", "8", "--battery", "52", "--soc", "90", "--max-detour", "0"])
