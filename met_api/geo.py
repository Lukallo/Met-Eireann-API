"""Great-circle distance between points on the Earth."""

import math

EARTH_RADIUS_KM = 6371.0088


def haversine(a, b):
    """Distance in km between two ``(lat, lon)`` points given in degrees."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))
