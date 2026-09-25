import re

import pytest

from met_api.geo import haversine
from met_api.stations import (
    COUNTIES, TYPES, by_distance, fold, in_bbox, load_csv, matches, near_list, nearest,
)

# Island of Ireland, with a small margin.
IRELAND_BBOX = (-10.7, 51.3, -5.9, 55.45)
# Stations whose county hasn't been confirmed yet (see PLAN.md, open questions).
COUNTY_UNCONFIRMED = {"limerick-clareville", "mount-russell", "nealstown"}

STATIONS = load_csv()
BY_ID = {s["id"]: s for s in STATIONS}


def test_counts():
    assert sum(s["type"] == "synoptic" for s in STATIONS) == 25
    assert sum(s["type"] == "automatic" for s in STATIONS) == 78


def test_ids_are_readable_and_unique():
    ids = [s["id"] for s in STATIONS]
    assert len(ids) == len(set(ids))
    for station_id in ids:
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", station_id), station_id


def test_upstream_keys_are_unique():
    for key in ("metweb_slug", "station_number"):
        values = [s[key] for s in STATIONS if s[key] is not None]
        assert len(values) == len(set(values)), key


def test_no_id_is_another_stations_met_eireann_id():
    # Old Met Éireann IDs redirect to ours, so they must never mean a different station.
    slugs = {s["metweb_slug"]: s["id"] for s in STATIONS if s["metweb_slug"]}
    for s in STATIONS:
        assert slugs.get(s["id"], s["id"]) == s["id"], s["id"]


def test_confusing_met_eireann_ids_are_replaced():
    renamed = {"dublin": "dublin-airport", "cork": "cork-airport", "shannon": "shannon-airport",
               "knock": "knock-airport", "casement": "casement-aerodrome",
               "valentia": "valentia-observatory", "mt-dillon": "mount-dillon",
               "markree-castle": "markree", "newport-furnace": "newport"}
    by_slug = {s["metweb_slug"]: s["id"] for s in STATIONS if s["metweb_slug"]}
    for slug, new_id in renamed.items():
        assert by_slug[slug] == new_id


def test_names_are_clean():
    for s in STATIONS:
        # No shouting capitals or facility codes like WTP / WWTP / Agr Coll.
        shouting = set(re.findall(r"\b[A-Z]{3,}\b", s["name"])) - {"JFK"}
        assert not shouting, s["name"]
        assert not re.search(r"\b(WTP|WWTP|Agr|Coll|Agri)\b", s["name"], re.I), s["name"]
        assert s["name"] == s["name"].strip() and "  " not in s["name"]


def test_every_county_is_real_and_set():
    for s in STATIONS:
        if s["id"] in COUNTY_UNCONFIRMED:
            assert s["county"] is None
        else:
            assert s["county"] in COUNTIES, s["id"]


def test_automatic_stations_keep_met_eireanns_name():
    for s in STATIONS:
        if s["type"] == "automatic":
            assert s["official_name"] and s["station_number"], s["id"]


def test_no_two_stations_share_coordinates():
    seen = {}
    for s in STATIONS:
        point = (s["lat"], s["lon"])
        assert point not in seen, f"{s['id']} has the same coordinates as {seen[point]}"
        seen[point] = s["id"]


def test_every_station_is_in_ireland():
    assert [s["id"] for s in STATIONS if not in_bbox(s, IRELAND_BBOX)] == []


def test_types_and_feeds():
    for s in STATIONS:
        assert s["type"] in TYPES
        assert (s["metweb_slug"] is not None) == (s["type"] == "synoptic"), s["id"]


@pytest.mark.parametrize("query, expected", [
    ("carlow", "oak-park"),
    ("Fermoy", "moore-park"),
    ("bundoran", "finner"),
    ("lanesborough", "mount-dillon"),
    ("swords", "dublin-airport"),
    ("mt-dillon", "mount-dillon"),      # Met Éireann's ID
    ("TUAM WWTP", "tuam"),              # Met Éireann's name
    ("  valentia ", "valentia-observatory"),
])
def test_search_finds_stations(query, expected):
    assert expected in [s["id"] for s in STATIONS if matches(s, query)]


def test_search_ignores_accents_and_case():
    assert fold("Dún Laoghaire") == "dun laoghaire"
    assert matches(BY_ID["athenry"], "ÁTHENRY")


def test_near_list():
    assert near_list(BY_ID["roches-point"]) == ["Whitegate", "Cork Harbour"]
    assert near_list(BY_ID["athenry"]) == []


def test_haversine_known_distance():
    assert haversine((0, 0), (0, 1)) == pytest.approx(111.195, abs=0.01)
    assert haversine((53.0, -8.0), (53.0, -8.0)) == 0


@pytest.mark.parametrize("point, expected", [
    ((54.076944, -7.611667), "ballyhaise"),   # the test point from met.py
    ((52.7038, -8.8642), "shannon-airport"),  # Shannon town used to resolve to Athenry
    ((53.4273, -6.2436), "dublin-airport"),
    ((51.8413, -8.4911), "cork-airport"),
])
def test_nearest_synoptic(point, expected):
    synoptic = [s for s in STATIONS if s["type"] == "synoptic"]
    assert nearest(synoptic, *point)[0]["id"] == expected


def test_nearest_automatic_matches_met_py():
    automatic = [s for s in STATIONS if s["type"] == "automatic"]
    best = nearest(automatic, 54.076944, -7.611667)[0]
    assert (best["id"], best["station_number"]) == ("ballinamore", 5385)
    assert best["distance_km"] == pytest.approx(12.7, abs=0.1)


def test_by_distance_is_sorted_and_complete():
    ranked = by_distance(STATIONS, 53.35, -6.26)
    assert len(ranked) == len(STATIONS)
    distances = [s["distance_km"] for s in ranked]
    assert distances == sorted(distances)
