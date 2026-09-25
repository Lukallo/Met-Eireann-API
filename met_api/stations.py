"""Station registry: loading ``data/stations.csv`` and location searches.

The CSV is the single source of truth for station metadata. ``flask init-db``
and ``flask ingest`` copy it into the ``stations`` table.
"""

import csv
from pathlib import Path

from .geo import haversine

CSV_PATH = Path(__file__).parent / "data" / "stations.csv"
TYPES = ("synoptic", "automatic")


def _optional(value, cast):
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
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "elevation_m": _optional(row["elevation_m"], float),
                "county": _optional(row["county"], str),
                "metweb_slug": _optional(row["metweb_slug"], str),
                "station_number": _optional(row["station_number"], int),
            }
            for row in csv.DictReader(f)
        ]


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
