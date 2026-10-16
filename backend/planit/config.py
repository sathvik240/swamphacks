"""Runtime configuration, read from environment variables."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("PLANIT_DATA_DIR", ROOT / "data"))
SITES_FILE = Path(os.getenv("PLANIT_SITES_FILE", DATA_DIR / "sites.json"))
DB_PATH = Path(os.getenv("PLANIT_DB", DATA_DIR / "planit.db"))
UPLOAD_DIR = Path(os.getenv("PLANIT_UPLOAD_DIR", DATA_DIR / "uploads"))
MODEL_PATH = Path(os.getenv("PLANIT_MODEL", DATA_DIR / "hazard_model.joblib"))
FRONTEND_DIR = ROOT / "frontend"

# "auto": try live APIs, fall back to simulation per site on failure.
# "live": live APIs only. "sim": simulation only (offline demos, tests).
DATA_MODE = os.getenv("PLANIT_DATA_MODE", "auto").lower()

# How often environmental conditions are refreshed for every site (seconds).
CONDITIONS_INTERVAL = int(os.getenv("PLANIT_CONDITIONS_INTERVAL", "600"))
# How often the lightning feed is polled (seconds).
LIGHTNING_INTERVAL = int(os.getenv("PLANIT_LIGHTNING_INTERVAL", "30"))

# Optional outbound notifications: JSON POST of every alert and briefing
# (e.g. a Slack incoming webhook or a Twilio/SMS relay).
ALERT_WEBHOOK = os.getenv("PLANIT_ALERT_WEBHOOK")
# Optional NASA FIRMS key for active-fire detections.
FIRMS_MAP_KEY = os.getenv("FIRMS_MAP_KEY")

USER_AGENT = os.getenv("PLANIT_USER_AGENT", "PlanIT-Control/1.0 (swamphacks; contact@example.com)")

# Safety thresholds
LIGHTNING_RADIUS_MI = 10.0
LIGHTNING_ALL_CLEAR_MIN = 30
FLOOD_OVERNIGHT_RAIN_IN = 1.0      # rain between 00:00 and 08:00 local
FLOOD_24H_RAIN_IN = 2.0
HEAT_CAUTION_F = 91.0              # NWS heat index: caution/extreme caution
HEAT_DANGER_F = 103.0
HEAT_EXTREME_F = 115.0
WIND_GUST_MPH = 30.0               # common aerial lift / crane limit
SOIL_SATURATED = 0.40              # volumetric soil moisture m3/m3
WILDLIFE_RADIUS_MI = 1.0
WILDFIRE_RADIUS_MI = 25.0
HIGH_RISK_INDEX = 7.0

MAX_PHOTO_BYTES = 8 * 1024 * 1024
