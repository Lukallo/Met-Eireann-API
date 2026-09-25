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

The goal is one service, one station model, one observation schema, one time format and explicit
units. Clients never need to know which Met Éireann host the data came from.

### Principles

1. **Real types.** Numbers are numbers. A missing value is `null`, never `"-"`.
2. **Units in field names**, for example `temperature_c`, `wind_speed_kt`, `wind_speed_kmh` and
   `pressure_hpa`. Then no field is ambiguous.
3. **ISO 8601 timestamps.** `observed_at` is in UTC (`Z`). `local_time` is in Europe/Dublin and
   carries its offset. Both are computed once, on the server, with DST handled.
4. **Stations are first-class.** They have stable IDs and coordinates, and can be listed and
   searched by location.
5. **One consistent envelope**: `{ "data": ..., "meta": ... }`. Errors use RFC 9457
   `application/problem+json`.
6. **Keep the raw value.** `?include=raw` returns the untouched upstream record, for debugging.
7. **Versioned** under `/v1`, with auto-generated OpenAPI docs.

### Endpoints

| Method and path | Purpose |
|---|---|
| `GET /v1/stations` | List stations. Filters: `type=synoptic\|automatic`, `bbox=minLon,minLat,maxLon,maxLat`, `county` |
| `GET /v1/stations/{id}` | Details for one station: coordinates, elevation, type, upstream IDs, which data it offers |
| `GET /v1/stations/nearest?lat=&lon=&type=&limit=3` | Nearest stations with `distance_km`. This replaces the loops in `met.py` |
| `GET /v1/stations/{id}/observations?from=&to=` | Hourly observations over an ISO 8601 range. Defaults to the last 24 h |
| `GET /v1/stations/{id}/observations/latest` | The most recent observation for one station |
| `GET /v1/observations/latest?stations=a,b,c` or `?bbox=` | A national or regional snapshot in one call. Upstream needs 25 separate calls for this |
| `GET /v1/observations/nearest?lat=&lon=` | Latest reading from the nearest station that is currently reporting |
| `GET /v1/forecast?lat=&lon=` (later phase) | The XML point forecast, normalised into the same units and shape |
| `GET /v1/health` | Upstream reachability and data freshness for each source |

### Schemas

Station:

```json
{
  "id": "shannon",
  "name": "Shannon Airport",
  "type": "synoptic",
  "location": { "lat": 52.690, "lon": -8.918, "elevation_m": 15 },
  "county": "Clare",
  "upstream": { "metweb_slug": "shannon", "wmo_id": null, "climate_id": null },
  "provides": ["observations"]
}
```

Observation:

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
  "weather": { "symbol": "04d", "description": "Cloudy" },
  "quality": "provisional"
}
```

Response envelope:

```json
{
  "data": [ /* observations */ ],
  "meta": {
    "source": "metweb/observations",
    "fetched_at": "2026-09-25T12:07:31Z",
    "attribution": "Met Éireann, CC BY 4.0"
  }
}
```

### Architecture

```
            ┌──────────── poller (hourly, ~:10 past) ────────────┐
upstream ──▶│ fetch → normalise (parse, types, units, UTC) → store│──▶ SQLite / Postgres
            └────────────────────────────────────────────────────┘          │
clients ──▶ FastAPI /v1 ── reads store; read-through fetch + short cache ◀──┘
```

The central idea is to **ingest every station on a schedule and serve from our own store**. That
gives us:

- **History.** Upstream only has today and yesterday. Polling every hour builds up history that
  can then be queried by date range.
- **Multi-station queries and one consistent format**, because every record passes through the
  same normaliser.
- **Isolation.** Clients never hit Met Éireann directly, so upstream downtime or rate limits
  don't reach them.

Suggested stack:

- Python, since the code is already Python.
- FastAPI and Pydantic, which give typed models and OpenAPI docs for free.
- `httpx` for upstream calls.
- SQLite to begin with (TimescaleDB later if the history grows).
- Cron or APScheduler for the poller.
- About 10 lines of in-house haversine instead of a dependency.

### Proposed layout

```
met_api/
  data/stations.csv          # single source of truth: id, name, type, lat, lon, elev, county, upstream ids
  stations.py                # load registry, nearest(), bbox filter
  geo.py                     # haversine
  models.py                  # Pydantic Station / Observation / Envelope
  upstream/metweb.py         # fetch + normalise /observations/{slug}/{today|yesterday}
  upstream/forecast.py       # (phase 5) XML locationforecast → Observation-like
  store.py                   # persistence
  poller.py                  # hourly ingest
  app.py                     # FastAPI routes
tests/
  fixtures/*.json            # real captured upstream responses
  test_stations.py test_geo.py test_normalise.py test_api.py
```

## 4. Delivery phases

0. **Fix `met.py`.** Add the `haversine` import and correct the five duplicated coordinate pairs.
1. **Station registry.** Move both station tables into `stations.csv` with verified coordinates.
   Add the following tests, which would have caught bug 2:
   - IDs are unique.
   - No two stations share coordinates.
   - Every point lies inside Ireland.

   Then add `nearest()`. None of this phase needs network access.
2. **Upstream client and normaliser.**
   1. Capture real `today` and `yesterday` responses for every slug as test fixtures.
   2. Write a parser that handles:
      - numbers sent as strings,
      - missing-value markers,
      - midnight rollover and DST,
      - an empty array just after midnight.
   3. Probe all 25 slugs and record which ones are valid.
3. **API v1, read-through.** Build `/stations`, `/stations/nearest` and `/observations/latest`,
   fetching live from upstream behind a cache of about 10 minutes.
4. **Poller and storage.** Ingest every hour, keeping the latest reading if a value is revised.
   Add range queries and multi-station snapshots.
5. **Forecasts.** Normalise the point-forecast XML into the same units and time format.
6. **Ship.**
   - Add a Dockerfile, a README with examples, and a CI job that runs the tests.
   - Add rate limiting.
   - Show Met Éireann's CC BY 4.0 attribution in `meta`.

## 5. Open questions

- **Which feed do the `auto_stations` (IDs ending in `85`) come from?** The `/observations` endpoint
  uses slugs, so these need a different source (perhaps the climate CSVs or another metweb path).
  If no feed exists, they should be dropped or marked `provides: []`.
- **Is historical data in scope?** If so, the poller in phase 4 matters. If not, a stateless
  proxy (phase 3) may be enough.
- **Where will it be hosted, and who will use it?** The answer drives caching and rate limits.
