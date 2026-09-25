"""SQLite access: connections, schema, writes, and the queries the API uses."""

import sqlite3
from pathlib import Path

from flask import current_app, g

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

OBSERVATION_COLUMNS = (
    "station_id", "observed_at", "local_date",
    "temperature_c", "humidity_pct", "pressure_hpa", "rainfall_mm",
    "wind_speed_kt", "wind_gust_kt", "wind_dir_deg", "wind_dir_cardinal",
    "weather_symbol", "weather_description",
    "raw_json", "fetched_at",
)
_KEY = ("station_id", "observed_at")

UPSERT_OBSERVATION = (
    f"INSERT INTO observations ({', '.join(OBSERVATION_COLUMNS)}) "
    f"VALUES ({', '.join(':' + c for c in OBSERVATION_COLUMNS)}) "
    "ON CONFLICT (station_id, observed_at) DO UPDATE SET "
    + ", ".join(f"{c} = excluded.{c}" for c in OBSERVATION_COLUMNS if c not in _KEY)
)

STATION_COLUMNS = ("id", "name", "type", "lat", "lon", "elevation_m", "county",
                   "metweb_slug", "station_number")

UPSERT_STATION = (
    f"INSERT INTO stations ({', '.join(STATION_COLUMNS)}, active) "
    f"VALUES ({', '.join(':' + c for c in STATION_COLUMNS)}, 1) "
    "ON CONFLICT (id) DO UPDATE SET "
    + ", ".join(f"{c} = excluded.{c}" for c in STATION_COLUMNS if c != "id")
    + ", active = 1"
)

# Latest observation per active station. CROSS JOIN stops SQLite reordering
# the join: it walks the ~100 stations and does two primary-key lookups each,
# instead of scanning every stored observation (0.5 ms vs 350 ms on a year of data).
_LATEST = """
SELECT o.*, s.name AS station_name, s.type AS station_type,
       s.lat AS station_lat, s.lon AS station_lon
FROM stations s
CROSS JOIN observations o
    ON o.station_id = s.id
   AND o.observed_at = (SELECT MAX(observed_at) FROM observations WHERE station_id = s.id)
WHERE s.active = 1 {station_filter}
ORDER BY o.station_id
"""


def connect(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    """The request's connection, opened on first use."""
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(conn):
    # WAL lets the API read while the ingest job writes. The setting persists in the file.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)


def sync_stations(conn, stations):
    """Make the stations table match the registry. Stations no longer listed become inactive."""
    ids = [s["id"] for s in stations]
    with conn:
        conn.executemany(UPSERT_STATION, stations)
        conn.execute(
            f"UPDATE stations SET active = 0 WHERE id NOT IN ({', '.join('?' * len(ids))})", ids
        )


def upsert_observations(conn, rows):
    with conn:
        conn.executemany(UPSERT_OBSERVATION, rows)
    return len(rows)


def log_ingest_run(conn, station_id, feed, started_at, http_status, rows_upserted, error):
    with conn:
        conn.execute(
            "INSERT INTO ingest_runs (station_id, feed, started_at, http_status, rows_upserted, error)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (station_id, feed, started_at, http_status, rows_upserted, error),
        )


# --- Queries used by the API -------------------------------------------------

def list_stations(conn):
    return conn.execute("SELECT * FROM stations WHERE active = 1 ORDER BY name").fetchall()


def get_station(conn, station_id):
    return conn.execute(
        "SELECT * FROM stations WHERE id = ? AND active = 1", (station_id,)
    ).fetchone()


def latest_observations(conn, station_ids=None):
    """Latest observation for every active station (or only those listed)."""
    if station_ids is None:
        return conn.execute(_LATEST.format(station_filter="")).fetchall()
    placeholders = ", ".join("?" * len(station_ids))
    sql = _LATEST.format(station_filter=f"AND s.id IN ({placeholders})")
    return conn.execute(sql, list(station_ids)).fetchall()


def latest_observation(conn, station_id):
    return conn.execute(
        "SELECT * FROM observations WHERE station_id = ? ORDER BY observed_at DESC LIMIT 1",
        (station_id,),
    ).fetchone()


def observations_between(conn, station_id, start, end, after=None, limit=500):
    """Rows with ``start <= observed_at <= end`` (and ``> after``), oldest first."""
    sql = ("SELECT * FROM observations WHERE station_id = ? "
           "AND observed_at >= ? AND observed_at <= ?")
    params = [station_id, start, end]
    if after is not None:
        sql += " AND observed_at > ?"
        params.append(after)
    sql += " ORDER BY observed_at LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def ingest_status(conn):
    """For each polled station: newest stored observation and the most recent ingest attempt."""
    return conn.execute(
        """
        SELECT s.id, s.name,
               (SELECT MAX(observed_at) FROM observations WHERE station_id = s.id)
                   AS last_observed_at,
               r.feed, r.started_at, r.http_status, r.rows_upserted, r.error
        FROM stations s
        LEFT JOIN ingest_runs r ON r.id = (
            SELECT id FROM ingest_runs WHERE station_id = s.id
            ORDER BY started_at DESC, id DESC LIMIT 1
        )
        WHERE s.active = 1 AND s.metweb_slug IS NOT NULL
        ORDER BY s.id
        """
    ).fetchall()
