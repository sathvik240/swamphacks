"""Alert engine: lightning stop-work countdowns and condition-based warnings."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from . import config, db
from .geo import haversine_mi
from .sites import Site, load_sites

LEVEL_ORDER = {"advisory": 1, "warning": 2, "danger": 3, "stop-work": 4}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def active(site_id: str | None = None) -> list[dict]:
    if site_id:
        return db.query("SELECT * FROM alerts WHERE cleared_at IS NULL AND site_id=? ORDER BY created_at DESC", (site_id,))
    return db.query("SELECT * FROM alerts WHERE cleared_at IS NULL ORDER BY created_at DESC")


def recent(limit: int = 100) -> list[dict]:
    return db.query("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))


def _get_active(site_id: str, kind: str) -> dict | None:
    rows = db.query("SELECT * FROM alerts WHERE cleared_at IS NULL AND site_id=? AND kind=?", (site_id, kind))
    return rows[0] if rows else None


def _insert(site_id, kind, level, title, message, data, now) -> dict:
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO alerts(site_id, kind, level, title, message, data, created_at) VALUES (?,?,?,?,?,?,?)",
            (site_id, kind, level, title, message, json.dumps(data), _iso(now)))
        aid = cur.lastrowid
    return db.query("SELECT * FROM alerts WHERE id=?", (aid,))[0]


def _update(aid, level, title, message, data) -> dict:
    with db.tx() as c:
        c.execute("UPDATE alerts SET level=?, title=?, message=?, data=? WHERE id=?",
                  (level, title, message, json.dumps(data), aid))
    return db.query("SELECT * FROM alerts WHERE id=?", (aid,))[0]


def _clear(aid, now) -> dict:
    with db.tx() as c:
        c.execute("UPDATE alerts SET cleared_at=? WHERE id=?", (_iso(now), aid))
    return db.query("SELECT * FROM alerts WHERE id=?", (aid,))[0]


class AlertEngine:
    def __init__(self, hub):
        self.hub = hub

    async def _emit(self, event, alert, site: Site):
        alert = {**alert, "site_name": site.name}
        await self.hub.broadcast(event, alert)
        if event == "alert" or (event == "alert_cleared" and alert["kind"] == "lightning"):
            prefix = "ALL CLEAR" if event == "alert_cleared" else alert["level"].upper()
            title = "Lightning all-clear: work may resume" if event == "alert_cleared" else alert["title"]
            self.hub.notify_bg(f"[{prefix}] {site.name}, {site.state}: {title}", alert)

    # ---- lightning -------------------------------------------------------
    async def process_strikes(self, strikes: list[dict], now: datetime) -> list[str]:
        """Each strike within 10 mi starts (or restarts) a 30 minute countdown."""
        sites = load_sites()
        hits: dict[str, dict] = {}
        window = timedelta(minutes=config.LIGHTNING_ALL_CLEAR_MIN)
        for s in strikes:
            t = datetime.fromisoformat(s["time"])
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if now - t > window:
                continue
            for site in sites.values():
                if abs(site.lat - s["lat"]) > 0.3 or abs(site.lon - s["lon"]) > 0.4:
                    continue
                d = haversine_mi(site.lat, site.lon, s["lat"], s["lon"])
                if d <= config.LIGHTNING_RADIUS_MI:
                    h = hits.setdefault(site.id, {"count": 0, "nearest": d, "last": t})
                    h["count"] += 1
                    h["nearest"] = min(h["nearest"], d)
                    h["last"] = max(h["last"], t)
        for sid, h in hits.items():
            site = sites[sid]
            all_clear = h["last"] + window
            existing = _get_active(sid, "lightning")
            if existing:
                data = existing["data"] or {}
                prev = datetime.fromisoformat(data["all_clear_at"])
                data.update(strike_count=data.get("strike_count", 0) + h["count"],
                            nearest_mi=round(min(data.get("nearest_mi", 99), h["nearest"]), 1),
                            last_strike=_iso(max(prev - window, h["last"])),
                            all_clear_at=_iso(max(prev, all_clear)))
                alert = _update(existing["id"], "stop-work", existing["title"], self._lightning_msg(data), data)
                await self._emit("alert_updated", alert, site)
            else:
                data = {"strike_count": h["count"], "nearest_mi": round(h["nearest"], 1),
                        "last_strike": _iso(h["last"]), "all_clear_at": _iso(all_clear)}
                alert = _insert(sid, "lightning", "stop-work", "STOP WORK: lightning within 10 miles",
                                self._lightning_msg(data), data, now)
                await self._emit("alert", alert, site)
        return list(hits)

    @staticmethod
    def _lightning_msg(data):
        return (f"Strike {data['nearest_mi']} mi from site. Move crews to vehicles or enclosed buildings. "
                f"All-clear at {data['all_clear_at']} UTC; any new strike within 10 mi restarts the 30 minute countdown.")

    async def check_all_clear(self, now: datetime) -> list[str]:
        cleared = []
        sites = load_sites()
        for a in db.query("SELECT * FROM alerts WHERE cleared_at IS NULL AND kind='lightning'"):
            if datetime.fromisoformat(a["data"]["all_clear_at"]) <= now:
                alert = _clear(a["id"], now)
                cleared.append(a["site_id"])
                if a["site_id"] in sites:
                    await self._emit("alert_cleared", alert, sites[a["site_id"]])
        return cleared

    # ---- condition-based alerts -----------------------------------------
    @staticmethod
    def desired(site: Site, cond: dict, hazard: dict, ctx: dict) -> dict[str, tuple]:
        out: dict[str, tuple] = {}
        cur = cond["current"]
        hi = cur["heat_index_f"]
        if hi >= config.HEAT_EXTREME_F:
            out["heat"] = ("danger", f"Extreme heat: heat index {hi:.0f}F",
                           "Stop strenuous outdoor work. Mandatory cool-down breaks, buddy checks for heat illness.", {"heat_index_f": hi})
        elif hi >= config.HEAT_DANGER_F:
            out["heat"] = ("warning", f"Heat danger: heat index {hi:.0f}F",
                           "Water every 15 min, 15 min shaded rest each hour, watch for heat exhaustion symptoms.", {"heat_index_f": hi})

        over, d24 = cond["overnight_precip_in"], cond["precip_24h_in"]
        nws_flood = [a for a in cond.get("nws_alerts", []) if "flood" in (a.get("event") or "").lower()]
        if (site.river or site.coastal) and (over >= config.FLOOD_OVERNIGHT_RAIN_IN or d24 >= config.FLOOD_24H_RAIN_IN):
            where = site.river or "coastal waters"
            out["flood"] = ("warning", f"Flood risk: {where}",
                            f"{over:.2f} in overnight / {d24:.2f} in in 24h along {where}. Check access roads and low "
                            "crossings before mobilizing; do not drive through standing water.", {"overnight_in": over, "rain_24h_in": d24})
        elif nws_flood:
            out["flood"] = ("warning", f"Flood risk: {nws_flood[0]['event']}", nws_flood[0].get("headline") or "", {})
        elif d24 >= config.FLOOD_24H_RAIN_IN:
            out["flood"] = ("advisory", "Heavy rain: ponding likely",
                            f"{d24:.2f} in in 24h. Expect ponding and washouts on unpaved access.", {"rain_24h_in": d24})

        if cur["wind_gust_mph"] >= config.WIND_GUST_MPH:
            out["wind"] = ("warning", f"High wind: gusts {cur['wind_gust_mph']:.0f} mph",
                           "Suspend aerial lifts, crane picks and conductor stringing until gusts drop below 30 mph.",
                           {"gust_mph": cur["wind_gust_mph"]})
        if cur["soil_moisture"] >= config.SOIL_SATURATED:
            out["soil"] = ("advisory", "Saturated ground",
                           "Soft, muddy ground: use matting, keep heavy equipment on the temporary road, "
                           "inspect excavations for cave-in risk.", {"soil_moisture": cur["soil_moisture"]})
        if ctx.get("fire_nearby"):
            out["wildfire"] = ("warning", "Wildfire risk nearby",
                               "Active fire or red-flag conditions within 25 mi. No hot work; keep egress routes open.", {})
        if ctx.get("wildlife"):
            kinds = ", ".join(sorted({(r.get("description") or "wildlife")[:40] for r in ctx["wildlife"]}))
            out["wildlife"] = ("advisory", f"Wildlife reported within 1 mi ({len(ctx['wildlife'])})",
                               f"Recent crew sightings: {kinds}. Keep distance, watch water edges and tall grass.",
                               {"report_ids": [r["id"] for r in ctx["wildlife"]]})
        for a in cond.get("nws_alerts", []):
            ev = a.get("event") or "NWS alert"
            if "flood" in ev.lower():
                continue
            lvl = {"Extreme": "danger", "Severe": "warning"}.get(a.get("severity"), "advisory")
            out[f"nws:{ev}"] = (lvl, f"NWS {ev}", a.get("headline") or ev, {"ends": a.get("ends")})
        if hazard["index"] >= config.HIGH_RISK_INDEX:
            drivers = [o["label"] for o in hazard["overlays"]] + [f["label"] for f in hazard["factors"][:2] if f["contribution"] > 0.3]
            top = ", ".join(drivers[:3]) or "combined conditions"
            out["hazard_index"] = ("warning", f"High hazard index {hazard['index']}/10",
                                   f"Driven by {top}. Supervisor review required before work continues.", {"index": hazard["index"]})
        return out

    async def evaluate(self, site: Site, cond: dict, hazard: dict, ctx: dict, now: datetime):
        want = self.desired(site, cond, hazard, ctx)
        current = {a["kind"]: a for a in active(site.id) if a["kind"] != "lightning"}
        for kind, (lvl, title, msg, data) in want.items():
            if kind in current:
                a = current[kind]
                if a["level"] != lvl or a["title"] != title:
                    escalated = LEVEL_ORDER[lvl] > LEVEL_ORDER[a["level"]]
                    alert = _update(a["id"], lvl, title, msg, data)
                    await self._emit("alert" if escalated else "alert_updated", alert, site)
            else:
                await self._emit("alert", _insert(site.id, kind, lvl, title, msg, data, now), site)
        for kind, a in current.items():
            if kind not in want:
                await self._emit("alert_cleared", _clear(a["id"], now), site)
