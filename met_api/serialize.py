"""Turning database rows into the API's JSON shapes."""

import json

from . import quality
from .stations import near_list
from .timeutil import IRISH_TZ, from_iso

KT_TO_KMH = 1.852


def num(value):
    """Numbers as reported: ``9.0`` becomes ``9``, since the feed gives whole degrees."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def kmh(knots):
    return None if knots is None else round(knots * KT_TO_KMH, 1)


def rounded(value, digits=1):
    return None if value is None else num(round(value, digits))


def station_json(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "county": row["county"],
        "type": row["type"],
        "has_observations": row["metweb_slug"] is not None,
        "location": {"lat": row["lat"], "lon": row["lon"], "elevation_m": row["elevation_m"]},
        "near": near_list(row),
        "upstream": {
            "metweb_slug": row["metweb_slug"],
            "station_number": row["station_number"],
            "official_name": row["official_name"],
        },
    }


def station_ref(row):
    """Short station block embedded in observations that span several stations."""
    return {
        "id": row["station_id"],
        "name": row["station_name"],
        "county": row["station_county"],
        "location": {"lat": row["station_lat"], "lon": row["station_lon"]},
    }


def _observation(station_id, observed_at, values, row, flags):
    local = from_iso(observed_at).astimezone(IRISH_TZ)
    return {
        "station_id": station_id,
        "observed_at": observed_at,
        "local_time": local.isoformat(),
        "temperature_c": num(values["temperature_c"]),
        "humidity_pct": num(values["humidity_pct"]),
        "pressure_msl_hpa": num(values["pressure_msl_hpa"]),
        "rainfall_mm": num(values["rainfall_mm"]),
        "wind": {
            "speed_kt": num(values["wind_speed_kt"]),
            "speed_kmh": kmh(values["wind_speed_kt"]),
            "gust_kt": num(values["wind_gust_kt"]),
            "gust_kmh": kmh(values["wind_gust_kt"]),
            "direction_deg": num(values["wind_dir_deg"]),
            "direction_cardinal": row["wind_dir_cardinal"] if row else None,
        },
        "weather": {
            "symbol": row["weather_symbol"] if row else None,
            "description": row["weather_description"] if row else None,
        },
        "flags": flags,
    }


def observation_json(row, include_raw=False):
    values, flags = quality.check(row)
    obs = _observation(row["station_id"], row["observed_at"], values, row, flags)
    if include_raw:
        obs["raw"] = json.loads(row["raw_json"])
    return obs


def missing_observation_json(station_id, observed_at):
    """Placeholder for an hour with no reading: the same shape, all values null."""
    values = dict.fromkeys(quality.LIMITS)
    return _observation(station_id, observed_at, values, None, ["missing"])


def daily_json(day, row, hours_expected):
    """One Irish calendar day. ``row`` is None when nothing was stored that day."""
    get = (lambda key: row[key]) if row else (lambda key: None)
    reported = row["hours_reported"] if row else 0
    return {
        "date": day.isoformat(),
        "hours_reported": reported,
        "hours_expected": hours_expected,
        "complete": hours_expected > 0 and reported >= hours_expected,
        "temperature_min_c": num(get("temperature_min_c")),
        "temperature_max_c": num(get("temperature_max_c")),
        "temperature_mean_c": rounded(get("temperature_mean_c")),
        "rainfall_total_mm": rounded(get("rainfall_total_mm")),
        "wind_speed_max_kt": num(get("wind_speed_max_kt")),
        "wind_speed_max_kmh": kmh(get("wind_speed_max_kt")),
        "wind_gust_max_kt": num(get("wind_gust_max_kt")),
        "wind_gust_max_kmh": kmh(get("wind_gust_max_kt")),
        "humidity_mean_pct": rounded(get("humidity_mean_pct")),
        "pressure_msl_min_hpa": num(get("pressure_msl_min_hpa")),
        "pressure_msl_max_hpa": num(get("pressure_msl_max_hpa")),
    }
