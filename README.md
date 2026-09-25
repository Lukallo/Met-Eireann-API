# Met-Eireann-API

A bit of code used to fix the issues in the Met Éireann open data APIs.

It is a small Flask service. Every hour it collects Met Éireann's station observations
into SQLite and serves them as a consistent JSON API:

- real numbers, with `null` for missing values,
- UTC timestamps plus Irish local time,
- units in the field names,
- stations searchable by location,
- history beyond the "today" and "yesterday" the upstream feed offers.

See [PLAN.md](PLAN.md) for the design and what's next.

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt

flask --app met_api init-db                  # create instance/met.sqlite3 and load stations
flask --app met_api probe --station athenry  # check the live feed parses
flask --app met_api ingest                   # store the latest readings for every station
flask --app met_api run                      # http://127.0.0.1:5000/v1/
```

Run the tests with `pytest`.

## Endpoints

Every response looks like `{"data": ..., "meta": {...}}`. Errors are
`application/problem+json`, e.g. `{"status": 404, "title": "Not Found", "detail": "..."}`.

| Endpoint | What it returns |
|---|---|
| `GET /v1/stations?type=&county=&bbox=` | Stations. `type` is `synoptic` or `automatic`. `bbox` is `min_lon,min_lat,max_lon,max_lat` |
| `GET /v1/stations/nearest?lat=&lon=&limit=3&type=` | Nearest stations, each with `distance_km` |
| `GET /v1/stations/{id}` | One station, with its latest observation |
| `GET /v1/stations/{id}/observations/latest` | Latest observation at a station |
| `GET /v1/stations/{id}/observations?from=&to=&limit=` | Hourly history, oldest first. Defaults to the last 24 h. Follow `meta.next` for more pages |
| `GET /v1/observations/latest?stations=a,b` | Latest observation at every station (or those listed) |
| `GET /v1/observations/nearest?lat=&lon=&max_age_h=3` | Latest reading from the nearest station that has reported within `max_age_h` |
| `GET /v1/health` | Per station: last stored reading, last fetch attempt, and `ok` / `stale` / `no_data` |

Some details:

- Times in `from`, `to` and similar parameters are ISO 8601 dates or datetimes. A value
  without an offset is read as UTC.
- `?include=raw` adds the untouched upstream record to each observation.
- `county` matching ignores case.

Example observation:

```json
{
  "station_id": "athenry",
  "observed_at": "2026-09-25T03:00:00Z",
  "local_time": "2026-09-25T04:00:00+01:00",
  "temperature_c": 9.0,
  "humidity_pct": 93,
  "pressure_hpa": 1012.0,
  "rainfall_mm": 0.0,
  "wind": {"speed_kt": 9, "speed_kmh": 16.7, "gust_kt": null, "gust_kmh": null,
           "direction_deg": 22.5, "direction_cardinal": "NNE"},
  "weather": {"symbol": "03n", "description": "Partly cloudy"}
}
```

## Commands

| Command | Purpose |
|---|---|
| `flask --app met_api init-db` | Create any missing tables and sync `met_api/data/stations.csv` into the database |
| `flask --app met_api ingest [--feed today\|yesterday] [--station ID ...]` | Fetch and store observations. Exits 1 if every station failed |
| `flask --app met_api probe [--save DIR] [--station ID ...]` | Check each station's feed parses, without touching the database. `--save` keeps the raw responses |

## Configuration

Every setting can be overridden with an environment variable prefixed `MET_API_`:

| Setting | Default |
|---|---|
| `MET_API_DATABASE` | `instance/met.sqlite3` |
| `MET_API_METWEB_BASE_URL` | `https://prodapi.metweb.ie` |
| `MET_API_METWEB_TIMEOUT` | `15` (seconds) |
| `MET_API_METWEB_TIMEZONE` | `Europe/Dublin` (the zone the feed's times are in) |
| `MET_API_INGEST_DELAY_SECONDS` | `1.0` (pause between stations) |
| `MET_API_STALE_AFTER_HOURS` | `3` |

## Deploying on a Linux server

The `deploy/` folder has systemd units:

- **`met-api.service`** runs gunicorn on `127.0.0.1:8000`. Put nginx or Caddy in front of it.
- **`met-ingest-today.timer`** collects at ten past every hour.
- **`met-ingest-yesterday.timer`** re-fetches yesterday's feed at 00:20 Irish time, to fill any
  gaps.

Setup:

```bash
sudo useradd --system --home /var/lib/met-api --create-home met-api
sudo git clone https://github.com/lukallo/met-eireann-api /opt/met-eireann-api
cd /opt/met-eireann-api
sudo python3 -m venv .venv && sudo .venv/bin/pip install -r requirements.txt
sudo cp deploy/met-api.env.example /etc/met-api.env
sudo cp deploy/*.service deploy/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo -u met-api env MET_API_DATABASE=/var/lib/met-api/met.sqlite3 .venv/bin/flask --app met_api init-db
sudo systemctl enable --now met-api.service met-ingest-today.timer met-ingest-yesterday.timer
```

To update: `git pull`, then `sudo systemctl restart met-api`. The next ingest picks up any
station changes.

To use cron instead of systemd timers:

```cron
10 * * * *  cd /opt/met-eireann-api && MET_API_DATABASE=/var/lib/met-api/met.sqlite3 .venv/bin/flask --app met_api ingest
20 0 * * *  cd /opt/met-eireann-api && MET_API_DATABASE=/var/lib/met-api/met.sqlite3 .venv/bin/flask --app met_api ingest --feed yesterday
```

With cron, the second line runs at 00:20 in the server's time zone.

**Back up the database.** The upstream feed only keeps two days, so the SQLite file is
the only copy of your history. `sqlite3 met.sqlite3 ".backup backup.sqlite3"` is safe to
run while the service is up.

## Not yet verified against the live feed

This was built without network access to `prodapi.metweb.ie`. On the first run from a
machine that can reach it, run
`flask --app met_api probe --save tests/fixtures/real` and check:

- that every station ID in `stations.csv` returns data,
- that no `missing_fields` are reported,
- that the printed "Normalised as" times match the feed's `reportTime`.

Also check the corrected coordinates for `casement`, `cork`, `dublin`, `knock` and
`shannon` against Met Éireann's station list. They are approximate.

Data: Met Éireann, licensed under CC BY 4.0.
