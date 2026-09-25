"""SQLite access: connections, schema and migrations, writes, and the queries the API uses."""

import sqlite3
from pathlib import Path

from flask import current_app, g

from .quality import valid_sql

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

OBSERVATION_COLUMNS = (
    "station_id", "observed_at", "local_date",
    "temperature_c", "humidity_pct", "pressure_msl_hpa", "rainfall_mm",
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

STATION_COLUMNS = ("id", "name", "type", "county", "lat", "lon", "elevation_m", "near",
                   "metweb_slug", "station_number", "official_name")

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
SELECT o.*, s.name AS station_name, s.county AS station_county,
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
    migrate(conn)


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn):
    """Bring a database made by an older version up to date. Safe to run repeatedly."""
    with conn:
        station_columns = _columns(conn, "stations")
        for column in ("county", "near", "official_name"):
            if column not in station_columns:
                conn.execute(f"ALTER TABLE stations ADD COLUMN {column} TEXT")
        if "pressure_hpa" in _columns(conn, "observations"):
            conn.execute("ALTER TABLE observations RENAME COLUMN pressure_hpa TO pressure_msl_hpa")


def sync_stations(conn, stations):
    """Make the stations table match the registry.

    A station whose ID changed (matched on its Met Éireann slug or number)
    keeps its history under the new ID. Stations no longer listed become
    inactive. Returns ``[(old_id, new_id), ...]`` for any renames.
    """
    renames = []
    with conn:
        for s in stations:
            for (old_id,) in conn.execute(
                "SELECT id FROM stations WHERE id != ? AND (metweb_slug = ? OR station_number = ?)",
                (s["id"], s["metweb_slug"], s["station_number"]),
            ).fetchall():
                # Free the unique keys so the new row can take them.
                conn.execute(
                    "UPDATE stations SET metweb_slug = NULL, station_number = NULL, active = 0"
                    " WHERE id = ?", (old_id,),
                )
                renames.append((old_id, s["id"]))

        conn.executemany(UPSERT_STATION, stations)

        for old_id, new_id in renames:
            conn.execute("UPDATE OR IGNORE observations SET station_id = ? WHERE station_id = ?",
                         (new_id, old_id))
            conn.execute("DELETE FROM observations WHERE station_id = ?", (old_id,))
            conn.execute("UPDATE ingest_runs SET station_id = ? WHERE station_id = ?",
                         (new_id, old_id))
            conn.execute("DELETE FROM stations WHERE id = ?", (old_id,))

        ids = [s["id"] for s in stations]
        conn.execute(
            f"UPDATE stations SET active = 0 WHERE id NOT IN ({', '.join('?' * len(ids))})", ids
        )
    return renames


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


def station_by_slug(conn, slug):
    """The station Met Éireann calls ``slug``, for redirecting their IDs to ours."""
    return conn.execute(
        "SELECT * FROM stations WHERE metweb_slug = ? AND active = 1", (slug,)
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


def observations_between(conn, station_id, start, end, after=None, limit=None):
    """Rows with ``start <= observed_at <= end`` (and ``> after``), oldest first."""
    sql = ("SELECT * FROM observations WHERE station_id = ? "
           "AND observed_at >= ? AND observed_at <= ?")
    params = [station_id, start, end]
    if after is not None:
        sql += " AND observed_at > ?"
        params.append(after)
    sql += " ORDER BY observed_at"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def observation_times(conn, station_id, start, end):
    """Just the timestamps stored in a window, for working out gaps."""
    return [
        row[0] for row in conn.execute(
            "SELECT observed_at FROM observations WHERE station_id = ?"
            " AND observed_at >= ? AND observed_at <= ? ORDER BY observed_at",
            (station_id, start, end),
        )
    ]


def daily_summaries(conn, station_id, first_date, last_date):
    """One row per Irish calendar day that has data. Impossible values are left out."""
    v = valid_sql
    return conn.execute(
        f"""
        SELECT local_date,
               COUNT(*)                         AS hours_reported,
               MIN({v('temperature_c')})        AS temperature_min_c,
               MAX({v('temperature_c')})        AS temperature_max_c,
               AVG({v('temperature_c')})        AS temperature_mean_c,
               SUM({v('rainfall_mm')})          AS rainfall_total_mm,
               MAX({v('wind_speed_kt')})        AS wind_speed_max_kt,
               MAX({v('wind_gust_kt')})         AS wind_gust_max_kt,
               AVG({v('humidity_pct')})         AS humidity_mean_pct,
               MIN({v('pressure_msl_hpa')})     AS pressure_msl_min_hpa,
               MAX({v('pressure_msl_hpa')})     AS pressure_msl_max_hpa
        FROM observations
        WHERE station_id = ? AND local_date BETWEEN ? AND ?
        GROUP BY local_date
        ORDER BY local_date
        """,
        (station_id, first_date, last_date),
    ).fetchall()


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
        ORDER BY s.name
        """
    ).fetchall()
