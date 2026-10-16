import os
import sys
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="planit-test-"))
os.environ.update(PLANIT_DATA_MODE="sim", PLANIT_DB=str(_tmp / "t.db"), PLANIT_MODEL=str(_tmp / "m.joblib"),
                  PLANIT_UPLOAD_DIR=str(_tmp / "up"), PLANIT_CONDITIONS_INTERVAL="3600", PLANIT_LIGHTNING_INTERVAL="3600")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from planit import db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db.reset(tmp_path / "planit.db")
    yield
    db.reset()


class FakeHub:
    def __init__(self):
        self.events = []

    async def broadcast(self, t, payload):
        self.events.append((t, payload))

    def notify_bg(self, text, payload):
        self.events.append(("notify", text))


@pytest.fixture
def hub():
    return FakeHub()
