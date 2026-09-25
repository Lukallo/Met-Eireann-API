"""OpenAPI description of the /v1 API, served at /v1/openapi.json and rendered at /docs.

tests/test_docs.py checks this against the real routes and their accepted
parameters, so it can't silently drift.
"""

from flask import Blueprint, jsonify, redirect, render_template_string, url_for

PUBLIC_URL = "https://api.lcvetkovic.com"

docs_bp = Blueprint("docs_pages", __name__)


def _q(name, description, schema, required=False, example=None):
    param = {"name": name, "in": "query", "required": required,
             "description": description, "schema": schema}
    if example is not None:
        param["example"] = example
    return param


def _ok(schema_ref, description, many=False, meta=None):
    data = {"type": "array", "items": {"$ref": schema_ref}} if many else {"$ref": schema_ref}
    meta_schema = {"$ref": "#/components/schemas/Meta"}
    if meta:
        meta_schema = {"allOf": [meta_schema, {"type": "object", "properties": meta}]}
    return {"200": {"description": description, "content": {"application/json": {"schema": {
        "type": "object", "properties": {"data": data, "meta": meta_schema}}}}}}


def _errors(*codes):
    text = {400: "Invalid parameter.", 404: "Not found.", 301: "Old or differently written ID; "
            "follow the Location header."}
    return {str(c): {"description": text[c]} if c == 301 else {
        "description": text[c],
        "content": {"application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}},
    } for c in codes}


STATION_ID = {"name": "station_id", "in": "path", "required": True,
              "description": "Station ID, e.g. `dublin-airport`. Met Éireann's IDs (`dublin`) redirect.",
              "schema": {"type": "string"}, "example": "dublin-airport"}
LAT = _q("lat", "Latitude in degrees.", {"type": "number", "minimum": -90, "maximum": 90},
         required=True, example=53.35)
LON = _q("lon", "Longitude in degrees (negative in Ireland).",
         {"type": "number", "minimum": -180, "maximum": 180}, required=True, example=-6.26)
INCLUDE = _q("include", "`raw` adds Met Éireann's untouched record to each reading.",
             {"type": "string", "enum": ["raw"]})
ALL = _q("all", "Also list stations without live readings (most automatic stations).",
         {"type": "boolean", "default": False})
TYPE = _q("type", "`synoptic` (main weather stations) or `automatic`.",
          {"type": "string", "enum": ["synoptic", "automatic"]})
NUMBER = {"type": ["number", "null"]}


def spec():
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Irish Weather Observations API",
            "version": "1.1.0",
            "description": (
                "Hourly weather readings from Met Éireann's stations, cleaned up: real numbers, "
                "`null` for missing values, UTC timestamps plus Irish local time, units in every "
                "field name, readable station IDs, and history kept beyond the two days Met "
                "Éireann's own feed offers.\n\n"
                "Unofficial. Data: Met Éireann (www.met.ie), CC BY 4.0. Met Éireann's readings are "
                "not quality-controlled: impossible values are returned as `null` with a flag.\n\n"
                "Every response is `{\"data\": ..., \"meta\": ...}`. Errors use "
                "`application/problem+json`. Unknown query parameters are rejected, so typos "
                "don't go unnoticed."
            ),
            "license": {"name": "Data: CC BY 4.0", "url": "https://creativecommons.org/licenses/by/4.0/"},
        },
        "servers": [
            {"url": "/", "description": "This server"},
            {"url": PUBLIC_URL, "description": "Public instance"},
        ],
        "tags": [
            {"name": "Stations", "description": "Where readings come from."},
            {"name": "Observations", "description": "Hourly readings and summaries."},
            {"name": "Service", "description": "Status of the data collection."},
        ],
        "paths": {
            "/v1/stations": {"get": {
                "tags": ["Stations"], "summary": "List or search stations",
                "description": "Stations with live readings, sorted by name. `q` searches names, "
                               "counties and nearby towns, so `q=carlow` finds Oak Park.",
                "parameters": [
                    _q("q", "Search text.", {"type": "string"}, example="carlow"),
                    _q("county", "County name, any case.", {"type": "string"}, example="Cork"),
                    TYPE,
                    _q("bbox", "Bounding box: `min_lon,min_lat,max_lon,max_lat`.",
                       {"type": "string"}, example="-6.5,53.2,-6.0,53.5"),
                    ALL,
                ],
                "responses": {**_ok("#/components/schemas/Station", "Stations.", many=True),
                              **_errors(400)},
            }},
            "/v1/stations/nearest": {"get": {
                "tags": ["Stations"], "summary": "Stations nearest a point",
                "parameters": [LAT, LON,
                               _q("limit", "How many.", {"type": "integer", "minimum": 1,
                                                         "maximum": 50, "default": 3}),
                               TYPE, ALL],
                "responses": {**_ok("#/components/schemas/StationWithDistance",
                                    "Nearest first.", many=True), **_errors(400)},
            }},
            "/v1/stations/{station_id}": {"get": {
                "tags": ["Stations"], "summary": "One station, with its latest reading",
                "parameters": [STATION_ID],
                "responses": {**_ok("#/components/schemas/StationDetail", "The station."),
                              **_errors(301, 404)},
            }},
            "/v1/stations/{station_id}/observations/latest": {"get": {
                "tags": ["Observations"], "summary": "Latest reading at a station",
                "parameters": [STATION_ID, INCLUDE],
                "responses": {**_ok("#/components/schemas/Observation", "The newest reading."),
                              **_errors(301, 400, 404)},
            }},
            "/v1/stations/{station_id}/observations": {"get": {
                "tags": ["Observations"], "summary": "Hourly history",
                "description": "Readings oldest first. `meta.coverage` lists any hours with no "
                               "reading. With `fill=true` each missing hour is returned as an entry "
                               "with null values and the flag `missing`, which suits charts. "
                               "Follow `meta.next` for more pages.",
                "parameters": [
                    STATION_ID,
                    _q("from", "Start (ISO 8601; no offset means UTC). Default: 24 hours before `to`.",
                       {"type": "string"}, example="2026-09-24T00:00:00Z"),
                    _q("to", "End (ISO 8601). Default: now.", {"type": "string"}),
                    _q("limit", "Page size.", {"type": "integer", "minimum": 1, "maximum": 5000,
                                               "default": 500}),
                    _q("after", "Pagination cursor, set by `meta.next`.", {"type": "string"}),
                    _q("fill", "Return an entry for every hour, including missing ones.",
                       {"type": "boolean", "default": False}),
                    INCLUDE,
                ],
                "responses": {**_ok("#/components/schemas/Observation", "Readings.", many=True,
                                    meta={"from": {"type": "string"}, "to": {"type": "string"},
                                          "next": {"type": ["string", "null"]},
                                          "coverage": {"$ref": "#/components/schemas/Coverage"}}),
                              **_errors(301, 400, 404)},
            }},
            "/v1/stations/{station_id}/daily": {"get": {
                "tags": ["Observations"], "summary": "Daily summaries",
                "description": "One entry per Irish calendar day (midnight to midnight, "
                               "Europe/Dublin), including days with no data. Met Éireann's "
                               "official climate day runs 09:00–09:00 UTC, so totals can differ "
                               "from theirs. `complete` says whether every hour was reported.",
                "parameters": [
                    STATION_ID,
                    _q("from", "First day. Default: 6 days before `to`.",
                       {"type": "string", "format": "date"}, example="2026-09-19"),
                    _q("to", "Last day (at most 366 days after `from`). Default: today.",
                       {"type": "string", "format": "date"}),
                ],
                "responses": {**_ok("#/components/schemas/DailySummary", "Days.", many=True),
                              **_errors(301, 400, 404)},
            }},
            "/v1/observations/latest": {"get": {
                "tags": ["Observations"], "summary": "Latest reading at every station",
                "parameters": [
                    _q("stations", "Comma-separated station IDs. Default: all.",
                       {"type": "string"}, example="dublin-airport,cork-airport"),
                    INCLUDE,
                ],
                "responses": {**_ok("#/components/schemas/StationObservation", "Readings.",
                                    many=True), **_errors(400)},
            }},
            "/v1/observations/nearest": {"get": {
                "tags": ["Observations"], "summary": "Latest reading nearest a point",
                "description": "The newest reading from the nearest station that has reported "
                               "within `max_age_h` and is within `max_distance_km`. Everywhere in "
                               "Ireland is within 80 km of a main station.",
                "parameters": [
                    LAT, LON,
                    _q("max_age_h", "Ignore stations whose newest reading is older than this.",
                       {"type": "number", "minimum": 0, "maximum": 168, "default": 3}),
                    _q("max_distance_km", "Ignore stations further away than this.",
                       {"type": "number", "minimum": 0, "maximum": 1000, "default": 80}),
                    INCLUDE,
                ],
                "responses": {**_ok("#/components/schemas/StationObservation", "The reading."),
                              **_errors(400, 404)},
            }},
            "/v1/health": {"get": {
                "tags": ["Service"], "summary": "Data freshness per station",
                "responses": _ok("#/components/schemas/Health", "Status."),
            }},
        },
        "components": {"schemas": {
            "Meta": {"type": "object", "properties": {
                "count": {"type": "integer"},
                "generated_at": {"type": "string", "format": "date-time"},
                "attribution": {"type": "string"},
            }},
            "Problem": {"type": "object", "properties": {
                "type": {"type": "string"}, "title": {"type": "string"},
                "status": {"type": "integer"}, "detail": {"type": "string"},
            }},
            "Station": {"type": "object", "properties": {
                "id": {"type": "string", "example": "oak-park"},
                "name": {"type": "string", "example": "Oak Park"},
                "county": {"type": ["string", "null"], "example": "Carlow"},
                "type": {"type": "string", "enum": ["synoptic", "automatic"]},
                "has_observations": {"type": "boolean"},
                "location": {"type": "object", "properties": {
                    "lat": {"type": "number"}, "lon": {"type": "number"},
                    "elevation_m": NUMBER}},
                "near": {"type": "array", "items": {"type": "string"}, "example": ["Carlow"]},
                "upstream": {"type": "object", "description": "Met Éireann's identifiers.",
                             "properties": {
                                 "metweb_slug": {"type": ["string", "null"]},
                                 "station_number": {"type": ["integer", "null"]},
                                 "official_name": {"type": ["string", "null"]}}},
            }},
            "StationWithDistance": {"allOf": [
                {"$ref": "#/components/schemas/Station"},
                {"type": "object", "properties": {"distance_km": {"type": "number"}}}]},
            "StationDetail": {"allOf": [
                {"$ref": "#/components/schemas/Station"},
                {"type": "object", "properties": {"latest_observation": {"oneOf": [
                    {"$ref": "#/components/schemas/Observation"}, {"type": "null"}]}}}]},
            "Observation": {"type": "object", "properties": {
                "station_id": {"type": "string"},
                "observed_at": {"type": "string", "format": "date-time",
                                "description": "UTC.", "example": "2026-09-25T12:00:00Z"},
                "local_time": {"type": "string", "format": "date-time",
                               "example": "2026-09-25T13:00:00+01:00"},
                "temperature_c": {**NUMBER, "description": "Whole degrees, as reported."},
                "humidity_pct": NUMBER,
                "pressure_msl_hpa": {**NUMBER, "description": "Mean sea level pressure."},
                "rainfall_mm": {**NUMBER, "description": "Rain in the hour."},
                "wind": {"type": "object", "properties": {
                    "speed_kt": NUMBER, "speed_kmh": NUMBER,
                    "gust_kt": NUMBER, "gust_kmh": NUMBER,
                    "direction_deg": {**NUMBER, "description": "Only when Met Éireann gives it."},
                    "direction_cardinal": {"type": ["string", "null"], "example": "SW"}}},
                "weather": {"type": "object", "properties": {
                    "symbol": {"type": ["string", "null"], "example": "04n"},
                    "description": {"type": ["string", "null"], "example": "Cloudy"}}},
                "flags": {"type": "array", "items": {"type": "string"},
                          "description": "Empty when all is well. `missing` marks a filled-in "
                                         "hour; `<field>_out_of_range` marks an impossible value "
                                         "that was replaced with null.",
                          "example": []},
                "raw": {"type": "object", "description": "Only with `include=raw`."},
            }},
            "StationObservation": {"allOf": [
                {"$ref": "#/components/schemas/Observation"},
                {"type": "object", "properties": {"station": {"type": "object", "properties": {
                    "id": {"type": "string"}, "name": {"type": "string"},
                    "county": {"type": ["string", "null"]},
                    "location": {"type": "object"},
                    "distance_km": {"type": "number", "description": "Only from /nearest."}}}}}]},
            "Coverage": {"type": "object", "properties": {
                "hours_expected": {"type": "integer"},
                "hours_reported": {"type": "integer"},
                "gaps": {"type": "array", "items": {"type": "object", "properties": {
                    "from": {"type": "string"}, "to": {"type": "string"},
                    "hours": {"type": "integer"}}}},
                "gaps_truncated": {"type": "boolean"},
            }},
            "DailySummary": {"type": "object", "properties": {
                "date": {"type": "string", "format": "date"},
                "hours_reported": {"type": "integer"},
                "hours_expected": {"type": "integer",
                                   "description": "24, or 23/25 when the clocks change."},
                "complete": {"type": "boolean"},
                **{k: NUMBER for k in (
                    "temperature_min_c", "temperature_max_c", "temperature_mean_c",
                    "rainfall_total_mm", "wind_speed_max_kt", "wind_speed_max_kmh",
                    "wind_gust_max_kt", "wind_gust_max_kmh", "humidity_mean_pct",
                    "pressure_msl_min_hpa", "pressure_msl_max_hpa")},
            }},
            "Health": {"type": "object", "properties": {
                "status": {"type": "string", "enum": ["ok", "degraded", "failing"]},
                "stations_ok": {"type": "integer"},
                "stations_total": {"type": "integer"},
                "stale_after_hours": {"type": "number"},
                "stations": {"type": "array", "items": {"type": "object"}},
            }},
        }},
    }


DOCS_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Irish Weather Observations API</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
  <style>body { margin: 0; } .swagger-ui .topbar { display: none; }</style>
</head>
<body>
  <div id="docs"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    SwaggerUIBundle({ url: {{ spec_url | tojson }}, dom_id: "#docs",
                      deepLinking: true, tryItOutEnabled: true });
  </script>
</body>
</html>
"""


@docs_bp.get("/v1/openapi.json")
def openapi_json():
    return jsonify(spec())


@docs_bp.get("/docs")
def docs():
    return render_template_string(DOCS_PAGE, spec_url=url_for("docs_pages.openapi_json"))


@docs_bp.get("/")
def home():
    return redirect(url_for("docs_pages.docs"))
