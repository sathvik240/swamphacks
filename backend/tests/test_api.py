from fastapi.testclient import TestClient

from planit.main import app, monitor
from planit.monitor import utcnow


def test_end_to_end_api():
    with TestClient(app) as c:
        # loops sleep for an hour in tests; refresh synchronously instead
        c.portal.call(monitor.refresh_all, utcnow())
        sites = c.get("/api/sites").json()
        assert len(sites) >= 50 and all("index" in s for s in sites)
        assert [s["index"] for s in sites] == sorted((s["index"] for s in sites), reverse=True)

        r = c.post("/api/demo/scenario", json={"site_id": "turkey-point-fl", "kind": "storm"}).json()
        assert r["lightning_all_clear_at"] and r["index"] >= 9.5
        assert c.get("/api/sites").json()[0]["index"] >= 9.5

        ingest = c.post("/api/lightning/strikes", json=[{"lat": 30.0, "lon": -90.47}]).json()
        assert ingest == {"queued": 1}
        c.portal.call(monitor.lightning_tick, utcnow())
        assert any(a["kind"] == "lightning" for a in c.get("/api/alerts", params={"site_id": "waterford-3-la"}).json())

        rep = c.post("/api/reports", data={"lat": 25.44, "lon": -80.33, "hazard_type": "wildlife", "description": "gator"})
        assert rep.status_code == 201
        detail = c.get("/api/sites/turkey-point-fl").json()
        assert detail["reports"] and any(a["kind"] == "wildlife" for a in detail["alerts"])

        b = c.post("/api/sites/turkey-point-fl/briefing").json()
        assert b["site_id"] == "turkey-point-fl" and c.get("/api/briefings").json()
        assert c.get("/api/model").json()["source"]
        assert c.get("/").status_code == 200
        assert c.post("/api/reports", data={"lat": 1, "lon": 1, "hazard_type": "bad"}).status_code == 422
