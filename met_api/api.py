"""The /v1 JSON API.

Every response is ``{"data": ..., "meta": ...}``. Errors are
``application/problem+json`` (RFC 9457). Times are UTC ``...Z``, and each
observation also carries its Irish local time. The full reference is served
at /docs (see openapi.py).
"""

import difflib
import json
import re
from datetime import timedelta

from flask import Blueprint, abort, current_app, jsonify, redirect, request, url_for
from werkzeug.exceptions import HTTPException

from . import db, timeutil
from .db import get_db
from .geo import haversine
from .params import (
    accepts, bbox_arg, bool_arg, date_arg, float_arg, include_raw, int_arg, time_arg, type_arg,
)
from .serialize import (
    daily_json, missing_observation_json, observation_json, station_json, station_ref,
)
from .stations import by_distance, in_bbox, matches
from .timeutil import due_by, hour_slots, irish_date, irish_day_bounds, to_iso

bp = Blueprint("v1", __name__, url_prefix="/v1")

ATTRIBUTION = "Met Éireann (www.met.ie), CC BY 4.0. Readings are provisional."
HISTORY_DEFAULT_HOURS = 24
HISTORY_DEFAULT_LIMIT = 500
HISTORY_MAX_LIMIT = 5000
DAILY_DEFAULT_DAYS = 7
DAILY_MAX_DAYS = 366
MAX_GAPS_LISTED = 100


# --- Helpers -----------------------------------------------------------------

def envelope(data, **meta):
    if isinstance(data, list):
        meta = {"count": len(data), **meta}
    meta["generated_at"] = to_iso(timeutil.utcnow())
    meta["attribution"] = ATTRIBUTION
    return jsonify(data=data, meta=meta)


def _slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _canonical_id(conn, given):
    """Our ID for ``given`` if it is one of Met Éireann's IDs or a differently written
    version of ours (``Dublin Airport``), else None."""
    old = db.station_by_slug(conn, given)
    if old is not None:
        return old["id"]
    slug = _slugify(given)
    if slug != given and db.get_station(conn, slug) is not None:
        return slug
    return None


def _unknown_station(conn, given):
    ids = [r["id"] for r in db.list_stations(conn)]
    slug = _slugify(given)
    starts = [i for i in ids if slug and i.startswith(slug)]
    close = list(dict.fromkeys(starts + difflib.get_close_matches(slug, ids, n=3, cutoff=0.75)))[:3]
    hint = f" Did you mean {' or '.join(repr(c) for c in close)}?" if close else ""
    return f"No station '{given}'.{hint} Search with /v1/stations?q=..."


def _station(station_id):
    """The station row, a redirect for an old or differently written ID, or a 404."""
    conn = get_db()
    row = db.get_station(conn, station_id)
    if row is not None:
        return row
    canonical = _canonical_id(conn, station_id)
    if canonical is not None:
        target = url_for(request.endpoint, **{**request.view_args, "station_id": canonical},
                         **request.args.to_dict())
        abort(redirect(target, 301))
    abort(404, _unknown_station(conn, station_id))


def _station_list_arg(conn):
    """``stations=a,b`` as our IDs. Met Éireann's IDs are accepted too."""
    raw = request.args.get("stations")
    if not raw:
        return None
    ids = []
    for given in (s.strip() for s in raw.split(",") if s.strip()):
        if db.get_station(conn, given) is not None:
            ids.append(given)
        elif (canonical := _canonical_id(conn, given)) is not None:
            ids.append(canonical)
        else:
            abort(400, _unknown_station(conn, given))
    return ids


def _listed(rows, include_all, type_):
    """Stations without observations are hidden unless ``all=true``."""
    return [r for r in rows
            if (include_all or r["metweb_slug"] is not None)
            and (type_ is None or r["type"] == type_)]


def _coverage(station_id, start, end):
    """Which hours in the window have a reading. Hours not yet due don't count as missing."""
    stored = db.observation_times(get_db(), station_id, to_iso(start), to_iso(end))
    expected = hour_slots(start, min(end, due_by(timeutil.utcnow())))
    have = set(stored)
    missing = [slot for slot in expected if slot not in have]

    gaps = []
    for slot in missing:
        if gaps and timeutil.from_iso(slot) - timeutil.from_iso(gaps[-1]["to"]) == timeutil.HOUR:
            gaps[-1]["to"] = slot
            gaps[-1]["hours"] += 1
        else:
            gaps.append({"from": slot, "to": slot, "hours": 1})
    coverage = {
        "hours_expected": len(expected),
        "hours_reported": len(expected) - len(missing),
        "gaps": gaps[:MAX_GAPS_LISTED],
    }
    if len(gaps) > MAX_GAPS_LISTED:
        coverage["gaps_truncated"] = True
    return coverage, stored, expected


# --- Routes ------------------------------------------------------------------

@bp.get("/")
@accepts()
def index():
    return envelope({
        "docs": url_for("docs_pages.docs"),
        "endpoints": {
            "/v1/stations": "Stations with live readings. Search with q, filter by county.",
            "/v1/stations/nearest?lat=&lon=": "Stations nearest a point, with distance_km.",
            "/v1/stations/{id}": "One station, with its latest reading.",
            "/v1/stations/{id}/observations/latest": "Latest reading at a station.",
            "/v1/stations/{id}/observations?from=&to=": "Hourly history, with any gaps listed.",
            "/v1/stations/{id}/daily?from=&to=": "Daily min/max temperature, rainfall and wind.",
            "/v1/observations/latest": "Latest reading at every station.",
            "/v1/observations/nearest?lat=&lon=": "Latest reading from the nearest reporting station.",
            "/v1/health": "Data freshness per station.",
        },
    })


@bp.get("/stations")
@accepts("q", "county", "type", "bbox", "all")
def list_stations():
    q, county = request.args.get("q"), request.args.get("county")
    type_, bbox, include_all = type_arg(), bbox_arg(), bool_arg("all")
    matching = [
        r for r in _listed(db.list_stations(get_db()), True, type_)
        if (q is None or matches(r, q))
        and (county is None or (r["county"] or "").lower() == county.strip().lower())
        and (bbox is None or in_bbox(r, bbox))
    ]
    shown = _listed(matching, include_all, None)
    meta = {}
    if hidden := len(matching) - len(shown):
        meta["hidden"] = hidden
        meta["hint"] = (f"{hidden} matching station(s) have no live readings and are hidden. "
                        "Add all=true to include them.")
    return envelope([station_json(r) for r in shown], **meta)


@bp.get("/stations/nearest")
@accepts("lat", "lon", "limit", "type", "all")
def nearest_stations():
    lat = float_arg("lat", -90, 90, required=True)
    lon = float_arg("lon", -180, 180, required=True)
    limit = int_arg("limit", 3, 1, 50)
    candidates = [dict(r) for r in _listed(db.list_stations(get_db()), bool_arg("all"), type_arg())]
    ranked = by_distance(candidates, lat, lon)[:limit]
    return envelope(
        [{**station_json(s), "distance_km": round(s["distance_km"], 1)} for s in ranked],
        query={"lat": lat, "lon": lon},
    )


@bp.get("/stations/<station_id>")
@accepts()
def get_station(station_id):
    row = _station(station_id)
    latest = db.latest_observation(get_db(), row["id"])
    return envelope({
        **station_json(row),
        "latest_observation": observation_json(latest) if latest else None,
    })


@bp.get("/stations/<station_id>/observations/latest")
@accepts("include")
def station_latest(station_id):
    row = _station(station_id)
    latest = db.latest_observation(get_db(), row["id"])
    if latest is None:
        reason = ("it has no live feed" if row["metweb_slug"] is None
                  else "nothing has been stored for it yet")
        abort(404, f"No readings for '{row['id']}': {reason}.")
    return envelope(observation_json(latest, include_raw()))


@bp.get("/stations/<station_id>/observations")
@accepts("from", "to", "limit", "after", "fill", "include")
def station_observations(station_id):
    row = _station(station_id)
    end = time_arg("to") or timeutil.utcnow()
    start = time_arg("from") or end - timedelta(hours=HISTORY_DEFAULT_HOURS)
    if start > end:
        abort(400, "'from' must not be after 'to'.")
    after = time_arg("after")
    limit = int_arg("limit", HISTORY_DEFAULT_LIMIT, 1, HISTORY_MAX_LIMIT)
    fill, raw = bool_arg("fill"), include_raw()

    coverage, stored, expected = _coverage(row["id"], start, end)
    after_iso = to_iso(after) if after else None

    if fill:
        # One entry per expected hour, plus any reading that falls outside that grid.
        timeline = sorted(set(expected) | set(stored))
        if after_iso:
            timeline = [t for t in timeline if t > after_iso]
        page, more = timeline[:limit], len(timeline) > limit
        found = ({r["observed_at"]: r for r in
                  db.observations_between(get_db(), row["id"], page[0], page[-1])}
                 if page else {})
        data = [observation_json(found[t], raw) if t in found
                else missing_observation_json(row["id"], t) for t in page]
        last = page[-1] if page else None
    else:
        rows = db.observations_between(get_db(), row["id"], to_iso(start), to_iso(end),
                                       after=after_iso, limit=limit + 1)
        more = len(rows) > limit
        rows = rows[:limit]
        data = [observation_json(r, raw) for r in rows]
        last = rows[-1]["observed_at"] if rows else None

    next_url = None
    if more:
        args = {**request.args.to_dict(), "from": to_iso(start), "to": to_iso(end), "after": last}
        next_url = url_for(".station_observations", station_id=row["id"], **args)
    return envelope(data, **{"from": to_iso(start), "to": to_iso(end), "next": next_url,
                             "coverage": coverage})


@bp.get("/stations/<station_id>/daily")
@accepts("from", "to")
def station_daily(station_id):
    row = _station(station_id)
    today = irish_date(timeutil.utcnow())
    last = date_arg("to") or today
    first = date_arg("from") or last - timedelta(days=DAILY_DEFAULT_DAYS - 1)
    if first > last:
        abort(400, "'from' must not be after 'to'.")
    if (last - first).days >= DAILY_MAX_DAYS:
        abort(400, f"Ask for at most {DAILY_MAX_DAYS} days at a time.")

    stored = {r["local_date"]: r for r in
              db.daily_summaries(get_db(), row["id"], first.isoformat(), last.isoformat())}
    due = due_by(timeutil.utcnow())
    days = []
    day = first
    while day <= last:
        day_start, day_end = irish_day_bounds(day)
        hours_expected = len(hour_slots(day_start, min(day_end - timeutil.HOUR, due)))
        days.append(daily_json(day, stored.get(day.isoformat()), hours_expected))
        day += timedelta(days=1)
    return envelope(days, **{"from": first.isoformat(), "to": last.isoformat(),
                             "timezone": "Europe/Dublin"})


@bp.get("/observations/latest")
@accepts("stations", "include")
def latest_all():
    conn = get_db()
    ids = _station_list_arg(conn)
    raw = include_raw()
    rows = db.latest_observations(conn, ids)
    return envelope([{**observation_json(r, raw), "station": station_ref(r)} for r in rows])


@bp.get("/observations/nearest")
@accepts("lat", "lon", "max_age_h", "max_distance_km", "include")
def nearest_observation():
    cfg = current_app.config
    lat = float_arg("lat", -90, 90, required=True)
    lon = float_arg("lon", -180, 180, required=True)
    max_age_h = float_arg("max_age_h", 0, 24 * 7, default=cfg["STALE_AFTER_HOURS"])
    max_km = float_arg("max_distance_km", 0, 1000, default=cfg["NEAREST_MAX_DISTANCE_KM"])

    cutoff = to_iso(timeutil.utcnow() - timedelta(hours=max_age_h))
    fresh = sorted(
        ((haversine((lat, lon), (r["station_lat"], r["station_lon"])), r)
         for r in db.latest_observations(get_db()) if r["observed_at"] >= cutoff),
        key=lambda pair: pair[0],
    )
    if not fresh:
        abort(404, f"No station has reported in the last {max_age_h:g} hours.")
    distance, best = fresh[0]
    if distance > max_km:
        abort(404, f"No station within {max_km:g} km has reported in the last {max_age_h:g} hours. "
                   f"The nearest that has is {best['station_name']} ({distance:.0f} km away); "
                   f"raise max_distance_km to use it.")
    station = {**station_ref(best), "distance_km": round(distance, 1)}
    return envelope(
        {**observation_json(best, include_raw()), "station": station},
        query={"lat": lat, "lon": lon, "max_age_h": max_age_h, "max_distance_km": max_km},
    )


@bp.get("/health")
@accepts()
def health():
    stale_after = current_app.config["STALE_AFTER_HOURS"]
    now = timeutil.utcnow()
    stations = []
    for r in db.ingest_status(get_db()):
        last = r["last_observed_at"]
        if last is None:
            status = "no_data"
        elif now - timeutil.from_iso(last) > timedelta(hours=stale_after):
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
