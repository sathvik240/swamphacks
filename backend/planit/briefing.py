"""Next-day safety briefing, generated at 6 PM site-local time."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from . import config, db, reports
from .hazard import features as F
from .hazard import model


def _hours(hs: list[int]) -> str:
    if not hs:
        return ""
    spans, start, prev = [], hs[0], hs[0]
    for h in hs[1:] + [None]:
        if h is not None and h == prev + 1:
            prev = h
            continue
        spans.append(f"{start:02d}:00-{prev + 1:02d}:00")
        if h is not None:
            start = prev = h
    return ", ".join(spans)


def build(site, cond: dict, now: datetime) -> dict:
    local = now.astimezone(site.tz)
    tomorrow = (local + timedelta(days=1)).date().isoformat()
    day = next((d for d in cond["daily"] if d["date"] == tomorrow), None)
    if day is None:
        raise ValueError(f"no forecast for {tomorrow}")
    score, factors = model.explain(F.from_daily(site, day))
    hazards, recs = [], []

    hi = day["heat_index_max_f"]
    if hi >= config.HEAT_DANGER_F:
        hazards.append(f"Heat index up to {hi:.0f}F" + (f" ({_hours(day['heat_hours'])})" if day["heat_hours"] else ""))
        recs += ["Start work at first light and schedule heavy tasks before 11:00.",
                 "Water every 15 minutes, 15 minutes shaded rest per hour; assign heat-illness buddies."]
    elif hi >= config.HEAT_CAUTION_F:
        hazards.append(f"Heat index up to {hi:.0f}F")
        recs.append("Hydration and shaded breaks; watch new or unacclimatized workers.")
    if day["thunder"]:
        hazards.append(f"Thunderstorms likely ({_hours(day['thunder_hours'])})" if day["thunder_hours"] else "Thunderstorms possible")
        recs.append("Review the lightning plan: identify safe shelter (vehicles/buildings); stop work at the first strike "
                    "within 10 mi and wait 30 minutes after the last one.")
    if day["precip_in"] + day["precip_prior_48h_in"] >= 1.5 or day["soil_moisture"] >= config.SOIL_SATURATED:
        hazards.append(f"Wet ground: {day['precip_in']:.2f} in forecast, {day['precip_prior_48h_in']:.2f} in prior 48h")
        recs.append("Stage matting and tow equipment; keep tracked equipment on the temporary road.")
        if site.river:
            hazards.append(f"Flood watch on {site.river}")
            recs.append(f"Check {site.river} gauges and access road crossings before mobilizing.")
    if day["wind_gust_max_mph"] >= config.WIND_GUST_MPH:
        hazards.append(f"Gusts to {day['wind_gust_max_mph']:.0f} mph")
        recs.append("Plan aerial lift and crane work for calm windows; re-check wind before each pick.")

    nearby = reports.near(site.lat, site.lon, 2.0, since=now - timedelta(days=7))
    for r in nearby[:5]:
        hazards.append(f"Crew report: {r['hazard_label']} {r['distance_mi']} mi away - {r['description'] or 'no details'}")
    if any(r["hazard_type"] == "wildlife" for r in nearby):
        recs.append("Wildlife reported nearby: brief crew on the location, keep clear of water edges and brush.")
    if not recs:
        recs.append("No elevated hazards forecast. Standard PPE, hydration and job briefing.")

    lvl = model.level(score)
    headline = f"{site.name} ({site.state}) - {tomorrow}: hazard {score:.1f}/10 ({lvl})"
    text = "\n".join([headline, "", "Hazards:", *(f"- {h}" for h in hazards or ["None significant"]),
                      "", "Actions:", *(f"- {r}" for r in recs)])
    return {
        "site_id": site.id, "site_name": site.name, "for_date": tomorrow,
        "hazard_index": round(score, 1), "level": lvl, "headline": headline,
        "forecast": day, "factors": factors[:4], "hazards": hazards, "recommendations": recs,
        "text": text, "data_source": cond["source"],
    }


def save(body: dict, now: datetime, replace: bool = False) -> bool:
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    with db.tx() as c:
        cur = c.execute(f"{verb} INTO briefings(site_id, for_date, body, created_at) VALUES (?,?,?,?)",
                        (body["site_id"], body["for_date"], json.dumps(body),
                         now.astimezone(timezone.utc).isoformat(timespec="seconds")))
        return cur.rowcount > 0


def exists(site_id: str, for_date: str) -> bool:
    return bool(db.query("SELECT 1 FROM briefings WHERE site_id=? AND for_date=?", (site_id, for_date)))


def due(site, now: datetime) -> bool:
    """True between 18:00 and midnight local if tomorrow's briefing hasn't gone out."""
    local = now.astimezone(site.tz)
    return local.hour >= 18 and not exists(site.id, (local + timedelta(days=1)).date().isoformat())


def list_for(site_id: str | None = None, limit: int = 50) -> list[dict]:
    if site_id:
        rows = db.query("SELECT * FROM briefings WHERE site_id=? ORDER BY for_date DESC LIMIT ?", (site_id, limit))
    else:
        rows = db.query("SELECT * FROM briefings ORDER BY id DESC LIMIT ?", (limit,))
    return rows
