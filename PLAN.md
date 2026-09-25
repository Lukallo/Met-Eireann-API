# Plan: a coherent API over Met Éireann's open data

## 1. What `met.py` does today

1. Holds two hard-coded station tables:
   - `stations`: 25 synoptic stations, keyed by the URL slug the upstream API uses.
   - `auto_stations`: 78 automatic stations, keyed by a numeric ID ending in `85`.
2. Finds the station nearest a fixed point in each table, using haversine distance.
3. Calls `https://prodapi.metweb.ie/observations/{slug}/today` for the nearest synoptic station.
4. Parses the JSON into `y` and then does nothing with it.

## 2. Test results

The upstream host (`prodapi.metweb.ie`) is blocked by this session's network policy, so the live
response wasn't fetched. Everything below comes from running the code and data offline, or from
Met Éireann's published dataset descriptions on data.gov.ie.

### Bugs in `met.py`

| # | Problem | Effect |
|---|---------|--------|
| 1 | `haversine` is called but never imported (`from haversine import haversine`, and the package isn't installed) | The script crashes on line 122 with `NameError` before any request is made |
| 2 | Six stations share Athenry's coordinates `(53.289167, -8.785556)`: `athenry`, `casement`, `cork`, `dublin`, `knock`, `shannon` | Five real stations can never be chosen, because ties go to `athenry`, which comes first (see below) |
| 3 | The nearest automatic station is computed and printed, but never queried | Dead work. The upstream observations endpoint takes slugs, not these numeric IDs |
| 4 | `phoenix-park`, `belmullet` and `markree-castle` have lower-precision coordinates than the other stations | Minor. Re-check them against the official list |
| 5 | The 50000 threshold is a magic "infinity" value, and its unit (km) isn't stated | Readability only |

How bug 2 changes real lookups (the fixed column uses approximate published positions, still to be verified):

| Location | Nearest station now | Nearest with coordinates fixed |
|---|---|---|
| Dublin Airport terminal | phoenix-park (10 km) | dublin (0 km) |
| Cork Airport | roches-point (18 km) | cork (1 km) |
| Shannon town | **athenry (65 km)** | shannon (4 km) |

Approximate positions to verify: Dublin Airport 53.428, -6.241 · Cork Airport 51.847, -8.486 ·
Shannon 52.690, -8.918 · Casement 53.306, -6.439 · Knock 53.906, -8.817.

With a local haversine and the fixed test point `(54.0769, -7.6117)`, the logic picks **ballyhaise**
(19.9 km) and automatic station **5385 BALLINAMORE** (12.7 km). Those results are correct.

### Why the upstream API is awkward

Confirmed from the published dataset descriptions and sample payloads:

- **Numbers come back as strings**, for example `"temperature": "10"`. Every client has to parse them.
- **Temperature is rounded to whole degrees.** Wind speed and gust are in **knots**. Direction is a
  cardinal string such as `"NE"`.
- **There is no timestamp.** Time is split across `dayName`, `date` and `reportTime` (`"01:00"`),
  with no timezone.
- **One station per request, today only**, plus a separate "yesterday" feed. There are no date
  ranges and no history.
- **There is no station list and no coordinates.** You have to know the slug already, which is why
  `met.py` keeps its own coordinate table (and got some of it wrong).
- **The data is not quality-controlled.**
- **Other feeds use different conventions**:
  - The forecast API is on another host, returns XML, and uses `;`-separated query parameters.
  - Climate data comes as zipped CSVs keyed by numeric station IDs.

Still to confirm with a live call:

- How missing values look (`"-"`, `""` or `"n/a"`).
- What happens at the clock changes. For example, 01:00 happens twice on the last Sunday of October.
- Whether all 25 slugs in `met.py` actually return data.
- What the `symbol` codes mean.

## 3. Target design

The goal is one Flask service, one SQLite database, one station model and one observation
schema. Every hourly reading is stored permanently, so history builds up from the day ingestion
starts.

### Principles

1. **Real types.** Numbers are numbers. A missing value is `null`, never `"-"`.
2. **Units in field names**, for example `temperature_c`, `wind_speed_kt`, `wind_speed_kmh` and
   `pressure_hpa`.
3. **ISO 8601 timestamps.** `observed_at` is in UTC (`Z`). `local_time` is in Europe/Dublin and
   carries its offset. Both are computed once, at ingest, with DST handled.
4. **Stations are first-class.** They have stable IDs and coordinates, and can be listed and
   searched by location.
5. **One consistent envelope**: `{ "data": ..., "meta": ... }`. Errors are JSON with the HTTP
   status, never an HTML error page.
6. **Keep the raw record.** Every row stores the untouched upstream JSON, so the data can be
   re-parsed if the normaliser turns out to be wrong.
7. **Versioned** under `/v1`.

### Why history must start early

The upstream feed only covers **today and yesterday**, so a missed day is gone for good. That is
why ingestion is phase 2 in section 4, before the API. Met Éireann's historical CSV downloads
might be usable to backfill older data later (to be checked).

### Endpoints

**Minimum viable product:**

| Method and path | Purpose |
|---|---|
| `GET /v1/stations` | List stations. Filters: `type=synoptic\|automatic`, `bbox=minLon,minLat,maxLon,maxLat`, `county` |
| `GET /v1/stations/<id>` | One station, with its most recent observation embedded |
| `GET /v1/stations/nearest?lat=&lon=&limit=3&type=` | **Nearest** stations, each with `distance_km` |
| `GET /v1/observations/latest?stations=a,b` | **Latest** reading for every station, or for the listed ones |
| `GET /v1/stations/<id>/observations/latest` | **Latest** reading for one station |
| `GET /v1/stations/<id>/observations?from=&to=&limit=` | **History**. Hourly rows in an ISO 8601 range, paginated with a `next` link. Defaults to the last 24 h |
| `GET /v1/observations/nearest?lat=&lon=&max_age_h=3` | Latest reading from the nearest station that has reported recently. Stations that have gone quiet are skipped |
| `GET /v1/health` | Last successful poll per station, how old its data is, and any gaps |

**Next ideas, all computed from the stored history:**

| Method and path | Purpose |
|---|---|
| `GET /v1/stations/<id>/daily?from=&to=` | Daily summaries: min and max temperature, total rainfall, maximum gust, mean pressure, and the number of hours reported (so partial days are visible) |
| `GET /v1/extremes?date=` (defaults to today) | Hottest, coldest, wettest and windiest stations in Ireland for that day |
| `GET /v1/stations/<id>/records` | Highs and lows for one station across everything we have stored, with when each happened |
| `?format=csv` on history and daily summaries | Spreadsheet export |
| `?format=geojson` on stations and latest | Can be dropped straight onto a Leaflet or Mapbox map |

**Later:**

| Method and path | Purpose |
|---|---|
| `GET /v1/forecast?lat=&lon=` | The XML point forecast, normalised into the observation format |
| `GET /v1/warnings` | Met Éireann's weather warnings, normalised |
| Threshold alerts | Webhook or email when, for example, the gust at a chosen station goes above 50 kt |

### Response shapes

Observation (as returned by the API; km/h values are worked out when the response is built):

```json
{
  "station_id": "athenry",
  "observed_at": "2026-09-25T12:00:00Z",
  "local_time": "2026-09-25T13:00:00+01:00",
  "temperature_c": 12,
  "humidity_pct": 93,
  "pressure_hpa": 1012,
  "rainfall_mm": 0.0,
  "wind": {
    "speed_kt": 9, "speed_kmh": 16.7,
    "gust_kt": null, "gust_kmh": null,
    "direction_cardinal": "SW", "direction_deg": 225
  },
  "weather": { "symbol": "04d", "description": "Cloudy" }
}
```

Envelope:

```json
{
  "data": [ /* ... */ ],
  "meta": {
    "count": 24,
    "next": "/v1/stations/athenry/observations?from=...&to=...",
    "generated_at": "2026-09-25T12:07:31Z",
    "attribution": "Met Éireann, CC BY 4.0"
  }
}
```

### SQLite schema

The schema is in [`met_api/schema.sql`](met_api/schema.sql). There are three tables:

- `stations`, loaded from `met_api/data/stations.csv`,
- `observations`, keyed on `(station_id, observed_at)`, holding the raw JSON too,
- `ingest_runs`, one row per fetch attempt.

Notes on the schema:

- **WAL mode** lets the API read while the ingest job writes.
- **Writes are upserts**, using `INSERT ... ON CONFLICT (station_id, observed_at) DO UPDATE`.
  Polling the same hour twice is harmless, and a revised value replaces the old one.
- **Values are stored in source units** (knots). Other units are worked out when a response is
  built, so no precision is lost.
- **`local_date` is stored** because SQLite has no timezone support. Grouping by Irish calendar
  day is then a plain `GROUP BY local_date`.
- **Size and speed are not a concern.** 25 stations × 24 hours × 365 days is about 220,000 rows
  a year. Benchmarked on a year of synthetic data:
  - latest for every station: 0.8 ms,
  - a week of history: 0.3 ms,
  - health: 0.2 ms.

  The "latest" query uses `CROSS JOIN` to force a per-station lookup. Without it, SQLite
  scanned the whole table and took about 350 ms.
- **Nearest-station search needs no spatial index.** With about 100 stations, computing haversine
  distance in Python is instant.

### Runtime layout

```
timer :10 past every hour  ──▶  flask --app met_api ingest                   (today feed, all stations)
timer 00:20 Irish time     ──▶  flask --app met_api ingest --feed yesterday  (fills any gaps)
                                        │ upsert
                                        ▼
                                  met.sqlite3 (WAL)
                                        ▲ read-only queries
clients ──▶ gunicorn wsgi:app ──▶ Flask blueprint /v1
```

Ingestion is a **Flask CLI command run by a systemd timer or cron**, not a scheduler inside the
web app. Gunicorn runs several workers, and a scheduler inside the app would run once in each of
them. Keeping ingestion separate also means a web restart never interrupts it. The units are in
`deploy/`.

### Code layout

```
met_api/
  __init__.py        # create_app(): config (MET_API_* env vars), blueprint, error handlers, CLI
  schema.sql
  db.py              # connections, schema, upserts, the queries the API uses
  stations.py        # load data/stations.csv, by_distance(), nearest(), in_bbox()
  geo.py             # haversine
  timeutil.py        # UTC formatting, query-string time parsing
  upstream.py        # fetch /observations/{slug}/{today|yesterday} and normalise
  ingest.py          # loop over stations → fetch → upsert, log to ingest_runs
  cli.py             # flask init-db / ingest / probe
  api.py             # /v1 blueprint: routes, envelope, problem+json errors
  data/stations.csv
tests/               # pytest: stations, upstream parsing, ingest + CLI, API
deploy/              # systemd service + timers, env file example
wsgi.py
```

## 4. Delivery phases

0. ✅ **Fix `met.py`.** Added the `haversine` import and corrected the five duplicated coordinate
   pairs.
1. ✅ **Station registry and schema.** Built `stations.csv` (25 synoptic and 78 automatic
   stations), the `init-db` command and `nearest()`.
2. ◐ **Upstream client and ingestion.** Done: the normaliser, the `ingest` and `probe` commands,
   and the timers. Still to do: run `flask probe --save tests/fixtures/real` against the live
   feed and act on its "Checks" section.
3. ✅ **Flask API, minimum viable product.** Stations, nearest, latest, history and health.
4. ✅ **Cleanup, v1.1.** Organisation, usability and accuracy:
   - **Stations:**
     - readable IDs (`dublin-airport`), with Met Éireann's IDs redirecting (301) to ours,
     - automatic station names without facility codes,
     - counties for all but three stations,
     - nearby towns for search (`?q=carlow` finds Oak Park),
     - stations without live readings hidden unless `all=true`.
   - **Requests:** unknown query parameters are rejected, and a mistyped station ID gets a
     "did you mean" suggestion.
   - **Readings:**
     - impossible values become `null` with a flag,
     - `pressure_msl_hpa`,
     - wind degrees only when Met Éireann reports them (never estimated from the compass point),
     - numbers as precise as reported.
   - **History and summaries:** history reports gaps (`meta.coverage`) and can fill them
     (`fill=true`). Daily summaries count 23 or 25 hours on clock-change days.
   - **Nearest reading:** limited to 80 km by default (everywhere in Ireland is within 80 km of
     a main station).
   - **Docs:** interactive at `/docs` (OpenAPI 3.1), tested against the routes so they can't
     drift.
   - **Upgrades:** version 1 databases upgrade in place, keeping history under the new IDs.
5. **Next.**
   - Beaufort force.
   - CSV and GeoJSON output.
   - Extremes ("hottest, wettest, windiest station today") and all-time station records.
   - Forecasts, warnings, alerts.
   - Backfill from Met Éireann's historical CSVs.
6. ◐ **Ship.** Done: systemd units, a Caddyfile for `api.lcvetkovic.com`, README and CI. Still to
   do: deploy, set up DNS, and schedule a nightly backup of the SQLite file.

## 5. Open questions

- **Which feed do the automatic stations come from?** The `/observations` endpoint uses slugs, so
  these need a different source. For now they can be found (`all=true`) but are never polled.
- **Are the corrected coordinates right?** Check those for Casement Aerodrome, Cork Airport,
  Dublin Airport, Knock Airport and Shannon Airport against Met Éireann's station list. Station
  heights (`elevation_m`) also need filling in.
- **Which county are these in?** Limerick (Clareville), Nealstown and Mount Russell. They are
  left blank rather than guessed.
- **Is the API public?** It will be at `api.lcvetkovic.com`, so it may need rate limiting
  (Caddy can do this) and caching headers.
