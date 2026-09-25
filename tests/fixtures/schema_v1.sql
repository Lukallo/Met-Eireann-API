-- Idempotent: run on every `flask init-db` and `flask ingest`.

CREATE TABLE IF NOT EXISTS stations (
    id             TEXT PRIMARY KEY,   -- 'athenry', 'glenveagh-national-park'
    name           TEXT NOT NULL,
    type           TEXT NOT NULL CHECK (type IN ('synoptic', 'automatic')),
    lat            REAL NOT NULL,
    lon            REAL NOT NULL,
    elevation_m    REAL,
    county         TEXT,
    metweb_slug    TEXT UNIQUE,        -- key on prodapi.metweb.ie; NULL means no live feed
    station_number INTEGER UNIQUE,     -- numeric ID for automatic stations
    active         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS observations (
    station_id          TEXT NOT NULL REFERENCES stations(id),
    observed_at         TEXT NOT NULL,  -- UTC 'YYYY-MM-DDTHH:MM:SSZ'
    local_date          TEXT NOT NULL,  -- Europe/Dublin 'YYYY-MM-DD', for daily summaries
    temperature_c       REAL,
    humidity_pct        INTEGER,
    pressure_hpa        REAL,
    rainfall_mm         REAL,
    wind_speed_kt       INTEGER,
    wind_gust_kt        INTEGER,
    wind_dir_deg        INTEGER,
    wind_dir_cardinal   TEXT,
    weather_symbol      TEXT,
    weather_description TEXT,
    raw_json            TEXT NOT NULL,  -- the untouched upstream record
    fetched_at          TEXT NOT NULL,
    PRIMARY KEY (station_id, observed_at)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS obs_by_day  ON observations (station_id, local_date);
CREATE INDEX IF NOT EXISTS obs_by_time ON observations (observed_at);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id            INTEGER PRIMARY KEY,
    station_id    TEXT NOT NULL,
    feed          TEXT NOT NULL,        -- 'today' or 'yesterday'
    started_at    TEXT NOT NULL,
    http_status   INTEGER,
    rows_upserted INTEGER NOT NULL DEFAULT 0,
    error         TEXT
);

CREATE INDEX IF NOT EXISTS runs_by_station ON ingest_runs (station_id, started_at);
