"""Lightning strike sources.

There is no free real-time US lightning network API, so strikes come from:
  * POST /api/lightning/strikes - plug in a commercial feed (Vaisala NLDN,
    Earth Networks, Blitzortung relay) by pushing strikes to this endpoint;
  * thunder observed in the live forecast for the current hour, which is
    treated as a strike at the site ("when thunder roars, go indoors");
  * simulated strikes around sites whose (simulated) weather has a storm.
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime

from ..geo import offset
from ..sites import Site


def simulated_strikes(site: Site, now: datetime, interval_s: int) -> list[dict]:
    seed = int(hashlib.sha256(f"{site.id}:{now.isoformat()}".encode()).hexdigest()[:8], 16)
    g = random.Random(seed)
    # ~4 strikes per minute inside a 15 mile radius during an active storm
    n = sum(1 for _ in range(max(1, int(4 * interval_s / 60))) if g.random() < 0.6)
    out = []
    for _ in range(n):
        lat, lon = offset(site.lat, site.lon, g.uniform(0.5, 15), g.uniform(0, 360))
        out.append({"lat": lat, "lon": lon, "time": now.isoformat(), "source": "simulated"})
    return out
