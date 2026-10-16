import json
from dataclasses import dataclass, asdict
from functools import lru_cache
from zoneinfo import ZoneInfo

from . import config


@dataclass(frozen=True)
class Site:
    id: str
    name: str
    utility: str
    state: str
    county: str
    lat: float
    lon: float
    plant_type: str
    timezone: str
    river: str | None = None
    coastal: bool = False

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def to_dict(self) -> dict:
        return asdict(self)


@lru_cache
def load_sites() -> dict[str, Site]:
    raw = json.loads(config.SITES_FILE.read_text())
    return {s["id"]: Site(**s) for s in raw["sites"]}
