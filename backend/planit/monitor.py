"""Background monitoring: refresh conditions, score hazards, raise alerts,
track lightning countdowns and send 6 PM briefings."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

from . import alerts, briefing, config, db, reports
from .geo import haversine_mi
from .hazard import model
from .providers import lightning, nws, weather, wildfire
from .sites import Site, load_sites

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Monitor:
    def __init__(self, hub):
        self.hub = hub
        self.engine = alerts.AlertEngine(hub)
        self.state: dict[str, dict] = {}
        self.scenarios: dict[str, dict] = {}
        self.pending_strikes: list[dict] = []
        self.hotspots: list[tuple[float, float]] | None = None
        self._live_failures = 0
        self._live_down_until: datetime | None = None
        self._tasks: list[asyncio.Task] = []
        self.client: httpx.AsyncClient | None = None

    # ---- lifecycle -------------------------------------------------------
    async def start(self):
        self.client = httpx.AsyncClient(headers={"User-Agent": config.USER_AGENT})
        model.load()
        self._tasks = [asyncio.create_task(self._loop(self.refresh_all, config.CONDITIONS_INTERVAL)),
                       asyncio.create_task(self._loop(self.lightning_tick, config.LIGHTNING_INTERVAL, delay=5)),
                       asyncio.create_task(self._loop(self.briefing_tick, 60, delay=15))]

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        if self.client:
            await self.client.aclose()

    async def _loop(self, fn, interval, delay=0):
        await asyncio.sleep(delay)
        while True:
            try:
                await fn(utcnow())
            except Exception:
                log.exception("monitor task %s failed", fn.__name__)
            await asyncio.sleep(interval)

    # ---- conditions ------------------------------------------------------
    def _live_ok(self, now) -> bool:
        if config.DATA_MODE == "sim":
            return False
        return self._live_down_until is None or now >= self._live_down_until

    async def fetch_conditions(self, site: Site, now: datetime) -> dict:
        local = now.astimezone(site.tz)
        series, nws_alerts = None, []
        if self._live_ok(now):
            try:
                series = await weather.fetch_open_meteo(self.client, site)
                self._live_failures = 0
                try:
                    nws_alerts = await nws.fetch_alerts(self.client, site)
                except Exception as e:
                    log.debug("NWS alerts unavailable for %s: %s", site.id, e)
            except Exception as e:
                if config.DATA_MODE == "live":
                    raise
                self._live_failures += 1
                if self._live_failures >= 5:
                    self._live_down_until = now + timedelta(minutes=15)
                    log.warning("live weather unreachable (%s); simulating for 15 min", e)
        if series is None:
            series = weather.simulate(site, local)
        scenario = self._scenario(site.id, now)
        weather.apply_scenario(series, scenario, local)
        cond = weather.derive_conditions(series, local)
        cond["nws_alerts"] = nws_alerts + nws.scenario_alerts(scenario)
        cond["fetched_at"] = now.isoformat(timespec="seconds")
        return cond

    def _scenario(self, site_id, now):
        sc = self.scenarios.get(site_id)
        if sc and sc["until"] <= now:
            self.scenarios.pop(site_id, None)
            return None
        return sc

    def _fire_nearby(self, site: Site, cond: dict) -> bool:
        if any(k in (a.get("event") or "") for a in cond.get("nws_alerts", []) for k in ("Red Flag", "Fire")):
            return True
        return any(haversine_mi(site.lat, site.lon, la, lo) <= config.WILDFIRE_RADIUS_MI for la, lo in self.hotspots or [])

    async def assess(self, site: Site, now: datetime, cond: dict | None = None):
        cond = cond or self.state[site.id]["conditions"]
        ctx = reports.site_context(site, now)
        ctx["fire_nearby"] = self._fire_nearby(site, cond)
        lightning_active = any(a["kind"] == "lightning" for a in alerts.active(site.id))
        hazard = model.assess(site, cond, now=now, lightning_active=lightning_active,
                              wildlife_reports=len(ctx["wildlife"]), crew_hazards=len(ctx["crew_hazards"]),
                              fire_nearby=ctx["fire_nearby"])
        self.state[site.id] = {"conditions": cond, "hazard": hazard, "updated_at": now.isoformat(timespec="seconds")}
        await self.engine.evaluate(site, cond, hazard, ctx, now)
        await self.hub.broadcast("site_update", self.summary(site))

    async def refresh_site(self, site: Site, now: datetime):
        await self.assess(site, now, await self.fetch_conditions(site, now))

    async def refresh_all(self, now: datetime):
        if config.DATA_MODE != "sim":
            try:
                self.hotspots = await wildfire.fetch_hotspots(self.client)
            except Exception as e:
                log.warning("FIRMS unavailable: %s", e)
        sem = asyncio.Semaphore(8)

        async def one(site):
            async with sem:
                try:
                    await self.refresh_site(site, now)
                except Exception:
                    log.exception("refresh failed for %s", site.id)

        await asyncio.gather(*(one(s) for s in load_sites().values()))

    # ---- lightning -------------------------------------------------------
    def ingest_strikes(self, strikes: list[dict]):
        self.pending_strikes.extend(strikes)

    async def lightning_tick(self, now: datetime):
        strikes, self.pending_strikes = self.pending_strikes, []
        for site in load_sites().values():
            st = self.state.get(site.id)
            if not st or not st["conditions"]["current"]["thunder"]:
                continue
            if st["conditions"]["source"] == "simulated" or self.scenarios.get(site.id):
                strikes += lightning.simulated_strikes(site, now, config.LIGHTNING_INTERVAL)
            else:
                # Live forecast says thunder at the site this hour: treat as a strike at the site.
                strikes.append({"lat": site.lat, "lon": site.lon, "time": now.isoformat(), "source": "thunder-observed"})
        if strikes:
            with db.tx() as c:
                c.executemany("INSERT INTO strikes(lat, lon, time, source) VALUES (?,?,?,?)",
                              [(s["lat"], s["lon"], s["time"], s.get("source")) for s in strikes])
            await self.hub.broadcast("strikes", {"strikes": strikes[-200:]})
        hit = await self.engine.process_strikes(strikes, now)
        cleared = await self.engine.check_all_clear(now)
        sites = load_sites()
        for sid in set(hit) | set(cleared):
            if sid in self.state:
                await self.assess(sites[sid], now)

    # ---- briefings -------------------------------------------------------
    async def briefing_tick(self, now: datetime):
        for site in load_sites().values():
            if site.id in self.state and briefing.due(site, now):
                await self.send_briefing(site, now)

    async def send_briefing(self, site: Site, now: datetime, replace=False) -> dict:
        body = briefing.build(site, self.state[site.id]["conditions"], now)
        if briefing.save(body, now, replace=replace):
            await self.hub.broadcast("briefing", body)
            self.hub.notify_bg(body["text"], body)
        return body

    # ---- views -----------------------------------------------------------
    def summary(self, site: Site) -> dict:
        st = self.state.get(site.id)
        act = alerts.active(site.id)
        lightning_alert = next((a for a in act if a["kind"] == "lightning"), None)
        out = {**site.to_dict(), "alerts": len(act),
               "top_alert": max(act, key=lambda a: alerts.LEVEL_ORDER[a["level"]])["title"] if act else None,
               "lightning_all_clear_at": lightning_alert["data"]["all_clear_at"] if lightning_alert else None}
        if st:
            h, c = st["hazard"], st["conditions"]
            out.update(index=h["index"], level=h["level"], ml_score=h["ml_score"],
                       top_factor=h["factors"][0]["label"] if h["factors"] and h["factors"][0]["contribution"] > 0.3 else None,
                       heat_index_f=c["current"]["heat_index_f"], source=c["source"], updated_at=st["updated_at"],
                       forecast=h["forecast"])
        return out

    def ranked(self) -> list[dict]:
        rows = [self.summary(s) for s in load_sites().values()]
        return sorted(rows, key=lambda r: (-(r.get("index") or -1), -r["alerts"], r["name"]))
