"""SQLite persistence for crew reports, alerts, briefings and lightning strikes."""
import json
import sqlite3
import threading
from contextlib import contextmanager

from . import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    hazard_type TEXT NOT NULL,
    severity INTEGER NOT NULL,
    description TEXT,
    reporter TEXT,
    photo TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_latlon ON reports(lat, lon);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    level TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    data TEXT,
    created_at TEXT NOT NULL,
    cleared_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_site ON alerts(site_id, kind, cleared_at);
CREATE TABLE IF NOT EXISTS briefings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL,
    for_date TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(site_id, for_date)
);
CREATE TABLE IF NOT EXISTS strikes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    time TEXT NOT NULL,
    source TEXT
);
CREATE INDEX IF NOT EXISTS idx_strikes_time ON strikes(time);
"""

_conn: sqlite3.Connection | None = None


def connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
    return _conn


def reset(path=None):
    """Point the module at a different database (used by tests)."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None
    if path is not None:
        config.DB_PATH = path


@contextmanager
def tx():
    with _lock:
        conn = connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def query(sql: str, params=()) -> list[dict]:
    with _lock:
        rows = connect().execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if "data" in d and d["data"]:
            d["data"] = json.loads(d["data"])
        if "body" in d and isinstance(d["body"], str):
            d["body"] = json.loads(d["body"])
        out.append(d)
    return out
