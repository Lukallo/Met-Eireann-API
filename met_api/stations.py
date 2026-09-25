"""Station registry: loading ``data/stations.csv``, searching it, and location queries.

The CSV is the single source of truth for station metadata. ``flask init-db``
and ``flask ingest`` copy it into the ``stations`` table.

IDs are readable names (``dublin-airport``, not Met Éireann's ``dublin``).
Met Éireann's own key stays in ``metweb_slug``, and the API redirects the old
IDs to the new ones.
"""

import csv
import unicodedata
from pathlib import Path

from .geo import haversine

CSV_PATH = Path(__file__).parent / "data" / "stations.csv"
TYPES = ("synoptic", "automatic")
COUNTIES = (
    "Carlow", "Cavan", "Clare", "Cork", "Donegal", "Dublin", "Galway", "Kerry", "Kildare",
    "Kilkenny", "Laois", "Leitrim", "Limerick", "Longford", "Louth", "Mayo", "Meath",
    "Monaghan", "Offaly", "Roscommon", "Sligo", "Tipperary", "Waterford", "Westmeath",
    "Wexford", "Wicklow",
)


def _optional(value, cast=str):
    value = value.strip()
    return cast(value) if value else None


def load_csv(path=CSV_PATH):
    """Return every station in the CSV as a dict with typed values."""
    with open(path, newline="", encoding="utf-8") as f:
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "type": row["type"],
                "county": _optional(row["county"]),
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "elevation_m": _optional(row["elevation_m"], float),
                "near": _optional(row["near"]),  # ';'-separated nearby towns
                "metweb_slug": _optional(row["metweb_slug"]),
                "station_number": _optional(row["station_number"], int),
                "official_name": _optional(row["official_name"]),
            }
            for row in csv.DictReader(f)
        ]


def near_list(station):
    return [t for t in (station["near"] or "").split(";") if t]


def fold(text):
    """Lower-case and strip accents, for forgiving text matching."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def matches(station, query):
    """True if ``query`` appears in the station's ID, name, county, nearby towns or upstream keys."""
    haystack = " ".join(
        str(station[k] or "") for k in ("id", "name", "county", "near", "metweb_slug", "official_name")
    )
    return fold(query.strip()) in fold(haystack)


def by_distance(stations, lat, lon):
    """Stations sorted nearest first, each with an added ``distance_km``."""
    return sorted(
        ({**s, "distance_km": haversine((lat, lon), (s["lat"], s["lon"]))} for s in stations),
        key=lambda s: s["distance_km"],
    )


def nearest(stations, lat, lon, limit=1):
    return by_distance(stations, lat, lon)[:limit]


def in_bbox(station, bbox):
    """True if the station lies inside ``(min_lon, min_lat, max_lon, max_lat)``."""
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lat <= station["lat"] <= max_lat and min_lon <= station["lon"] <= max_lon
