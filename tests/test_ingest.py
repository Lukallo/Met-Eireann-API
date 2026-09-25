import json

import pytest

from met_api import upstream
from met_api.ingest import ingest

from .conftest import load_fixture

ATHENRY = load_fixture("synthetic_athenry_today.json")


def count(conn, table, where="1=1", params=()):
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]


def test_init_db_loads_registry(conn):
    assert count(conn, "stations") == 103
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_sync_marks_removed_stations_inactive(conn, registry):
    from met_api import db
    db.sync_stations(conn, [s for s in registry.values() if s["id"] != "valentia-observatory"])
    assert count(conn, "stations", "active = 1") == 102
    assert count(conn, "stations", "id = 'valentia-observatory' AND active = 0") == 1


def test_ingest_stores_rows_and_logs_run(conn, registry):
    results = ingest(conn, [registry["athenry"]], "today", lambda slug, feed: (200, ATHENRY))
    assert [(r.station_id, r.http_status, r.rows, r.error) for r in results] == [
        ("athenry", 200, 5, None)]
    assert count(conn, "observations", "station_id = 'athenry'") == 5
    run = conn.execute("SELECT * FROM ingest_runs").fetchone()
    assert (run["station_id"], run["feed"], run["rows_upserted"]) == ("athenry", "today", 5)
    assert run["started_at"] == "2026-09-25T05:30:00Z"


def test_reingesting_updates_instead_of_duplicating(conn, registry):
    fetch = lambda slug, feed: (200, ATHENRY)
    ingest(conn, [registry["athenry"]], "today", fetch)

    revised = json.loads(json.dumps(ATHENRY))
    revised[0]["temperature"] = "12"
    ingest(conn, [registry["athenry"]], "yesterday", lambda slug, feed: (200, revised))

    assert count(conn, "observations") == 5
    temp = conn.execute("SELECT temperature_c FROM observations ORDER BY observed_at").fetchone()[0]
    assert temp == 12.0
    assert count(conn, "ingest_runs") == 2


def test_one_station_failing_does_not_stop_the_rest(conn, registry):
    def fetch(slug, feed):
        if slug == "cork":
            raise upstream.UpstreamError("https://x/observations/cork/today: HTTP 404", status=404)
        if slug == "dublin":
            raise RuntimeError("parser bug")
        return 200, ATHENRY

    picked = [registry[i] for i in ("cork-airport", "dublin-airport", "athenry")]
    results = {r.station_id: r for r in ingest(conn, picked, "today", fetch)}
    assert results["cork-airport"].http_status == 404 and "HTTP 404" in results["cork-airport"].error
    assert results["dublin-airport"].error == "RuntimeError: parser bug"
    assert results["athenry"].rows == 5 and results["athenry"].error is None
    assert count(conn, "ingest_runs") == 3


def test_skipped_records_are_reported(conn, registry):
    payload = [*ATHENRY, {"date": "25-09-2026", "reportTime": "??"}]
    (result,) = ingest(conn, [registry["athenry"]], "today", lambda s, f: (200, payload))
    assert result.rows == 5
    assert result.error.startswith("skipped 1 record(s)")


# --- CLI ---------------------------------------------------------------------

@pytest.fixture
def fake_fetch(monkeypatch):
    calls = []

    def fetch(slug, feed="today", **kwargs):
        calls.append((slug, feed))
        if slug == "athenry":
            return 200, ATHENRY
        raise upstream.UpstreamError(f"{slug}: HTTP 404", status=404)

    monkeypatch.setattr(upstream, "fetch", fetch)
    return calls


def test_cli_init_db(app):
    result = app.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code == 0
    assert "103 stations" in result.output


def test_cli_ingest_single_station(app, fake_fetch):
    result = app.test_cli_runner().invoke(args=["ingest", "--station", "athenry"])
    assert result.exit_code == 0, result.output
    assert "athenry" in result.output and "rows=5" in result.output
    assert fake_fetch == [("athenry", "today")]


def test_cli_ingest_all_polls_every_synoptic_station(app, fake_fetch):
    result = app.test_cli_runner().invoke(args=["ingest", "--feed", "yesterday"])
    assert result.exit_code == 0, result.output
    assert len(fake_fetch) == 25 and all(feed == "yesterday" for _, feed in fake_fetch)
    assert "1/25 stations stored data" in result.output


def test_cli_ingest_exits_non_zero_when_everything_fails(app, fake_fetch):
    result = app.test_cli_runner().invoke(args=["ingest", "--station", "cork-airport"])
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_cli_accepts_met_eireann_ids(app, fake_fetch):
    result = app.test_cli_runner().invoke(args=["ingest", "--station", "cork"])
    assert "cork-airport" in result.output
    assert fake_fetch == [("cork", "today")]


def test_cli_rejects_station_without_feed(app, fake_fetch):
    result = app.test_cli_runner().invoke(args=["ingest", "--station", "glenveagh-national-park"])
    assert result.exit_code == 2
    assert "no live feed" in result.output


def test_cli_probe_saves_raw_responses(app, fake_fetch, tmp_path):
    out = tmp_path / "real"
    result = app.test_cli_runner().invoke(
        args=["probe", "--save", str(out), "--station", "athenry", "--station", "cork"])
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in out.iterdir()) == ["athenry-today.json", "athenry-yesterday.json"]
    assert "ok   athenry/today" in result.output
    assert "FAIL cork/today" in result.output
    assert "Normalised as:" in result.output
    assert "ok      rainfall: went down during the day" in result.output
    assert "times:" in result.output
    assert "wind units: athenry" in result.output
