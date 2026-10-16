import pandas as pd

from ml.build_dataset import label_site, load_storm_events
from planit.sites import load_sites


def test_storm_events_labels(tmp_path):
    site = load_sites()["grand-gulf-ms"]
    csv = tmp_path / "se.csv"
    pd.DataFrame([
        {"BEGIN_DATE_TIME": "12-AUG-23 15:10:00", "EVENT_TYPE": "Lightning", "BEGIN_LAT": site.lat + 0.1,
         "BEGIN_LON": site.lon, "INJURIES_DIRECT": 1, "DEATHS_DIRECT": 0, "CZ_TIMEZONE": "CST-6"},
        {"BEGIN_DATE_TIME": "12-AUG-23 16:00:00", "EVENT_TYPE": "Flash Flood", "BEGIN_LAT": site.lat,
         "BEGIN_LON": site.lon + 0.1, "INJURIES_DIRECT": 0, "DEATHS_DIRECT": 0, "CZ_TIMEZONE": "CST-6"},
        {"BEGIN_DATE_TIME": "13-AUG-23 16:00:00", "EVENT_TYPE": "Lightning", "BEGIN_LAT": site.lat + 3,
         "BEGIN_LON": site.lon, "INJURIES_DIRECT": 0, "DEATHS_DIRECT": 0, "CZ_TIMEZONE": "CST-6"},
        {"BEGIN_DATE_TIME": "13-AUG-23 16:00:00", "EVENT_TYPE": "Drought", "BEGIN_LAT": site.lat,
         "BEGIN_LON": site.lon, "INJURIES_DIRECT": 0, "DEATHS_DIRECT": 0, "CZ_TIMEZONE": "CST-6"},
    ]).to_csv(csv, index=False)
    labels = label_site(site, load_storm_events([str(csv)]))
    assert list(labels.index.astype(str)) == ["2023-08-12"]
    assert 7 < labels.iloc[0] < 8.5   # lightning w/ injury (7) + flash flood (4), log-scaled
