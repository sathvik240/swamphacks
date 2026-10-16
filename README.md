# PlanIT Control

Real-time environmental hazard monitoring for utility field crews (SwampHacks challenge).
The platform tracks 59 real U.S. power plant sites. It continuously pulls the conditions that
matter to crews, scores each site's hazard on a 0–10 scale with a machine learning model, raises
instant alerts, sends a 6 PM next-day safety briefing, and lets crews pin photo hazard reports to the map.

## What it does

| Brief requirement | Implementation |
|---|---|
| One risk picture per site, 50+ real plants | `data/sites.json`: 59 plants (EIA-860 listings) with utility, county, type, timezone, river and coast flags. Each site combines heat index, rain (24h/72h/overnight), soil saturation, wind gusts, thunder/CAPE, lightning strikes, NWS warnings, wildfire (FIRMS + red flag) and crew wildlife sightings. |
| Lightning stop-work | Any strike within 10 mi raises a **STOP WORK** alert with a 30 minute all-clear countdown. Each new strike within 10 mi restarts the countdown, and the all-clear is announced automatically (`planit/alerts.py`). |
| Flood warning after a night of heavy rain | River and coastal sites (Mississippi, Ohio, Missouri, Tennessee…) get a flood-risk warning when overnight (00–08 local) rain is ≥ 1 in or 24h rain is ≥ 2 in, or when NWS issues a flood product. |
| Other instant alerts | Heat index ≥ 103 °F / 115 °F, gusts ≥ 30 mph (aerial lift and crane limit), saturated soil, wildfire within 25 mi, wildlife reported within 1 mi, NWS warnings, hazard index ≥ 7. |
| 6 PM next-day briefing | A scheduler checks every minute and generates each site's briefing at 18:00 **in that site's own timezone**. Each briefing has the forecast hazard index, a hazard list with time windows, crew reports and recommended actions. It is pushed to dashboards and to the optional webhook. |
| Crew hazard reporting | `＋ Report hazard`: snap a photo (mobile camera), pin it on the map or use GPS, then share. The report goes out to every connected dashboard right away, feeds the site's hazard index and alerts, and is stored in a permanent location-indexed hazard database (`GET /api/reports?lat=&lon=&radius_mi=`). |
| Supervisor dashboard | Map with markers sized and colored by risk, a ranked site list, live alert toasts, and per-site detail: conditions, score drivers, 3-day forecast, alerts with countdown, reports and briefing. |
| Hazard index 0–10 (ML) | A gradient-boosted regressor (`planit/hazard/model.py`) with per-feature attributions. Real-time signals the model can't see (active lightning, severe NWS warnings, fire, crew reports) are applied as explicit overlays. It also forecasts today, tomorrow and the day after. |

## Run it

```bash
cd backend
pip install -r requirements.txt
uvicorn planit.main:app --reload        # http://localhost:8000
python -m pytest                        # 16 tests
```

The first start trains the hazard model, which takes about 10 seconds, and saves it to `data/hazard_model.joblib`.

### Data sources and modes

`PLANIT_DATA_MODE` sets where conditions come from:

- `auto` (default): live data from **Open-Meteo** (hourly forecast, past 72h, soil moisture, CAPE) and **NWS api.weather.gov** active alerts. If the live APIs can't be reached, it falls back to simulation per site, and the dashboard shows which source is in use.
- `live`: live APIs only.
- `sim`: deterministic, climatology-based simulation, for offline demos and tests.

Optional settings:

| Env var | Purpose |
|---|---|
| `FIRMS_MAP_KEY` | NASA FIRMS key for active-fire detections (free). |
| `PLANIT_ALERT_WEBHOOK` | Every alert, all-clear, crew report and briefing is POSTed here as `{"text": ..., "planit": {...}}`. This works with a Slack incoming webhook or an SMS relay. |
| `PLANIT_CONDITIONS_INTERVAL` / `PLANIT_LIGHTNING_INTERVAL` | Refresh cadence in seconds (defaults: 600 / 30). |

**Lightning:** no free real-time U.S. lightning API exists. Strikes enter through three paths:

1. `POST /api/lightning/strikes` with `[{lat, lon, time}]`, where a commercial feed (Vaisala NLDN, Earth Networks, a Blitzortung relay) can be plugged in.
2. Thunder in the live forecast for the current hour, treated as a strike at the site ("when thunder roars, go indoors").
3. Simulated strikes during simulated storms.

### Demo script

Open a site's panel and use **Demo scenarios**, or call the API directly:

```bash
curl -XPOST localhost:8000/api/demo/scenario -H 'content-type: application/json' \
     -d '{"site_id":"turkey-point-fl","kind":"storm"}'        # lightning stop-work + countdown
curl -XPOST localhost:8000/api/demo/scenario -H 'content-type: application/json' \
     -d '{"site_id":"grand-gulf-ms","kind":"heavy_rain"}'     # Mississippi River flood warning
```

`kind` can be `storm`, `heavy_rain`, `heat` or `wildfire`.

## The hazard model

Features (shared by training and inference, `planit/hazard/features.py`): max heat index, rain over 24h and 72h, soil moisture, max wind gust, thunder, river proximity, coastal exposure, season and latitude.

- **Default training data is synthetic.** It is labelled with published safety thresholds: NWS heat-index bands, the NOAA/OSHA lightning rule, flash-flood rainfall rates, soil saturation and wind limits (`planit/hazard/synthetic.py`). This lets the app run out of the box, but the model only reproduces those rules.
- **Historical training** uses outcomes tied to the hazards crews actually face:
  ```bash
  # NOAA Storm Events details CSVs: https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/
  python -m ml.build_dataset --storm-events data/raw/StormEvents_details-*_d2023_*.csv.gz \
      --start 2023-01-01 --end 2023-12-31 [--osha data/raw/severeinjury.csv] --out data/training.csv
  python -m ml.train --data data/training.csv [--blend 0.3]
  ```
  Labels come from Storm Events (lightning, heat, flash flood, thunderstorm wind, wildfire, …) within 25 mi of each site, weighted by danger plus direct injuries and deaths. OSHA Severe Injury Reports can optionally be added using keyword weights. These labels are joined to ERA5 weather from the Open-Meteo archive for the same site and day.
- `GET /api/model` shows which data the loaded model was trained on and its holdout MAE.

## API

`GET /api/sites` (ranked) · `GET /api/sites/{id}` · `POST /api/sites/{id}/refresh` · `GET /api/alerts` ·
`GET|POST /api/reports` · `GET /api/reports/types` · `POST /api/lightning/strikes` · `GET /api/briefings` ·
`POST /api/sites/{id}/briefing` · `GET /api/model` · `POST|DELETE /api/demo/scenario` · `WS /ws`
(`site_update`, `alert`, `alert_updated`, `alert_cleared`, `strikes`, `report`, `briefing`).

## Layout

```
backend/planit/     FastAPI app, monitor loops, alert engine, reports, briefings
backend/planit/providers/  Open-Meteo, NWS, NASA FIRMS, lightning, simulator
backend/planit/hazard/     features, synthetic labels, model + explanations
backend/ml/         historical dataset builder (NOAA/OSHA + ERA5) and trainer
backend/scripts/    import_eia860m.py: regenerate sites from the latest EIA-860M workbook
frontend/           dashboard (vanilla JS + Leaflet, no build step)
data/sites.json     monitored job sites
```

Site coordinates are approximate, compiled from public EIA-860 listings. To refresh them from the official
EIA-860M workbook, run `python backend/scripts/import_eia860m.py <workbook.xlsx>`.
