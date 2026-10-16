"""Synthetic training data labelled by published field-safety thresholds.

Used when no historical NOAA/OSHA-labelled dataset has been built (see
backend/ml/build_dataset.py). Labels follow:
  * NWS heat index bands (caution 80, extreme caution 91, danger 103, extreme 115 F)
  * NOAA/OSHA lightning guidance (any thunder = stop work)
  * flash-flood rainfall rates, amplified near rivers and coasts
  * soil saturation (equipment bogging, trench collapse) and wind gust limits
"""
import numpy as np
import pandas as pd

from .features import FEATURES, month_terms


def expert_label(df: pd.DataFrame) -> np.ndarray:
    hi = df["heat_index_f"].to_numpy()
    heat = np.interp(hi, [80, 91, 103, 115, 125], [0, 2, 5, 8, 10])
    lightning = df["thunder"].to_numpy() * 7.0
    water = df["precip_24h_in"].to_numpy() + 0.4 * df["precip_72h_in"].to_numpy()
    exposure = 1 + 0.6 * df["near_river"].to_numpy() + 0.3 * df["coastal"].to_numpy()
    flood = np.interp(water * exposure, [0.5, 1.5, 3, 5], [0, 3, 6, 9])
    soil = np.interp(df["soil_moisture"].to_numpy(), [0.3, 0.4, 0.5], [0, 2, 4])
    wind = np.interp(df["wind_gust_mph"].to_numpy(), [20, 30, 45, 60], [0, 3, 6, 9])
    comps = np.vstack([heat, lightning, flood, soil, wind])
    top = comps.max(axis=0)
    rest = comps.sum(axis=0) - top
    return np.clip(top + 0.3 * rest, 0, 10)


def generate(n: int = 20000, seed: int = 7) -> pd.DataFrame:
    g = np.random.default_rng(seed)
    month = g.integers(1, 13, n)
    lat = g.uniform(25, 48, n)
    summer = np.cos(2 * np.pi * (month - 7) / 12)
    hi = 70 + 18 * summer - 0.6 * (lat - 25) + g.normal(0, 9, n) + 10 * (g.random(n) < 0.1)
    rainy = g.random(n) < 0.35
    p24 = np.where(rainy, g.gamma(1.3, 0.6, n), 0.0)
    p72 = p24 + np.where(g.random(n) < 0.45, g.gamma(1.2, 0.8, n), 0.0)
    thunder = (g.random(n) < 0.08 + 0.12 * np.clip(summer, 0, 1) * (lat < 33)).astype(float)
    soil = np.clip(0.18 + 0.05 * p72 + g.normal(0, 0.05, n), 0.02, 0.55)
    gust = np.clip(g.gamma(3, 5, n) + thunder * g.uniform(10, 35, n), 0, 90)
    ms, mc = zip(*(month_terms(int(m)) for m in month))
    df = pd.DataFrame({
        "heat_index_f": hi, "precip_24h_in": p24, "precip_72h_in": p72, "soil_moisture": soil,
        "wind_gust_mph": gust, "thunder": thunder,
        "near_river": (g.random(n) < 0.4).astype(float), "coastal": (g.random(n) < 0.25).astype(float),
        "month_sin": ms, "month_cos": mc, "lat": lat,
    })
    df["hazard"] = np.clip(expert_label(df) + g.normal(0, 0.35, n), 0, 10)
    return df[FEATURES + ["hazard"]]
