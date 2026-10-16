"""NASA FIRMS active-fire detections (needs a free FIRMS_MAP_KEY)."""
import csv
import io

import httpx

from .. import config

# Continental US + Alaska/Hawaii are out of scope for this bbox.
CONUS_BBOX = "-125,24,-66,50"


async def fetch_hotspots(client: httpx.AsyncClient) -> list[tuple[float, float]] | None:
    if not config.FIRMS_MAP_KEY:
        return None
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{config.FIRMS_MAP_KEY}/VIIRS_SNPP_NRT/{CONUS_BBOX}/1"
    r = await client.get(url, timeout=60)
    r.raise_for_status()
    rows = csv.DictReader(io.StringIO(r.text))
    return [(float(row["latitude"]), float(row["longitude"])) for row in rows]
