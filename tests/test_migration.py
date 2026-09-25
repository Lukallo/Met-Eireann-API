"""A database made by version 1 (old IDs, pressure_hpa) upgrades without losing history."""

from pathlib import Path

from met_api import db
from met_api.stations import load_csv

SCHEMA_V1 = (Path(__file__).parent / "fixtures" / "schema_v1.sql").read_text()


def make_v1_database(path):
    conn = db.connect(path)
    conn.executescript(SCHEMA_V1)
    conn.executemany(
        "INSERT INTO stations (id, name, type, lat, lon, county, metweb_slug, station_number)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [("dublin", "Dublin Airport", "synoptic", 53.428, -6.241, "Dublin", "dublin", None),
         ("athenry", "Athenry", "synoptic", 53.289, -8.786, "Galway", "athenry", None),
         ("tuam-wwtp", "Tuam WWTP", "automatic", 53.515, -8.881, None, None, 4285)],
    )
    conn.executemany(
        "INSERT INTO observations (station_id, observed_at, local_date, temperature_c,"
        " pressure_hpa, raw_json, fetched_at) VALUES (?, ?, ?, ?, ?, '{}', 'x')",
        [("dublin", "2026-09-24T12:00:00Z", "2026-09-24", 14, 1015),
         ("dublin", "2026-09-24T13:00:00Z", "2026-09-24", 15, 1014),
         ("athenry", "2026-09-24T12:00:00Z", "2026-09-24", 12, 1013)],
    )
    conn.execute("INSERT INTO ingest_runs (station_id, feed, started_at, rows_upserted)"
                 " VALUES ('dublin', 'today', '2026-09-24T13:10:00Z', 2)")
    conn.commit()
    return conn


def test_upgrade_keeps_history_under_new_ids(tmp_path):
    conn = make_v1_database(tmp_path / "v1.sqlite3")

    db.init_db(conn)
    renames = db.sync_stations(conn, load_csv())

    assert ("dublin", "dublin-airport") in renames
    assert ("tuam-wwtp", "tuam") in renames
    rows = conn.execute(
        "SELECT station_id, temperature_c, pressure_msl_hpa FROM observations"
        " ORDER BY station_id, observed_at").fetchall()
    assert [tuple(r) for r in rows] == [
        ("athenry", 12, 1013), ("dublin-airport", 14, 1015), ("dublin-airport", 15, 1014)]
    assert conn.execute("SELECT station_id FROM ingest_runs").fetchone()[0] == "dublin-airport"

    ids = {r[0] for r in conn.execute("SELECT id FROM stations")}
    assert "dublin" not in ids and "tuam-wwtp" not in ids
    tuam = conn.execute("SELECT * FROM stations WHERE id = 'tuam'").fetchone()
    assert (tuam["station_number"], tuam["official_name"]) == (4285, "TUAM WWTP")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_upgrade_is_repeatable(tmp_path):
    conn = make_v1_database(tmp_path / "v1.sqlite3")
    db.init_db(conn)
    db.sync_stations(conn, load_csv())

    db.init_db(conn)
    assert db.sync_stations(conn, load_csv()) == []
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM stations WHERE active = 1").fetchone()[0] == 103
