"""Feature vector shared by training (historical daily data) and inference."""
import math

FEATURES = [
    "heat_index_f", "precip_24h_in", "precip_72h_in", "soil_moisture", "wind_gust_mph",
    "thunder", "near_river", "coastal", "month_sin", "month_cos", "lat",
]

LABELS = {
    "heat_index_f": "Heat index", "precip_24h_in": "Rain (24h)", "precip_72h_in": "Rain (72h)",
    "soil_moisture": "Soil saturation", "wind_gust_mph": "Wind gusts", "thunder": "Thunderstorms",
    "near_river": "River proximity", "coastal": "Coastal exposure",
    "month_sin": "Season", "month_cos": "Season", "lat": "Latitude",
}

# Benign reference values used to attribute a score to individual features.
BASELINE = {
    "heat_index_f": 75.0, "precip_24h_in": 0.0, "precip_72h_in": 0.0, "soil_moisture": 0.2,
    "wind_gust_mph": 10.0, "thunder": 0.0,
}


def month_terms(month: int) -> tuple[float, float]:
    a = 2 * math.pi * (month - 1) / 12
    return math.sin(a), math.cos(a)


def build(*, heat_index_f, precip_24h_in, precip_72h_in, soil_moisture, wind_gust_mph, thunder,
          near_river, coastal, month, lat) -> dict:
    ms, mc = month_terms(month)
    return {
        "heat_index_f": float(heat_index_f), "precip_24h_in": float(precip_24h_in),
        "precip_72h_in": float(precip_72h_in), "soil_moisture": float(soil_moisture),
        "wind_gust_mph": float(wind_gust_mph), "thunder": float(bool(thunder)),
        "near_river": float(bool(near_river)), "coastal": float(bool(coastal)),
        "month_sin": ms, "month_cos": mc, "lat": float(lat),
    }


def from_current(site, cond: dict, month: int) -> dict:
    c = cond["current"]
    return build(heat_index_f=c["heat_index_f"], precip_24h_in=cond["precip_24h_in"],
                 precip_72h_in=cond["precip_72h_in"], soil_moisture=c["soil_moisture"],
                 wind_gust_mph=c["wind_gust_mph"], thunder=c["thunder"],
                 near_river=site.river is not None, coastal=site.coastal, month=month, lat=site.lat)


def from_daily(site, day: dict) -> dict:
    month = int(day["date"][5:7])
    return build(heat_index_f=day["heat_index_max_f"], precip_24h_in=day["precip_in"],
                 precip_72h_in=day["precip_in"] + day["precip_prior_48h_in"],
                 soil_moisture=day["soil_moisture"], wind_gust_mph=day["wind_gust_max_mph"],
                 thunder=day["thunder"], near_river=site.river is not None, coastal=site.coastal,
                 month=month, lat=site.lat)
