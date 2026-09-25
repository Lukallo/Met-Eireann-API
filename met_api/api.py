"""The /v1 JSON API.

Every response is ``{"data": ..., "meta": ...}``. Errors are
``application/problem+json`` (RFC 9457). Times are UTC ``...Z``, and each
observation also carries its Irish local time.
"""

import json
import math
from datetime import timedelta

from flask import Blueprint, abort, current_app, jsonify, request, url_for
from werkzeug.exceptions import HTTPException

from . import db, timeutil
from .db import get_db
from .geo import haversine
from .stations import TYPES, by_distance, in_bbox
from .timeutil import IRISH_TZ, from_iso, parse_query_time, to_iso

bp = Blueprint("v1", __name__, url_prefix="/v1")

ATTRIBUTION = "Met Éireann (www.met.ie), CC BY 4.0"
KT_TO_KMH = 1.852
HISTORY_DEFAULT_HOURS = 24
HISTORY_DEFAULT_LIMIT = 500
HISTORY_MAX_LIMIT = 5000


# --- Response helpers --------------------------------------------------------

def envelope(data, **meta):
    if isinstance(data, list):
        meta = {"count": len(data), **meta}
    meta["generated_at"] = to_iso(timeutil.utcnow())
    meta["attribution"] = ATTRIBUTION
    return jsonify(data=data, meta=meta)


def _kmh(knots):
    return None if knots is None else round(knots * KT_TO_KMH, 1)


def station_json(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "location": {"lat": row["lat"], "lon": row["lon"], "elevation_m": row["elevation_m"]},
        "county": row["county"],
        "feeds": ["observations"] if row["metweb_slug"] else [],
        "upstream": {"metweb_slug": row["metweb_slug"], "station_number": row["station_number"]},
    }


def station_ref(row):
    """Short station block embedded in observations that span several stations."""
    return {
        "id": row["station_id"],
        "name": row["station_name"],
        "type": row["station_type"],
        "location": {"lat": row["station_lat"], "lon": row["station_lon"]},
    }


def observation_json(row, include_raw=False):
    observed = from_iso(row["observed_at"])
    obs = {
        "station_id": row["station_id"],
        "observed_at": row["observed_at"],
        "local_time": observed.astimezone(IRISH_TZ).isoformat(),
        "temperature_c": row["temperature_c"],
        "humidity_pct": row["humidity_pct"],
        "pressure_hpa": row["pressure_hpa"],
        "rainfall_mm": row["rainfall_mm"],
        "wind": {
            "speed_kt": row["wind_speed_kt"],
            "speed_kmh": _kmh(row["wind_speed_kt"]),
            "gust_kt": row["wind_gust_kt"],
            "gust_kmh": _kmh(row["wind_gust_kt"]),
            "direction_deg": row["wind_dir_deg"],
            "direction_cardinal": row["wind_dir_cardinal"],
        },
        "weather": {"symbol": row["weather_symbol"], "description": row["weather_description"]},
    }
    if include_raw:
        obs["raw"] = json.loads(row["raw_json"])
    return obs


# --- Query-string parsing ----------------------------------------------------

def _float_arg(name, lo, hi, default=None):
    raw = request.args.get(name)
    if raw is None:
        if default is None:
            abort(400, f"'{name}' is required.")
        return default
    try:
        value = float(raw)
    except ValueError:
        abort(400, f"'{name}' must be a number.")
    if not math.isfinite(value) or not lo <= value <= hi:
        abort(400, f"'{name}' must be between {lo} and {hi}.")
    return value


def _int_arg(name, default, lo, hi):
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        abort(400, f"'{name}' must be a whole number.")
    if not lo <= value <= hi:
        abort(400, f"'{name}' must be between {lo} and {hi}.")
    return value


def _time_arg(name):
    raw = request.args.get(name)
    if raw is None:
        return None
    try:
        return parse_query_time(raw)
    except ValueError:
        abort(400, f"'{name}' must be an ISO 8601 date or datetime, e.g. 2026-09-25T12:00:00Z.")


def _type_arg():
    value = request.args.get("type")
    if value is not None and value not in TYPES:
        abort(400, f"'type' must be one of: {', '.join(TYPES)}.")
    return value


def _bbox_arg():
    raw = request.args.get("bbox")
    if raw is None:
        return None
    try:
        min_lon, min_lat, max_lon, max_lat = (float(p) for p in raw.split(","))
    except ValueError:
        abort(400, "'bbox' must be min_lon,min_lat,max_lon,max_lat.")
    if min_lon > max_lon or min_lat > max_lat:
        abort(400, "'bbox' minimums must not exceed its maximums.")
    return min_lon, min_lat, max_lon, max_lat


def _include_raw():
    return "raw" in request.args.get("include", "").split(",")


def _station_or_404(station_id):
    row = db.get_station(get_db(), station_id)
    if row is None:
        abort(404, f"No station with id '{station_id}'. See /v1/stations.")
    return row


# --- Routes ------------------------------------------------------------------

@bp.get("/")
def index():
    return envelope({
        "endpoints": {
            "/v1/stations": "List stations. Filters: type, county, bbox.",
            "/v1/stations/nearest?lat=&lon=": "Nearest stations, with distance_km. Options: limit, type.",
            "/v1/stations/{id}": "One station, with its latest observation.",
            "/v1/stations/{id}/observations/latest": "Latest observation for a station.",
            "/v1/stations/{id}/observations?from=&to=": "Hourly history. Defaults to the last 24 h.",
            "/v1/observations/latest": "Latest observation at every station. Option: stations=a,b.",
            "/v1/observations/nearest?lat=&lon=": "Latest observation from the nearest reporting station.",
            "/v1/health": "Ingest status and data freshness per station.",
        }
    })


@bp.get("/stations")
def list_stations():
    type_, bbox = _type_arg(), _bbox_arg()
    county = request.args.get("county")
    rows = [
        r for r in db.list_stations(get_db())
        if (type_ is None or r["type"] == type_)
        and (county is None or (r["county"] or "").lower() == county.lower())
        and (bbox is None or in_bbox(r, bbox))
    ]
    return envelope([station_json(r) for r in rows])


@bp.get("/stations/nearest")
def nearest_stations():
    lat, lon = _float_arg("lat", -90, 90), _float_arg("lon", -180, 180)
    limit = _int_arg("limit", 3, 1, 50)
    type_ = _type_arg()
    candidates = [dict(r) for r in db.list_stations(get_db()) if type_ is None or r["type"] == type_]
    ranked = by_distance(candidates, lat, lon)[:limit]
    return envelope(
        [{**station_json(s), "distance_km": round(s["distance_km"], 2)} for s in ranked],
        query={"lat": lat, "lon": lon},
    )


@bp.get("/stations/<station_id>")
def get_station(station_id):
    row = _station_or_404(station_id)
    latest = db.latest_observation(get_db(), station_id)
    return envelope({
        **station_json(row),
        "latest_observation": observation_json(latest) if latest else None,
    })


@bp.get("/stations/<station_id>/observations/latest")
def station_latest(station_id):
    _station_or_404(station_id)
    latest = db.latest_observation(get_db(), station_id)
    if latest is None:
        abort(404, f"No observations stored for '{station_id}' yet.")
    return envelope(observation_json(latest, _include_raw()))


@bp.get("/stations/<station_id>/observations")
def station_observations(station_id):
    _station_or_404(station_id)
    end = _time_arg("to") or timeutil.utcnow()
    start = _time_arg("from") or end - timedelta(hours=HISTORY_DEFAULT_HOURS)
    if start > end:
        abort(400, "'from' must not be after 'to'.")
    after = _time_arg("after")
    limit = _int_arg("limit", HISTORY_DEFAULT_LIMIT, 1, HISTORY_MAX_LIMIT)

    rows = db.observations_between(
        get_db(), station_id, to_iso(start), to_iso(end),
        after=to_iso(after) if after else None, limit=limit + 1,
    )
    next_url = None
    if len(rows) > limit:
        rows = rows[:limit]
        args = {**request.args.to_dict(), "from": to_iso(start), "to": to_iso(end),
                "after": rows[-1]["observed_at"]}
        next_url = url_for(".station_observations", station_id=station_id, **args)

    raw = _include_raw()
    return envelope(
        [observation_json(r, raw) for r in rows],
        **{"from": to_iso(start), "to": to_iso(end), "next": next_url},
    )


@bp.get("/observations/latest")
def latest_all():
    ids = None
    if request.args.get("stations"):
        ids = [s.strip() for s in request.args["stations"].split(",") if s.strip()]
        known = {r["id"] for r in db.list_stations(get_db())}
        unknown = [i for i in ids if i not in known]
        if unknown:
            abort(400, f"Unknown station id(s): {', '.join(unknown)}.")
    raw = _include_raw()
    rows = db.latest_observations(get_db(), ids)
    return envelope([{**observation_json(r, raw), "station": station_ref(r)} for r in rows])


@bp.get("/observations/nearest")
def nearest_observation():
    lat, lon = _float_arg("lat", -90, 90), _float_arg("lon", -180, 180)
    max_age_h = _float_arg("max_age_h", 0, 24 * 7, default=current_app.config["STALE_AFTER_HOURS"])
    cutoff = to_iso(timeutil.utcnow() - timedelta(hours=max_age_h))
    fresh = [r for r in db.latest_observations(get_db()) if r["observed_at"] >= cutoff]
    if not fresh:
        abort(404, f"No station has reported in the last {max_age_h:g} hours.")

    def distance(r):
        return haversine((lat, lon), (r["station_lat"], r["station_lon"]))

    best = min(fresh, key=distance)
    station = {**station_ref(best), "distance_km": round(distance(best), 2)}
    return envelope(
        {**observation_json(best, _include_raw()), "station": station},
        query={"lat": lat, "lon": lon, "max_age_h": max_age_h},
    )


@bp.get("/health")
def health():
    stale_after = current_app.config["STALE_AFTER_HOURS"]
    now = timeutil.utcnow()
    stations = []
    for r in db.ingest_status(get_db()):
        last = r["last_observed_at"]
        if last is None:
            status = "no_data"
        elif now - from_iso(last) > timedelta(hours=stale_after):
            status = "stale"
        else:
            status = "ok"
        stations.append({
            "id": r["id"],
            "name": r["name"],
            "status": status,
            "last_observed_at": last,
            "last_ingest": None if r["started_at"] is None else {
                "feed": r["feed"],
                "started_at": r["started_at"],
                "http_status": r["http_status"],
                "rows_upserted": r["rows_upserted"],
                "error": r["error"],
            },
        })
    ok = sum(s["status"] == "ok" for s in stations)
    overall = "ok" if stations and ok == len(stations) else "degraded" if ok else "failing"
    return envelope({
        "status": overall,
        "stations_ok": ok,
        "stations_total": len(stations),
        "stale_after_hours": stale_after,
        "stations": stations,
    })


@bp.after_request
def allow_cross_origin(response):
    # Read-only public data, so any web page may call it.
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


def register_error_handlers(app):
    @app.errorhandler(HTTPException)
    def problem(e):
        response = e.get_response()
        response.data = json.dumps({
            "type": "about:blank",
            "title": e.name,
            "status": e.code,
            "detail": e.description,
        })
        response.content_type = "application/problem+json"
        return response
