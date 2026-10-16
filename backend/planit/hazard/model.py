"""Hazard index (0-10): gradient-boosted model plus real-time overlays."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import train_test_split

from .. import config
from . import features as F
from . import synthetic

log = logging.getLogger(__name__)

_bundle: dict | None = None


def train(df: pd.DataFrame, source: str) -> dict:
    X, y = df[F.FEATURES], df["hazard"]
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=0)
    model = GradientBoostingRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.8, random_state=0)
    model.fit(Xtr, ytr)
    mae = float(mean_absolute_error(yte, model.predict(Xte)))
    bundle = {
        "model": model, "features": F.FEATURES, "source": source, "mae": round(mae, 3),
        "n_train": int(len(Xtr)), "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return bundle


def save(bundle: dict, path=None) -> None:
    path = path or config.MODEL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, path)


def load() -> dict:
    global _bundle
    if _bundle is None:
        if config.MODEL_PATH.exists():
            _bundle = joblib.load(config.MODEL_PATH)
        else:
            log.info("No hazard model at %s; training on synthetic threshold-labelled data", config.MODEL_PATH)
            _bundle = train(synthetic.generate(), source="synthetic")
            save(_bundle)
    return _bundle


def info() -> dict:
    b = load()
    return {k: b[k] for k in ("source", "mae", "n_train", "trained_at", "features")}


def predict_many(rows: list[dict]) -> np.ndarray:
    b = load()
    X = pd.DataFrame(rows)[b["features"]]
    return np.clip(b["model"].predict(X), 0, 10)


def explain(row: dict) -> tuple[float, list[dict]]:
    """Score plus each weather feature's contribution (score drop when that
    feature is reset to a benign baseline)."""
    variants = [row]
    keys = list(F.BASELINE)
    for k in keys:
        v = dict(row)
        v[k] = F.BASELINE[k]
        variants.append(v)
    preds = predict_many(variants)
    score = float(preds[0])
    factors = [{"feature": k, "label": F.LABELS[k], "value": round(row[k], 2),
                "contribution": round(float(preds[0] - p), 2)} for k, p in zip(keys, preds[1:])]
    factors.sort(key=lambda f: -f["contribution"])
    return score, factors


def level(index: float) -> str:
    if index >= 8:
        return "extreme"
    if index >= 6:
        return "high"
    if index >= 4:
        return "elevated"
    if index >= 2:
        return "moderate"
    return "low"


def assess(site, cond: dict, *, now: datetime, lightning_active: bool, wildlife_reports: int,
           crew_hazards: int, fire_nearby: bool) -> dict:
    """Combine the ML score with real-time signals the model can't see."""
    month = now.astimezone(site.tz).month
    ml_score, factors = explain(F.from_current(site, cond, month))
    overlays = []
    index = ml_score
    if lightning_active:
        overlays.append({"label": "Lightning within 10 mi", "effect": "floor 9.5"})
        index = max(index, 9.5)
    severe = [a for a in cond.get("nws_alerts", []) if a.get("severity") in ("Severe", "Extreme")]
    if severe:
        overlays.append({"label": f"NWS: {severe[0]['event']}", "effect": "floor 7.5"})
        index = max(index, 7.5)
    if fire_nearby:
        overlays.append({"label": "Wildfire within 25 mi / red flag", "effect": "+1.5"})
        index += 1.5
    if wildlife_reports:
        bump = min(2.0, 1.0 * wildlife_reports)
        overlays.append({"label": f"{wildlife_reports} wildlife sighting(s) nearby", "effect": f"+{bump:g}"})
        index += bump
    if crew_hazards:
        bump = min(1.5, 0.5 * crew_hazards)
        overlays.append({"label": f"{crew_hazards} crew-reported hazard(s)", "effect": f"+{bump:g}"})
        index += bump
    index = float(np.clip(index, 0, 10))

    forecast_rows = [F.from_daily(site, d) for d in cond.get("daily", [])]
    preds = predict_many(forecast_rows) if forecast_rows else []
    forecast = [{"date": d["date"], "index": round(float(p), 1), "level": level(float(p))}
                for d, p in zip(cond.get("daily", []), preds)]
    return {
        "index": round(index, 1), "level": level(index), "ml_score": round(ml_score, 1),
        "factors": factors, "overlays": overlays, "forecast": forecast,
    }
