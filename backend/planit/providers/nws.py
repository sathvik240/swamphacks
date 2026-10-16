"""Active National Weather Service alerts for a point (api.weather.gov)."""
import httpx

from .. import config
from ..sites import Site

URL = "https://api.weather.gov/alerts/active"


async def fetch_alerts(client: httpx.AsyncClient, site: Site) -> list[dict]:
    r = await client.get(URL, params={"point": f"{site.lat:.4f},{site.lon:.4f}"},
                         headers={"User-Agent": config.USER_AGENT, "Accept": "application/geo+json"}, timeout=20)
    r.raise_for_status()
    out = []
    for f in r.json().get("features", []):
        p = f["properties"]
        out.append({
            "event": p.get("event"), "severity": p.get("severity"),
            "headline": p.get("headline"), "ends": p.get("ends") or p.get("expires"),
        })
    return out


def scenario_alerts(scenario: dict | None) -> list[dict]:
    """Synthetic NWS-style products for demo scenarios, clearly marked."""
    if not scenario:
        return []
    events = {
        "wildfire": ("Red Flag Warning", "Severe"),
        "heavy_rain": ("Flood Watch", "Moderate"),
        "storm": ("Severe Thunderstorm Warning", "Severe"),
        "heat": ("Excessive Heat Warning", "Extreme"),
    }
    ev = events.get(scenario["kind"])
    if not ev:
        return []
    return [{"event": ev[0], "severity": ev[1], "headline": f"{ev[0]} (demo scenario)", "ends": None, "simulated": True}]
