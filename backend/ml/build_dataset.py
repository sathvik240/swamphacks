"""Build a historical, outcome-labelled training set for the hazard model.

Labels come from NOAA Storm Events (and optionally OSHA Severe Injury Reports):
for every site and day, events within RADIUS_MI are weighted by how dangerous
they are to outdoor crews; injuries and deaths add weight. Features for the same
site/day come from the Open-Meteo historical (ERA5) archive, matching the
features the live model sees.

Usage:
  # StormEvents_details-ftp_v1.0_dYYYY_*.csv.gz from
  # https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/
  python -m ml.build_dataset --storm-events data/raw/StormEvents_details-*_d2023_*.csv.gz \
      --start 2023-01-01 --end 2023-12-31 --out data/training.csv \
      [--osha data/raw/severeinjury.csv]
"""
from __future__ import annotations

import argparse
import math
import time

import httpx
import numpy as np
import pandas as pd

from planit.geo import haversine_mi
from planit.hazard import features as F
from planit.providers.weather import heat_index_f
from planit.sites import load_sites

RADIUS_MI = 25.0
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Danger of each NOAA event type to an outdoor utility crew (0-5).
EVENT_WEIGHTS = {
    "Lightning": 5, "Excessive Heat": 4, "Heat": 3, "Flash Flood": 4, "Flood": 3, "Coastal Flood": 2,
    "Thunderstorm Wind": 3, "Tornado": 5, "Hail": 2, "Wildfire": 4, "Heavy Rain": 2, "High Wind": 3,
    "Strong Wind": 2, "Tropical Storm": 4, "Hurricane (Typhoon)": 5, "Storm Surge/Tide": 3, "Dense Fog": 1,
}
OSHA_KEYWORDS = {"heat": 4, "lightning": 5, "snake": 3, "alligator": 4, "insect": 2, "mud": 2, "flood": 3, "drown": 4}


def load_storm_events(paths: list[str]) -> pd.DataFrame:
    cols = ["BEGIN_DATE_TIME", "EVENT_TYPE", "BEGIN_LAT", "BEGIN_LON", "INJURIES_DIRECT", "DEATHS_DIRECT", "CZ_TIMEZONE"]
    df = pd.concat([pd.read_csv(p, usecols=cols, low_memory=False) for p in paths], ignore_index=True)
    df = df[df["EVENT_TYPE"].isin(EVENT_WEIGHTS)].dropna(subset=["BEGIN_LAT", "BEGIN_LON"])
    df["date"] = pd.to_datetime(df["BEGIN_DATE_TIME"], format="%d-%b-%y %H:%M:%S").dt.date
    df["weight"] = df["EVENT_TYPE"].map(EVENT_WEIGHTS) + 2 * df["INJURIES_DIRECT"].clip(upper=5) + 4 * df["DEATHS_DIRECT"].clip(upper=3)
    return df.rename(columns={"BEGIN_LAT": "lat", "BEGIN_LON": "lon"})[["date", "lat", "lon", "weight", "EVENT_TYPE"]]


def load_osha(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="latin-1", low_memory=False)
    df = df.dropna(subset=["Latitude", "Longitude"])
    text = (df.get("Final Narrative", "").astype(str) + " " + df.get("NatureTitle", "").astype(str)).str.lower()
    weight = np.zeros(len(df))
    for kw, w in OSHA_KEYWORDS.items():
        weight = np.maximum(weight, np.where(text.str.contains(kw), w, 0))
    out = pd.DataFrame({"date": pd.to_datetime(df["EventDate"]).dt.date, "lat": df["Latitude"], "lon": df["Longitude"],
                        "weight": weight + 2, "EVENT_TYPE": "OSHA"})
    return out[weight > 0]


def label_site(site, events: pd.DataFrame) -> pd.Series:
    near = events[(events["lat"].sub(site.lat).abs() < 0.5) & (events["lon"].sub(site.lon).abs() < 0.6)]
    d = near.apply(lambda r: haversine_mi(site.lat, site.lon, r["lat"], r["lon"]), axis=1) if len(near) else pd.Series(dtype=float)
    near = near[d <= RADIUS_MI]
    per_day = near.groupby("date")["weight"].sum()
    # log-scaled to 0-10: one lightning strike event ~5, a bad multi-event day saturates near 10.
    return np.clip(10 * np.log1p(per_day) / np.log1p(25), 0, 10)


def fetch_archive(site, start: str, end: str) -> pd.DataFrame:
    params = {
        "latitude": site.lat, "longitude": site.lon, "start_date": start, "end_date": end,
        "hourly": "temperature_2m,relative_humidity_2m,precipitation,wind_gusts_10m,weather_code,soil_moisture_0_to_7cm",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
        "timezone": site.timezone,
    }
    for attempt in range(4):
        try:
            r = httpx.get(ARCHIVE_URL, params=params, timeout=120)
            r.raise_for_status()
            break
        except httpx.HTTPError:
            if attempt == 3:
                raise
            time.sleep(2 ** (attempt + 1))
    h = pd.DataFrame(r.json()["hourly"])
    h["time"] = pd.to_datetime(h["time"])
    h["hi"] = [heat_index_f(t, rh) if not (math.isnan(t) or math.isnan(rh)) else np.nan
               for t, rh in zip(h["temperature_2m"], h["relative_humidity_2m"])]
    h["thunder"] = h["weather_code"].isin([95, 96, 99])
    daily = h.groupby(h["time"].dt.date).agg(
        heat_index_f=("hi", "max"), precip_24h_in=("precipitation", "sum"), wind_gust_mph=("wind_gusts_10m", "max"),
        thunder=("thunder", "any"), soil_moisture=("soil_moisture_0_to_7cm", "mean"))
    daily["precip_72h_in"] = daily["precip_24h_in"].rolling(3, min_periods=1).sum()
    return daily.dropna()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--storm-events", nargs="+", required=True)
    ap.add_argument("--osha")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default="data/training.csv")
    args = ap.parse_args()

    events = load_storm_events(args.storm_events)
    if args.osha:
        events = pd.concat([events, load_osha(args.osha)], ignore_index=True)
    rows = []
    for site in load_sites().values():
        print(f"{site.name}: fetching archive", flush=True)
        daily = fetch_archive(site, args.start, args.end)
        labels = label_site(site, events)
        for day, w in daily.iterrows():
            feats = F.build(heat_index_f=w.heat_index_f, precip_24h_in=w.precip_24h_in, precip_72h_in=w.precip_72h_in,
                            soil_moisture=w.soil_moisture, wind_gust_mph=w.wind_gust_mph, thunder=w.thunder,
                            near_river=site.river is not None, coastal=site.coastal, month=day.month, lat=site.lat)
            rows.append({**feats, "hazard": float(labels.get(day, 0.0)), "site_id": site.id, "date": day})
        time.sleep(0.5)   # be polite to the free archive API
    out = pd.DataFrame(rows)
    out.to_csv(args.out, index=False)
    print(f"wrote {len(out)} rows ({(out['hazard'] > 0).mean():.1%} with a hazard event) to {args.out}")


if __name__ == "__main__":
    main()
