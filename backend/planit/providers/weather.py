"""Hourly weather for a site: Open-Meteo when reachable, simulation otherwise.

Both sources produce the same `HourlySeries` (local times, 72h back and 72h
ahead) which `derive_conditions` turns into the per-site condition summary used
by the alert engine, the hazard model and the briefings.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
import numpy as np

from ..sites import Site

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARS = [
    "temperature_2m", "relative_humidity_2m", "precipitation", "precipitation_probability",
    "wind_gusts_10m", "weather_code", "cape",
    "soil_moisture_0_to_1cm", "soil_moisture_1_to_3cm", "soil_moisture_3_to_9cm",
]
THUNDER_CODES = {95, 96, 99}


@dataclass
class HourlySeries:
    times: list[datetime]          # naive local times
    temp_f: list[float]
    rh: list[float]
    precip_in: list[float]
    precip_prob: list[float]
    gust_mph: list[float]
    weather_code: list[int]
    cape: list[float]
    soil: list[float]
    source: str = "open-meteo"
    notes: list[str] = field(default_factory=list)


def heat_index_f(temp_f: float, rh: float) -> float:
    """NWS heat index (Rothfusz regression with the standard adjustments)."""
    t, r = temp_f, rh
    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + r * 0.094)
    if (simple + t) / 2 < 80:
        return simple
    hi = (-42.379 + 2.04901523 * t + 10.14333127 * r - 0.22475541 * t * r
          - 6.83783e-3 * t * t - 5.481717e-2 * r * r + 1.22874e-3 * t * t * r
          + 8.5282e-4 * t * r * r - 1.99e-6 * t * t * r * r)
    if r < 13 and 80 <= t <= 112:
        hi -= ((13 - r) / 4) * math.sqrt((17 - abs(t - 95)) / 17)
    elif r > 85 and 80 <= t <= 87:
        hi += ((r - 85) / 10) * ((87 - t) / 5)
    return hi


async def fetch_open_meteo(client: httpx.AsyncClient, site: Site) -> HourlySeries:
    params = {
        "latitude": site.lat, "longitude": site.lon,
        "hourly": ",".join(HOURLY_VARS),
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
        "timezone": site.timezone, "past_days": 3, "forecast_days": 4,
    }
    r = await client.get(OPEN_METEO_URL, params=params, timeout=20)
    r.raise_for_status()
    h = r.json()["hourly"]

    def col(name, default=0.0):
        return [default if v is None else v for v in h.get(name, [])] or [default] * len(h["time"])

    soil = [np.nanmean([a, b, c]) for a, b, c in zip(
        col("soil_moisture_0_to_1cm", np.nan), col("soil_moisture_1_to_3cm", np.nan), col("soil_moisture_3_to_9cm", np.nan))]
    return HourlySeries(
        times=[datetime.fromisoformat(t) for t in h["time"]],
        temp_f=col("temperature_2m"), rh=col("relative_humidity_2m", 50.0),
        precip_in=col("precipitation"), precip_prob=col("precipitation_probability"),
        gust_mph=col("wind_gusts_10m"), weather_code=[int(x) for x in col("weather_code", 0)],
        cape=col("cape"), soil=[0.25 if math.isnan(s) else float(s) for s in soil],
        source="open-meteo",
    )


def _rng(site: Site, day) -> np.random.Generator:
    seed = int(hashlib.sha256(f"{site.id}:{day.isoformat()}".encode()).hexdigest()[:12], 16)
    return np.random.default_rng(seed)


def simulate(site: Site, now_local: datetime) -> HourlySeries:
    """Plausible, deterministic weather from climatology + seeded daily regimes.

    The same site/day always yields the same weather so refreshes are stable.
    """
    start = now_local.replace(minute=0, second=0, microsecond=0, tzinfo=None) - timedelta(hours=72)
    humid = site.lon > -100          # Gulf/Atlantic moisture east of the Plains
    times, temp, rh, pr, prob, gust, code, cape, soil = [], [], [], [], [], [], [], [], []
    moisture = 0.22 if humid else 0.12
    day_cache: dict = {}
    for i in range(72 + 96):
        t = start + timedelta(hours=i)
        d = t.date()
        if d not in day_cache:
            g = _rng(site, d)
            doy = t.timetuple().tm_yday
            season = math.cos(2 * math.pi * (doy - 200) / 365.0)       # +1 late July
            tmean = 74 - 1.0 * (site.lat - 25) + season * (8 + 0.55 * (site.lat - 25))
            storm_p = (0.45 if humid else 0.08) * max(season, 0.1) ** 1.5 * (1.2 if site.lat < 31 else 0.7)
            day_cache[d] = {
                "tmean": tmean + g.normal(0, 3),
                "storm": g.random() < storm_p,
                "storm_hour": int(g.integers(13, 19)),
                "storm_len": int(g.integers(1, 3)),
                "storm_rate": float(g.uniform(0.1, 0.6)),
                "night_rain": g.random() < (0.08 if humid else 0.03),
                "breeze": float(g.uniform(6, 16)),
                "noise": g,
            }
        dc = day_cache[d]
        g = dc["noise"]
        diurnal = math.cos(2 * math.pi * (t.hour - 15) / 24.0)
        tf = dc["tmean"] + 9 * diurnal + g.normal(0, 0.6)
        h = (78 if humid else 30) - 18 * diurnal + g.normal(0, 3)
        in_storm = dc["storm"] and dc["storm_hour"] <= t.hour < dc["storm_hour"] + dc["storm_len"]
        night = dc["night_rain"] and 1 <= t.hour <= 6
        p = dc["storm_rate"] * g.uniform(0.5, 1.2) if in_storm else (g.uniform(0.02, 0.15) if night else 0.0)
        if in_storm:
            tf -= 8
            h = min(h + 15, 98)
        moisture = min(0.5, moisture * 0.997 + p * 0.06)
        times.append(t)
        temp.append(round(tf, 1))
        rh.append(float(np.clip(h, 8, 100)))
        pr.append(round(float(p), 2))
        prob.append(85.0 if in_storm else (60.0 if night else (40.0 if dc["storm"] else 10.0)))
        gust.append(round(dc["breeze"] + 6 * max(diurnal, 0) + (g.uniform(8, 30) if in_storm else 0), 1))
        code.append(95 if in_storm else (61 if night else (2 if diurnal > 0 else 0)))
        cape.append(float(g.uniform(1800, 3200)) if in_storm else float(max(0, 900 * diurnal * (1 if humid else 0.3))))
        soil.append(round(moisture + (0.12 if humid else 0.0), 3))
    return HourlySeries(times, temp, rh, pr, prob, gust, code, cape, soil, source="simulated")


def apply_scenario(series: HourlySeries, scenario: dict | None, now_local: datetime) -> None:
    """Overlay a demo scenario (storm, heavy_rain, heat, wildfire) on the series."""
    if not scenario:
        return
    kind = scenario["kind"]
    now_h = now_local.replace(minute=0, second=0, microsecond=0, tzinfo=None)
    for i, t in enumerate(series.times):
        dh = (t - now_h).total_seconds() / 3600
        if kind == "storm" and -1 <= dh <= 2:
            series.weather_code[i], series.cape[i] = 95, 3000.0
            series.precip_in[i], series.gust_mph[i], series.precip_prob[i] = 0.8, 52.0, 95.0
        elif kind == "heavy_rain" and t.date() == now_h.date() and 0 <= t.hour < 8 and dh <= 0:
            series.precip_in[i], series.weather_code[i], series.precip_prob[i] = 0.45, 65, 100.0
            series.soil[i] = 0.47
        elif kind == "heat" and abs(dh) <= 30 and 10 <= t.hour <= 18:
            series.temp_f[i], series.rh[i] = 101.0, 55.0
    if kind == "heavy_rain":
        for i, t in enumerate(series.times):
            if t >= now_h:
                series.soil[i] = max(series.soil[i], 0.46)
    series.notes.append(f"demo scenario: {kind}")


def derive_conditions(series: HourlySeries, now_local: datetime) -> dict:
    now_h = now_local.replace(minute=0, second=0, microsecond=0, tzinfo=None)
    idx = min(range(len(series.times)), key=lambda i: abs((series.times[i] - now_h).total_seconds()))
    hi = [heat_index_f(t, r) for t, r in zip(series.temp_f, series.rh)]

    def window(hours_back):
        lo = max(0, idx - hours_back + 1)
        return sum(series.precip_in[lo:idx + 1])

    today = now_h.date()
    overnight = sum(p for t, p in zip(series.times, series.precip_in)
                    if t.date() == today and 0 <= t.hour < 8 and t <= now_h)

    daily = []
    for offset_days in range(3):
        d = today + timedelta(days=offset_days)
        ix = [i for i, t in enumerate(series.times) if t.date() == d]
        if not ix:
            continue
        prior = [i for i, t in enumerate(series.times) if d - timedelta(days=3) < t.date() < d]
        daily.append({
            "date": d.isoformat(),
            "temp_max_f": round(max(series.temp_f[i] for i in ix), 1),
            "heat_index_max_f": round(max(hi[i] for i in ix), 1),
            "precip_in": round(sum(series.precip_in[i] for i in ix), 2),
            "precip_prior_48h_in": round(sum(series.precip_in[i] for i in prior), 2),
            "precip_prob_max": max(series.precip_prob[i] for i in ix),
            "wind_gust_max_mph": round(max(series.gust_mph[i] for i in ix), 1),
            "thunder": any(series.weather_code[i] in THUNDER_CODES for i in ix),
            "thunder_hours": [series.times[i].hour for i in ix if series.weather_code[i] in THUNDER_CODES],
            "heat_hours": [series.times[i].hour for i in ix if hi[i] >= 103],
            "soil_moisture": round(float(np.mean([series.soil[i] for i in ix])), 3),
        })

    hourly = [{
        "time": series.times[i].isoformat(timespec="minutes"),
        "heat_index_f": round(hi[i], 1), "precip_in": series.precip_in[i],
        "precip_prob": series.precip_prob[i], "gust_mph": series.gust_mph[i],
        "thunder": series.weather_code[i] in THUNDER_CODES,
    } for i in range(idx, min(idx + 48, len(series.times)))]

    return {
        "source": series.source,
        "notes": series.notes,
        "local_time": now_local.isoformat(timespec="minutes"),
        "current": {
            "temp_f": round(series.temp_f[idx], 1),
            "heat_index_f": round(hi[idx], 1),
            "humidity": round(series.rh[idx]),
            "wind_gust_mph": round(series.gust_mph[idx], 1),
            "precip_in_1h": series.precip_in[idx],
            "soil_moisture": round(series.soil[idx], 3),
            "cape": round(series.cape[idx]),
            "weather_code": series.weather_code[idx],
            "thunder": series.weather_code[idx] in THUNDER_CODES,
        },
        "precip_24h_in": round(window(24), 2),
        "precip_72h_in": round(window(72), 2),
        "overnight_precip_in": round(overnight, 2),
        "daily": daily,
        "hourly": hourly,
    }
