"""Crew hazard reports: photo + map pin, shared to the team and kept as a
location hazard database for future projects."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from . import config, db
from .geo import haversine_mi
from .sites import load_sites

HAZARD_TYPES = {
    "wildlife": "Wildlife (gator, snake, wasps...)",
    "flooding": "Flooding / standing water",
    "soft_ground": "Mud / soft ground / stuck equipment",
    "downed_line": "Downed line / electrical",
    "heat_illness": "Heat illness",
    "fire": "Fire / smoke",
    "lightning": "Lightning / storm",
    "terrain": "Terrain / sinkhole / washout",
    "other": "Other",
}

_SIGNATURES = {b"\xff\xd8\xff": ".jpg", b"\x89PNG\r\n\x1a\n": ".png", b"RIFF": ".webp", b"GIF8": ".gif"}


class ReportError(ValueError):
    pass


def photo_ext(data: bytes) -> str:
    for sig, ext in _SIGNATURES.items():
        if data.startswith(sig):
            if ext == ".webp" and data[8:12] != b"WEBP":
                continue
            return ext
    raise ReportError("photo must be a JPEG, PNG, WebP or GIF image")


def nearest_site(lat: float, lon: float, max_mi: float = 10.0) -> str | None:
    best = min(load_sites().values(), key=lambda s: haversine_mi(lat, lon, s.lat, s.lon))
    return best.id if haversine_mi(lat, lon, best.lat, best.lon) <= max_mi else None


def create(*, lat: float, lon: float, hazard_type: str, severity: int, description: str | None,
           reporter: str | None, site_id: str | None, photo: bytes | None, now: datetime | None = None) -> dict:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ReportError("invalid coordinates")
    if hazard_type not in HAZARD_TYPES:
        raise ReportError(f"hazard_type must be one of {sorted(HAZARD_TYPES)}")
    if not 1 <= severity <= 5:
        raise ReportError("severity must be 1-5")
    if site_id and site_id not in load_sites():
        raise ReportError("unknown site_id")
    site_id = site_id or nearest_site(lat, lon)
    filename = None
    if photo:
        if len(photo) > config.MAX_PHOTO_BYTES:
            raise ReportError("photo too large (max 8 MB)")
        filename = f"{uuid.uuid4().hex}{photo_ext(photo)}"
        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        (config.UPLOAD_DIR / filename).write_bytes(photo)
    now = now or datetime.now(timezone.utc)
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO reports(site_id, lat, lon, hazard_type, severity, description, reporter, photo, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (site_id, lat, lon, hazard_type, severity, (description or "")[:500], (reporter or "")[:80] or None,
             filename, now.isoformat(timespec="seconds")))
        rid = cur.lastrowid
    return get(rid)


def _decorate(r: dict) -> dict:
    r["photo_url"] = f"/uploads/{r['photo']}" if r.get("photo") else None
    r["hazard_label"] = HAZARD_TYPES.get(r["hazard_type"], r["hazard_type"])
    return r


def get(rid: int) -> dict:
    return _decorate(db.query("SELECT * FROM reports WHERE id=?", (rid,))[0])


def near(lat: float, lon: float, radius_mi: float, since: datetime | None = None,
         hazard_type: str | None = None, limit: int = 200) -> list[dict]:
    dlat = radius_mi / 69.0
    dlon = radius_mi / 50.0          # conservative for latitudes below ~43 deg; filtered precisely below
    sql = "SELECT * FROM reports WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?"
    params: list = [lat - dlat, lat + dlat, lon - dlon * 1.5, lon + dlon * 1.5]
    if since:
        sql += " AND created_at >= ?"
        params.append(since.isoformat(timespec="seconds"))
    if hazard_type:
        sql += " AND hazard_type = ?"
        params.append(hazard_type)
    sql += " ORDER BY created_at DESC"
    out = []
    for r in db.query(sql, params):
        d = haversine_mi(lat, lon, r["lat"], r["lon"])
        if d <= radius_mi:
            r["distance_mi"] = round(d, 2)
            out.append(_decorate(r))
            if len(out) >= limit:
                break
    return out


def recent(limit: int = 100) -> list[dict]:
    return [_decorate(r) for r in db.query("SELECT * FROM reports ORDER BY id DESC LIMIT ?", (limit,))]


def site_context(site, now: datetime) -> dict:
    """Reports that feed the hazard index and wildlife alerts for a site."""
    recent_reports = near(site.lat, site.lon, config.WILDLIFE_RADIUS_MI, since=now - timedelta(hours=48))
    return {
        "wildlife": [r for r in recent_reports if r["hazard_type"] == "wildlife"],
        "crew_hazards": [r for r in recent_reports if r["hazard_type"] != "wildlife" and r["severity"] >= 3],
    }
