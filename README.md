# Met-Eireann-API

A clean, easy-to-use API for Met Éireann's open weather observations.

**Public instance: <https://api.lcvetkovic.com>. Interactive docs: <https://api.lcvetkovic.com/docs>**

Met Éireann publishes hourly readings from its weather stations, but the feed is awkward:

- numbers come as strings,
- there are no timestamps,
- units aren't stated,
- station IDs are confusing (`dublin` is the airport, `cork` is the airport),
- there's no station list,
- only today and yesterday are available.

This project collects the feed every hour into SQLite and serves it with:

- **real numbers**, with `null` for missing values,
- **UTC timestamps**, plus Irish local time,
- **units in every field name** (`temperature_c`, `wind.speed_kmh`, `pressure_msl_hpa`),
- **readable station IDs** (`dublin-airport`), search by town, and nearest-station lookup,
- **history as far back as collection goes**, with gaps reported rather than hidden, plus daily
  summaries,
- **checks on impossible values**: they come back as `null` with a flag. Met Éireann's data isn't
  quality-controlled.

Unofficial. The data is © Met Éireann, licensed under CC BY 4.0.

## Try it

```bash
curl "https://api.lcvetkovic.com/v1/observations/nearest?lat=53.35&lon=-6.26"   # weather near you
curl "https://api.lcvetkovic.com/v1/stations?q=carlow"                           # find a station
curl "https://api.lcvetkovic.com/v1/stations/cork-airport/daily"                 # last 7 days
```

## Stations

There are 25 main (synoptic) stations with hourly readings. The 78 automatic stations are
listed too, but they have no live feed yet. Stations without readings are hidden unless you add
`all=true`.

| You want | Ask for |
|---|---|
| A town's station | `/v1/stations?q=fermoy` finds Moore Park. `q` searches names, counties and nearby towns |
| Every station in a county | `/v1/stations?county=Kerry` |
| The station nearest you | `/v1/stations/nearest?lat=..&lon=..` |
| One station | `/v1/stations/valentia-observatory` |

**IDs are the station name in lower case with hyphens.** Met Éireann's IDs still work: they
redirect (HTTP 301) to ours. These are the ones that changed:

| Met Éireann ID | Our ID |
|---|---|
| `dublin` | `dublin-airport` |
| `cork` | `cork-airport` |
| `shannon` | `shannon-airport` |
| `knock` | `knock-airport` |
| `casement` | `casement-aerodrome` |
| `valentia` | `valentia-observatory` |
| `mt-dillon` | `mount-dillon` |
| `markree-castle` | `markree` |
| `newport-furnace` | `newport` |

The automatic stations lost their facility codes. For example, `TUAM WWTP` became Tuam
(`tuam`), and `JFK PARK` became New Ross (JFK Arboretum) (`new-ross`). Met Éireann's
original name is kept in `upstream.official_name`.

Each station looks like this:

```json
{
  "id": "oak-park",
  "name": "Oak Park",
  "county": "Carlow",
  "type": "synoptic",
  "has_observations": true,
  "location": {"lat": 52.861111, "lon": -6.915278, "elevation_m": null},
  "near": ["Carlow"],
  "upstream": {"metweb_slug": "oak-park", "station_number": null, "official_name": null}
}
```

## Endpoints

Every response is `{"data": ..., "meta": {...}}`. Errors are `application/problem+json`, with a
`detail` that says what to fix. An unknown query parameter is an error, so a typo such as
`lattitude=` doesn't get silently ignored. The full reference, where you can try each call, is
at [`/docs`](https://api.lcvetkovic.com/docs).

| Endpoint | Returns |
|---|---|
| `GET /v1/stations?q=&county=&type=&bbox=&all=` | Stations, sorted by name |
| `GET /v1/stations/nearest?lat=&lon=&limit=3` | Nearest stations, with `distance_km` |
| `GET /v1/stations/{id}` | One station, with its latest reading |
| `GET /v1/stations/{id}/observations/latest` | Latest reading at a station |
| `GET /v1/stations/{id}/observations?from=&to=&fill=` | Hourly history (see below) |
| `GET /v1/stations/{id}/daily?from=&to=` | One summary per day (see below) |
| `GET /v1/observations/latest?stations=a,b` | Latest reading at every station (or those listed) |
| `GET /v1/observations/nearest?lat=&lon=` | Latest reading from the nearest station that is still reporting |
| `GET /v1/health` | For each station: `ok`, `stale` or `no_data`, plus its last fetch |

### A reading

```json
{
  "station_id": "athenry",
  "observed_at": "2026-09-25T03:00:00Z",
  "local_time": "2026-09-25T04:00:00+01:00",
  "temperature_c": 9,
  "humidity_pct": 93,
  "pressure_msl_hpa": 1012,
  "rainfall_mm": 0,
  "wind": {"speed_kt": 9, "speed_kmh": 16.7, "gust_kt": null, "gust_kmh": null,
           "direction_deg": 225, "direction_cardinal": "SW"},
  "weather": {"symbol": "03n", "description": "Partly cloudy"},
  "flags": []
}
```

- `temperature_c` is in whole degrees, because that is what Met Éireann reports.
- `pressure_msl_hpa` is mean sea level pressure.
- `direction_deg` is only given when Met Éireann reports it. It isn't estimated from "SW".
- `flags` is empty when all is well:
  - `humidity_pct_out_of_range` (or similar) means the value was impossible and has been
    replaced with `null`.
  - `missing` marks an hour that was filled in because it had no reading.
- `?include=raw` adds Met Éireann's untouched record.

### History

`/v1/stations/{id}/observations` returns readings oldest first. It defaults to the last 24
hours. Use `from` and `to` in ISO 8601 (a time without an offset means UTC). Follow `meta.next`
for more pages.

`meta.coverage` reports gaps:

```json
"coverage": {"hours_expected": 23, "hours_reported": 21,
             "gaps": [{"from": "2026-09-24T06:00:00Z", "to": "2026-09-24T07:00:00Z", "hours": 2}]}
```

With `fill=true`, every hour gets an entry. A missing hour has `null` values and the flag
`missing`, so charts show a break instead of drawing a line across the gap.

### Daily summaries

`/v1/stations/{id}/daily` returns one entry per Irish calendar day, including days with no data.
It defaults to the last 7 days, and you can ask for up to 366.

```json
{
  "date": "2026-09-24", "hours_reported": 24, "hours_expected": 24, "complete": true,
  "temperature_min_c": 8, "temperature_max_c": 15, "temperature_mean_c": 11.3,
  "rainfall_total_mm": 3.2,
  "wind_speed_max_kt": 18, "wind_speed_max_kmh": 33.3, "wind_gust_max_kt": 31, "wind_gust_max_kmh": 57.4,
  "humidity_mean_pct": 85.2, "pressure_msl_min_hpa": 1008, "pressure_msl_max_hpa": 1015
}
```

- `hours_expected` is 23 or 25 on the days the clocks change.
- Days run midnight to midnight, Irish time. Met Éireann's official climate day runs
  09:00–09:00 UTC, so rainfall totals can differ from theirs.

## Running it yourself

You need Python 3.11 or newer.

```bash
git clone https://github.com/Lukallo/Met-Eireann-API.git && cd Met-Eireann-API
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q

flask --app met_api init-db                  # create instance/met.sqlite3 and load stations
flask --app met_api probe --station athenry  # check the live feed parses
flask --app met_api ingest                   # store the latest readings
flask --app met_api run                      # http://127.0.0.1:5000/docs
```

| Command | Purpose |
|---|---|
| `flask --app met_api init-db` | Create or upgrade the database, and sync `met_api/data/stations.csv` into it |
| `flask --app met_api ingest [--feed today\|yesterday] [--station ID ...]` | Fetch and store readings. Exits 1 if every station failed |
| `flask --app met_api probe [--save DIR] [--station ID ...]` | Check every feed parses and test the feed's time zone, rainfall and wind units, without touching the database |

A database made by an earlier version is upgraded on the next `init-db` or `ingest`. Renamed
stations keep their history.

### Configuration

Every setting can be overridden with an environment variable prefixed `MET_API_`:

| Setting | Default |
|---|---|
| `MET_API_DATABASE` | `instance/met.sqlite3` |
| `MET_API_METWEB_BASE_URL` | `https://prodapi.metweb.ie` |
| `MET_API_METWEB_TIMEOUT` | `15` (seconds) |
| `MET_API_METWEB_TIMEZONE` | `Europe/Dublin` (the zone the feed's times are in) |
| `MET_API_INGEST_DELAY_SECONDS` | `1.0` (pause between stations) |
| `MET_API_STALE_AFTER_HOURS` | `3` |
| `MET_API_NEAREST_MAX_DISTANCE_KM` | `80` (everywhere in Ireland is within 80 km of a main station) |

## Deploying (how api.lcvetkovic.com runs)

The `deploy/` folder has everything needed on a Debian or Ubuntu server:

- **`met-api.service`** runs gunicorn on `127.0.0.1:8000`.
- **`met-ingest-today.timer`** collects at ten past every hour.
- **`met-ingest-yesterday.timer`** re-fetches yesterday's feed at 00:20 Irish time, to fill any
  gaps.
- **`Caddyfile`** serves `api.lcvetkovic.com` over HTTPS.

```bash
sudo apt install -y git python3-venv sqlite3 caddy
sudo useradd --system --create-home --home-dir /var/lib/met-api met-api
sudo git clone https://github.com/Lukallo/Met-Eireann-API.git /opt/met-eireann-api
cd /opt/met-eireann-api
sudo python3 -m venv .venv && sudo .venv/bin/pip install -r requirements.txt
sudo cp deploy/met-api.env.example /etc/met-api.env
sudo cp deploy/*.service deploy/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo -u met-api env MET_API_DATABASE=/var/lib/met-api/met.sqlite3 .venv/bin/flask --app met_api init-db
sudo systemctl enable --now met-api.service met-ingest-today.timer met-ingest-yesterday.timer
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

For HTTPS, DNS needs an `A` record for `api` pointing at the server's public IP, and ports 80
and 443 must be open.

To update: `cd /opt/met-eireann-api && sudo git pull && sudo .venv/bin/pip install -r
requirements.txt && sudo systemctl restart met-api`.

**Back up the database.** Met Éireann's feed only keeps two days, so the SQLite file is the
only copy of the history. `sqlite3 /var/lib/met-api/met.sqlite3 ".backup backup.sqlite3"` is safe
to run while the service is up.

## Not yet verified against the live feed

This was built without network access to `prodapi.metweb.ie`. Run
`flask --app met_api probe --save tests/fixtures/real` and check its "Checks" section. It
confirms whether:

- times are Irish local time,
- rainfall is per hour rather than a running total,
- wind is in knots (compare one reading with met.ie).

Still to check against Met Éireann's station list:

- the coordinates of the five airport and aerodrome stations,
- the counties of Limerick (Clareville), Nealstown and Mount Russell.
