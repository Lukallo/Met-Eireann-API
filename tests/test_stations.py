import pytest

from met_api.geo import haversine
from met_api.stations import TYPES, by_distance, in_bbox, load_csv, nearest

# Island of Ireland, with a small margin.
IRELAND_BBOX = (-10.7, 51.3, -5.9, 55.45)

STATIONS = load_csv()


def test_counts():
    assert sum(s["type"] == "synoptic" for s in STATIONS) == 25
    assert sum(s["type"] == "automatic" for s in STATIONS) == 78


def test_ids_and_upstream_keys_are_unique():
    for key in ("id", "metweb_slug", "station_number"):
        values = [s[key] for s in STATIONS if s[key] is not None]
        assert len(values) == len(set(values)), key


def test_no_two_stations_share_coordinates():
    # This is the check that would have caught five stations copied from Athenry.
    seen = {}
    for s in STATIONS:
        point = (s["lat"], s["lon"])
        assert point not in seen, f"{s['id']} has the same coordinates as {seen[point]}"
        seen[point] = s["id"]


def test_every_station_is_in_ireland():
    outside = [s["id"] for s in STATIONS if not in_bbox(s, IRELAND_BBOX)]
    assert outside == []


def test_types_and_feeds():
    for s in STATIONS:
        assert s["type"] in TYPES
        # Only synoptic stations have a slug on the observations feed.
        assert (s["metweb_slug"] is not None) == (s["type"] == "synoptic"), s["id"]


def test_haversine_known_distance():
    # One degree of longitude on the equator.
    assert haversine((0, 0), (0, 1)) == pytest.approx(111.195, abs=0.01)
    assert haversine((53.0, -8.0), (53.0, -8.0)) == 0


@pytest.mark.parametrize(
    "point, expected",
    [
        ((54.076944, -7.611667), "ballyhaise"),  # the test point from met.py
        ((52.7038, -8.8642), "shannon"),         # Shannon town used to resolve to Athenry
        ((53.4273, -6.2436), "dublin"),          # Dublin Airport terminal
        ((51.8413, -8.4911), "cork"),            # Cork Airport
    ],
)
def test_nearest_synoptic(point, expected):
    synoptic = [s for s in STATIONS if s["type"] == "synoptic"]
    assert nearest(synoptic, *point)[0]["id"] == expected


def test_nearest_automatic_matches_met_py():
    automatic = [s for s in STATIONS if s["type"] == "automatic"]
    best = nearest(automatic, 54.076944, -7.611667)[0]
    assert best["station_number"] == 5385
    assert best["distance_km"] == pytest.approx(12.7, abs=0.1)


def test_by_distance_is_sorted_and_complete():
    ranked = by_distance(STATIONS, 53.35, -6.26)
    assert len(ranked) == len(STATIONS)
    distances = [s["distance_km"] for s in ranked]
    assert distances == sorted(distances)
