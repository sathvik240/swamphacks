import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from planit import alerts, briefing, reports
from planit.geo import haversine_mi, offset
from planit.hazard import model
from planit.providers import weather
from planit.sites import load_sites

NOW = datetime(2026, 8, 12, 15, 0, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


def test_at_least_50_sites_with_valid_coordinates():
    sites = load_sites()
    assert len(sites) >= 50
    for s in sites.values():
        assert 18 < s.lat < 50 and -125 < s.lon < -65, s.id
        s.tz  # valid IANA zone
    assert any(s.river == "Mississippi River" for s in sites.values())


def test_geo_roundtrip():
    lat, lon = offset(25.0, -80.0, 9.5, 45)
    assert haversine_mi(25.0, -80.0, lat, lon) == pytest.approx(9.5, abs=0.01)


def test_heat_index_matches_nws_table():
    assert weather.heat_index_f(96, 65) == pytest.approx(121, abs=1.5)
    assert weather.heat_index_f(90, 50) == pytest.approx(95, abs=1.5)
    assert weather.heat_index_f(70, 50) < 72


def _strike(site, miles, t):
    lat, lon = offset(site.lat, site.lon, miles, 90)
    return {"lat": lat, "lon": lon, "time": t.isoformat()}


def test_lightning_stop_work_and_countdown_reset(hub):
    site = load_sites()["turkey-point-fl"]
    eng = alerts.AlertEngine(hub)
    assert run(eng.process_strikes([_strike(site, 11, NOW)], NOW)) == []
    assert run(eng.process_strikes([_strike(site, 9, NOW)], NOW)) == [site.id]
    a = alerts.active(site.id)[0]
    assert a["kind"] == "lightning" and a["level"] == "stop-work"
    assert a["data"]["all_clear_at"] == (NOW + timedelta(minutes=30)).isoformat(timespec="seconds")

    later = NOW + timedelta(minutes=20)
    run(eng.process_strikes([_strike(site, 3, later)], later))
    a = alerts.active(site.id)[0]
    assert a["data"]["all_clear_at"] == (later + timedelta(minutes=30)).isoformat(timespec="seconds")
    assert a["data"]["strike_count"] == 2 and a["data"]["nearest_mi"] == 3.0

    assert run(eng.check_all_clear(NOW + timedelta(minutes=35))) == []
    assert run(eng.check_all_clear(later + timedelta(minutes=30))) == [site.id]
    assert alerts.active(site.id) == []
    assert any(e[0] == "alert_cleared" for e in hub.events)


def test_stale_strike_ignored(hub):
    site = load_sites()["turkey-point-fl"]
    eng = alerts.AlertEngine(hub)
    assert run(eng.process_strikes([_strike(site, 2, NOW - timedelta(minutes=45))], NOW)) == []


def _conditions(site, now, scenario=None):
    local = now.astimezone(site.tz)
    s = weather.simulate(site, local)
    weather.apply_scenario(s, scenario, local)
    c = weather.derive_conditions(s, local)
    c["nws_alerts"] = []
    return c


def test_flood_warning_after_night_of_heavy_rain_on_river(hub):
    river = load_sites()["grand-gulf-ms"]
    now = datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc)   # 09:00 CDT
    cond = _conditions(river, now, {"kind": "heavy_rain"})
    assert cond["overnight_precip_in"] >= 1.0
    hz = model.assess(river, cond, now=now, lightning_active=False, wildlife_reports=0, crew_hazards=0, fire_nearby=False)
    run(alerts.AlertEngine(hub).evaluate(river, cond, hz, {}, now))
    kinds = {a["kind"]: a for a in alerts.active(river.id)}
    assert kinds["flood"]["level"] == "warning" and "Mississippi" in kinds["flood"]["title"]
    assert "soil" in kinds

    # once conditions return to normal, the alerts clear
    dry = _conditions(river, now)
    dry["overnight_precip_in"] = dry["precip_24h_in"] = 0.0
    dry["current"]["soil_moisture"] = 0.2
    hz = model.assess(river, dry, now=now, lightning_active=False, wildlife_reports=0, crew_hazards=0, fire_nearby=False)
    run(alerts.AlertEngine(hub).evaluate(river, dry, hz, {}, now))
    assert "flood" not in {a["kind"] for a in alerts.active(river.id)}


def test_hazard_model_orders_risk_sensibly():
    site = load_sites()["turkey-point-fl"]
    from planit.hazard import features as F
    base = dict(precip_24h_in=0, precip_72h_in=0, soil_moisture=0.2, wind_gust_mph=10, thunder=0,
                near_river=0, coastal=1, month=8, lat=site.lat)
    calm = model.predict_many([F.build(heat_index_f=80, **base)])[0]
    hot = model.predict_many([F.build(heat_index_f=112, **base)])[0]
    storm = model.predict_many([F.build(heat_index_f=80, **{**base, "thunder": 1, "wind_gust_mph": 45})])[0]
    assert calm < 2.5 < 5 < hot and storm > 6
    score, factors = model.explain(F.build(heat_index_f=112, **base))
    assert factors[0]["feature"] == "heat_index_f"


def test_lightning_overlay_floors_index():
    site = load_sites()["sanford-fl"]
    cond = _conditions(site, NOW)
    hz = model.assess(site, cond, now=NOW, lightning_active=True, wildlife_reports=0, crew_hazards=0, fire_nearby=False)
    assert hz["index"] >= 9.5 and len(hz["forecast"]) == 3


PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc0f01f0005000201"
                    "e2a1c3c60000000049454e44ae426082")


def test_reports_snap_to_site_and_query_by_location():
    site = load_sites()["turkey-point-fl"]
    r = reports.create(lat=site.lat + 0.005, lon=site.lon, hazard_type="wildlife", severity=4,
                       description="gator by culvert", reporter="J", site_id=None, photo=PNG, now=NOW)
    assert r["site_id"] == site.id and r["photo_url"].endswith(".png")
    assert [x["id"] for x in reports.near(site.lat, site.lon, 1)] == [r["id"]]
    assert reports.near(site.lat + 1, site.lon, 1) == []
    ctx = reports.site_context(site, NOW + timedelta(hours=1))
    assert len(ctx["wildlife"]) == 1
    assert reports.site_context(site, NOW + timedelta(hours=49))["wildlife"] == []


@pytest.mark.parametrize("kw,msg", [
    (dict(hazard_type="nope"), "hazard_type"), (dict(severity=9), "severity"),
    (dict(photo=b"GIF9 not really"), "photo"), (dict(lat=200), "coordinates"),
])
def test_report_validation(kw, msg):
    args = dict(lat=25.4, lon=-80.3, hazard_type="other", severity=3, description=None, reporter=None, site_id=None, photo=None)
    with pytest.raises(reports.ReportError, match=msg):
        reports.create(**{**args, **kw})


def test_briefing_due_at_6pm_local_once():
    site = load_sites()["turkey-point-fl"]            # America/New_York (EDT, UTC-4)
    before = datetime(2026, 8, 12, 21, 59, tzinfo=timezone.utc)   # 17:59 local
    at = datetime(2026, 8, 12, 22, 0, tzinfo=timezone.utc)        # 18:00 local
    assert not briefing.due(site, before) and briefing.due(site, at)
    body = briefing.build(site, _conditions(site, at), at)
    assert body["for_date"] == "2026-08-13" and body["recommendations"]
    assert briefing.save(body, at) and not briefing.save(body, at)
    assert not briefing.due(site, at + timedelta(minutes=5))
    # Phoenix (no DST) is 18:00 at 01:00 UTC
    pv = load_sites()["palo-verde-az"]
    assert briefing.due(pv, datetime(2026, 8, 13, 1, 0, tzinfo=timezone.utc))
