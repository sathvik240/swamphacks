"""FastAPI app: REST + WebSocket API and the supervisor dashboard."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import alerts, briefing, config, reports
from .hazard import model
from .monitor import Monitor, utcnow
from .realtime import Hub
from .sites import load_sites

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

hub = Hub()
monitor = Monitor(hub)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await monitor.start()
    yield
    await monitor.stop()


app = FastAPI(title="PlanIT Control", version="1.0", lifespan=lifespan)


def _site(site_id: str):
    site = load_sites().get(site_id)
    if not site:
        raise HTTPException(404, "unknown site")
    return site


@app.get("/api/health")
def health():
    return {"ok": True, "sites": len(load_sites()), "monitored": len(monitor.state), "mode": config.DATA_MODE}


@app.get("/api/sites")
def sites():
    """All sites ranked by current hazard index (highest first)."""
    return monitor.ranked()


@app.get("/api/sites/{site_id}")
def site_detail(site_id: str):
    site = _site(site_id)
    st = monitor.state.get(site_id, {})
    briefs = briefing.list_for(site_id, limit=1)
    return {
        "site": monitor.summary(site),
        "conditions": st.get("conditions"),
        "hazard": st.get("hazard"),
        "alerts": alerts.active(site_id),
        "reports": reports.near(site.lat, site.lon, 5.0, since=utcnow() - timedelta(days=30)),
        "briefing": briefs[0]["body"] if briefs else None,
    }


@app.post("/api/sites/{site_id}/refresh")
async def refresh(site_id: str):
    await monitor.refresh_site(_site(site_id), utcnow())
    return monitor.summary(_site(site_id))


@app.get("/api/alerts")
def list_alerts(site_id: str | None = None, include_cleared: bool = False):
    return alerts.recent() if include_cleared else alerts.active(site_id)


@app.get("/api/reports")
def list_reports(lat: float | None = None, lon: float | None = None, radius_mi: float = Query(5.0, gt=0, le=250),
                 days: int = Query(3650, gt=0), hazard_type: str | None = None):
    """Location hazard database: reports near a point (any project, any time window)."""
    if lat is None or lon is None:
        return reports.recent()
    return reports.near(lat, lon, radius_mi, since=utcnow() - timedelta(days=days), hazard_type=hazard_type)


@app.get("/api/reports/types")
def report_types():
    return reports.HAZARD_TYPES


@app.post("/api/reports", status_code=201)
async def create_report(lat: float = Form(...), lon: float = Form(...), hazard_type: str = Form(...),
                        severity: int = Form(3), description: str | None = Form(None),
                        reporter: str | None = Form(None), site_id: str | None = Form(None),
                        photo: UploadFile | None = File(None)):
    data = await photo.read(config.MAX_PHOTO_BYTES + 1) if photo is not None else None
    try:
        report = reports.create(lat=lat, lon=lon, hazard_type=hazard_type, severity=severity,
                                description=description, reporter=reporter, site_id=site_id or None, photo=data or None)
    except reports.ReportError as e:
        raise HTTPException(422, str(e))
    await hub.broadcast("report", report)
    if report["site_id"] and report["site_id"] in monitor.state:
        site = load_sites()[report["site_id"]]
        hub.notify_bg(f"[CREW REPORT] {site.name}: {report['hazard_label']} - {report['description'] or ''}", report)
        await monitor.assess(site, utcnow())
    return report


class Strike(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    time: datetime | None = None


@app.post("/api/lightning/strikes", status_code=202)
async def ingest_strikes(strikes: list[Strike]):
    """Hook for a real-time lightning feed. Strikes are processed on the next tick."""
    now = utcnow()
    monitor.ingest_strikes([{"lat": s.lat, "lon": s.lon, "source": "feed",
                             "time": (s.time or now).astimezone(timezone.utc).isoformat()} for s in strikes])
    return {"queued": len(strikes)}


@app.get("/api/briefings")
def list_briefings(site_id: str | None = None):
    return [b["body"] for b in briefing.list_for(site_id)]


@app.post("/api/sites/{site_id}/briefing")
async def make_briefing(site_id: str):
    site = _site(site_id)
    if site_id not in monitor.state:
        await monitor.refresh_site(site, utcnow())
    return await monitor.send_briefing(site, utcnow(), replace=True)


@app.get("/api/model")
def model_info():
    return model.info()


class Scenario(BaseModel):
    site_id: str
    kind: str = Field(pattern="^(storm|heavy_rain|heat|wildfire)$")
    minutes: int = Field(30, ge=1, le=720)


@app.post("/api/demo/scenario")
async def start_scenario(sc: Scenario):
    """Inject a hazard scenario at a site to demo alerts end to end."""
    site = _site(sc.site_id)
    now = utcnow()
    monitor.scenarios[site.id] = {"kind": sc.kind, "until": now + timedelta(minutes=sc.minutes)}
    await monitor.refresh_site(site, now)
    if sc.kind == "storm":
        await monitor.lightning_tick(now)
    return monitor.summary(site)


@app.delete("/api/demo/scenario/{site_id}")
async def stop_scenario(site_id: str):
    site = _site(site_id)
    monitor.scenarios.pop(site_id, None)
    await monitor.refresh_site(site, utcnow())
    return monitor.summary(site)


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(websocket)


config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=config.UPLOAD_DIR), name="uploads")
app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")
