import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from met_api import create_app, db, stations, timeutil
from met_api.ingest import ingest

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 25, 5, 30, tzinfo=timezone.utc)


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def frozen_now(monkeypatch):
    monkeypatch.setattr(timeutil, "utcnow", lambda: NOW)
    return NOW


@pytest.fixture
def app(tmp_path, frozen_now):
    return create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "test.sqlite3"),
        "INGEST_DELAY_SECONDS": 0,
    })


@pytest.fixture
def registry():
    return {s["id"]: s for s in stations.load_csv()}


@pytest.fixture
def conn(app):
    c = db.connect(app.config["DATABASE"])
    db.init_db(c)
    db.sync_stations(c, stations.load_csv())
    yield c
    c.close()


@pytest.fixture
def client(app, conn, registry):
    """Athenry has five fresh hourly readings; Dublin Airport has one, ten hours old."""
    payloads = {
        "athenry": load_fixture("synthetic_athenry_today.json"),
        "dublin": load_fixture("synthetic_dublin.json"),
    }
    ingest(conn, [registry["athenry"], registry["dublin-airport"]], "today",
           lambda slug, feed: (200, payloads[slug]))
    return app.test_client()
