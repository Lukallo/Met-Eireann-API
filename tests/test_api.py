import json

import pytest

from met_api.ingest import ingest


def data(resp, status=200):
    assert resp.status_code == status, resp.get_json()
    return resp.get_json()["data"]


def meta(resp):
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["meta"]


def problem(resp, status):
    assert resp.status_code == status, resp.get_data(as_text=True)
    assert resp.content_type == "application/problem+json"
    body = resp.get_json()
    assert body["status"] == status
    return body["detail"]


def ids(items):
    return [item["id"] for item in items]


def test_index_and_envelope(client):
    resp = client.get("/v1/")
    body = resp.get_json()
    assert body["data"]["docs"] == "/docs"
    assert "/v1/stations/{id}/daily?from=&to=" in body["data"]["endpoints"]
    assert body["meta"]["generated_at"] == "2026-09-25T05:30:00Z"
    assert "Met Éireann" in body["meta"]["attribution"]
    assert resp.headers["Access-Control-Allow-Origin"] == "*"


# --- Stations ----------------------------------------------------------------

def test_list_shows_stations_with_readings(client):
    resp = client.get("/v1/stations")
    stations = data(resp)
    assert len(stations) == 25 and all(s["has_observations"] for s in stations)
    assert meta(resp)["hidden"] == 78
    assert "all=true" in meta(resp)["hint"]
    names = [s["name"] for s in stations]
    assert names == sorted(names)


def test_list_all_stations(client):
    resp = client.get("/v1/stations?all=true")
    assert len(data(resp)) == 103
    assert "hidden" not in meta(resp)


def test_station_shape(client):
    oak_park = next(s for s in data(client.get("/v1/stations")) if s["id"] == "oak-park")
    assert oak_park == {
        "id": "oak-park",
        "name": "Oak Park",
        "county": "Carlow",
        "type": "synoptic",
        "has_observations": True,
        "location": {"lat": 52.861111, "lon": -6.915278, "elevation_m": None},
        "near": ["Carlow"],
        "upstream": {"metweb_slug": "oak-park", "station_number": None, "official_name": None},
    }


@pytest.mark.parametrize("query, expected", [
    ("q=carlow", ["oak-park"]),
    ("q=fermoy", ["moore-park"]),
    ("q=cork", ["cork-airport", "moore-park", "roches-point", "sherkin-island"]),
    ("county=CORK", ["cork-airport", "moore-park", "roches-point", "sherkin-island"]),
    ("q=dublin", ["casement-aerodrome", "dublin-airport", "phoenix-park"]),
    ("q=dublin&all=true", ["casement-aerodrome", "dublin-airport", "dublin-glasnevin",
                           "phoenix-park", "shanganagh"]),
    ("type=automatic&q=tuam&all=true", ["tuam"]),
    ("bbox=-6.5,53.2,-6.0,53.5", ["casement-aerodrome", "dublin-airport", "phoenix-park"]),
    ("q=nowhere", []),
])
def test_station_search_and_filters(client, query, expected):
    assert sorted(ids(data(client.get(f"/v1/stations?{query}")))) == expected


@pytest.mark.parametrize("query, message", [
    ("type=radar", "'type' must be one of"),
    ("bbox=1,2,3", "'bbox' must be"),
    ("bbox=-6,53,-7,54", "minimums"),
    ("all=maybe", "'all' must be true or false"),
    ("search=cork", "Unknown parameter 'search'. This endpoint accepts: q, county, type, bbox, all"),
])
def test_station_filter_errors(client, query, message):
    assert message in problem(client.get(f"/v1/stations?{query}"), 400)


def test_nearest_stations(client):
    body = client.get("/v1/stations/nearest?lat=52.7038&lon=-8.8642").get_json()
    assert ids(body["data"])[0] == "shannon-airport" and len(body["data"]) == 3
    assert body["data"][0]["distance_km"] == pytest.approx(3.9, abs=0.2)
    assert body["meta"]["query"] == {"lat": 52.7038, "lon": -8.8642}


def test_nearest_stations_skip_those_without_readings(client):
    near_met_py = "lat=54.0769&lon=-7.6117&limit=1"
    assert ids(data(client.get(f"/v1/stations/nearest?{near_met_py}"))) == ["ballyhaise"]
    assert ids(data(client.get(f"/v1/stations/nearest?{near_met_py}&all=true"))) == ["ballinamore"]


@pytest.mark.parametrize("query, message", [
    ("lon=-8", "'lat' is required"),
    ("lat=abc&lon=-8", "'lat' must be a number"),
    ("lat=100&lon=-8", "'lat' must be between"),
    ("lat=nan&lon=-8", "'lat' must be between"),
    ("lat=53&lon=-8&limit=0", "'limit' must be between"),
    ("lat=53&lon=-8&limit=two", "'limit' must be a whole number"),
    ("lattitude=53&lon=-8", "Unknown parameter 'lattitude'"),
])
def test_nearest_stations_validation(client, query, message):
    assert message in problem(client.get(f"/v1/stations/nearest?{query}"), 400)


def test_station_detail_embeds_latest(client):
    station = data(client.get("/v1/stations/athenry"))
    assert station["latest_observation"]["observed_at"] == "2026-09-25T03:00:00Z"
    assert data(client.get("/v1/stations/cork-airport"))["latest_observation"] is None


@pytest.mark.parametrize("old, new", [
    ("/v1/stations/dublin", "/v1/stations/dublin-airport"),
    ("/v1/stations/mt-dillon", "/v1/stations/mount-dillon"),
    ("/v1/stations/Dublin%20Airport", "/v1/stations/dublin-airport"),
    ("/v1/stations/cork/observations?from=2026-09-24",
     "/v1/stations/cork-airport/observations?from=2026-09-24"),
    ("/v1/stations/valentia/daily", "/v1/stations/valentia-observatory/daily"),
])
def test_old_and_loose_ids_redirect(client, old, new):
    resp = client.get(old)
    assert resp.status_code == 301
    assert resp.headers["Location"] == new


def test_redirect_is_followed_to_the_data(client):
    resp = client.get("/v1/stations/dublin", follow_redirects=True)
    assert data(resp)["id"] == "dublin-airport"


def test_unknown_station_suggests_close_ids(client):
    detail = problem(client.get("/v1/stations/dublin-aiport"), 404)
    assert "No station 'dublin-aiport'. Did you mean 'dublin-airport'?" in detail
    assert "Did you mean 'roches-point'?" in problem(client.get("/v1/stations/roches"), 404)
    detail = problem(client.get("/v1/stations/nowhere/observations"), 404)
    assert "Search with /v1/stations?q=" in detail


# --- Observations ------------------------------------------------------------

def test_station_latest(client):
    obs = data(client.get("/v1/stations/athenry/observations/latest"))
    assert obs == {
        "station_id": "athenry",
        "observed_at": "2026-09-25T03:00:00Z",
        "local_time": "2026-09-25T04:00:00+01:00",
        "temperature_c": 9,
        "humidity_pct": None,
        "pressure_msl_hpa": 1012,
        "rainfall_mm": None,
        "wind": {
            "speed_kt": 9, "speed_kmh": 16.7, "gust_kt": None, "gust_kmh": None,
            "direction_deg": None, "direction_cardinal": "NNE",
        },
        "weather": {"symbol": "03n", "description": "Partly cloudy"},
        "flags": [],
    }


def test_include_raw(client):
    obs = data(client.get("/v1/stations/athenry/observations/latest?include=raw"))
    assert obs["raw"]["reportTime"] == "04:00"
    assert "'include' only accepts 'raw'" in problem(
        client.get("/v1/stations/athenry/observations/latest?include=everything"), 400)


def test_station_latest_without_data(client):
    assert "it has no live feed" in problem(
        client.get("/v1/stations/glenveagh-national-park/observations/latest"), 404)
    assert "nothing has been stored for it yet" in problem(
        client.get("/v1/stations/cork-airport/observations/latest"), 404)


def test_impossible_values_are_nulled_and_flagged(client, conn, registry):
    record = {"date": "25-09-2026", "reportTime": "05:00", "temperature": "85",
              "humidity": "140", "pressure": "1013", "windSpeed": "12"}
    ingest(conn, [registry["mullingar"]], "today", lambda slug, feed: (200, [record]))
    obs = data(client.get("/v1/stations/mullingar/observations/latest"))
    assert obs["temperature_c"] is None and obs["humidity_pct"] is None
    assert obs["pressure_msl_hpa"] == 1013 and obs["wind"]["speed_kt"] == 12
    assert obs["flags"] == ["temperature_c_out_of_range", "humidity_pct_out_of_range"]
    daily = data(client.get("/v1/stations/mullingar/daily?from=2026-09-25&to=2026-09-25"))
    assert daily[0]["temperature_max_c"] is None and daily[0]["hours_reported"] == 1


def test_history_defaults_to_last_24_hours_and_reports_gaps(client):
    resp = client.get("/v1/stations/athenry/observations")
    m = meta(resp)
    assert (m["from"], m["to"], m["count"], m["next"]) == (
        "2026-09-24T05:30:00Z", "2026-09-25T05:30:00Z", 5, None)
    # Hours up to 04:00Z are due by 05:30Z; Athenry only has 23:00Z-03:00Z.
    assert m["coverage"] == {
        "hours_expected": 23,
        "hours_reported": 5,
        "gaps": [
            {"from": "2026-09-24T06:00:00Z", "to": "2026-09-24T22:00:00Z", "hours": 17},
            {"from": "2026-09-25T04:00:00Z", "to": "2026-09-25T04:00:00Z", "hours": 1},
        ],
    }
    times = [o["observed_at"] for o in data(resp)]
    assert times == sorted(times)


def test_history_fill_returns_every_hour(client):
    url = "/v1/stations/athenry/observations?from=2026-09-24T22:00:00Z&fill=true"
    items = data(client.get(url))
    assert [o["observed_at"][11:13] for o in items] == ["22", "23", "00", "01", "02", "03", "04"]
    assert [o["flags"] for o in items] == [["missing"], [], [], [], [], [], ["missing"]]
    assert items[0]["temperature_c"] is None and items[0]["wind"]["speed_kt"] is None
    assert set(items[0]) == set(items[1])  # same shape either way


def test_history_fill_pagination(client):
    url = "/v1/stations/athenry/observations?from=2026-09-24T22:00:00Z&fill=true&limit=3"
    pages = []
    while url:
        body = client.get(url).get_json()
        pages.append([o["observed_at"][11:13] for o in body["data"]])
        url = body["meta"]["next"]
    assert pages == [["22", "23", "00"], ["01", "02", "03"], ["04"]]


@pytest.mark.parametrize("query, expected", [
    ("from=2026-09-25T00:00:00Z&to=2026-09-25T02:00:00Z", 3),       # inclusive at both ends
    ("from=2026-09-25T02:00:00%2B01:00&to=2026-09-26", 3),           # offset, date-only
    ("from=2026-09-25T02:00:00 01:00&to=2026-09-25T03:00:00", 3),     # unencoded '+'
    ("from=2026-09-20&to=2026-09-21", 0),
])
def test_history_ranges(client, query, expected):
    assert len(data(client.get(f"/v1/stations/athenry/observations?{query}"))) == expected


def test_history_pagination(client):
    url = "/v1/stations/athenry/observations?limit=2"
    seen, pages = [], 0
    while url:
        body = client.get(url).get_json()
        seen += [o["observed_at"] for o in body["data"]]
        url = body["meta"]["next"]
        pages += 1
    assert pages == 3
    assert len(seen) == len(set(seen)) == 5


@pytest.mark.parametrize("query, message", [
    ("from=2026-09-26&to=2026-09-25", "'from' must not be after 'to'"),
    ("from=yesterday", "'from' must be an ISO 8601"),
    ("limit=0", "'limit' must be between"),
    ("limit=5001", "'limit' must be between"),
    ("fill=sometimes", "'fill' must be true or false"),
    ("start=2026-09-25", "Unknown parameter 'start'"),
])
def test_history_validation(client, query, message):
    assert message in problem(client.get(f"/v1/stations/athenry/observations?{query}"), 400)


# --- Daily summaries ---------------------------------------------------------

def test_daily_summary(client):
    days = data(client.get("/v1/stations/athenry/daily?from=2026-09-24&to=2026-09-25"))
    assert [d["date"] for d in days] == ["2026-09-24", "2026-09-25"]
    empty, today = days
    assert (empty["hours_reported"], empty["hours_expected"], empty["complete"]) == (0, 24, False)
    assert empty["temperature_max_c"] is None
    # 25 Sept so far: 00:00-05:00 Irish time is due (six hours); five were reported.
    assert today == {
        "date": "2026-09-25",
        "hours_reported": 5,
        "hours_expected": 6,
        "complete": False,
        "temperature_min_c": 9,
        "temperature_max_c": 11,
        "temperature_mean_c": 10.2,
        "rainfall_total_mm": 0.2,
        "wind_speed_max_kt": 14,
        "wind_speed_max_kmh": 25.9,
        "wind_gust_max_kt": 22,
        "wind_gust_max_kmh": 40.7,
        "humidity_mean_pct": 94,
        "pressure_msl_min_hpa": 1012,
        "pressure_msl_max_hpa": 1012,
    }


def test_daily_defaults_to_last_seven_days(client):
    resp = client.get("/v1/stations/athenry/daily")
    assert [d["date"] for d in data(resp)][0] == "2026-09-19"
    assert (meta(resp)["from"], meta(resp)["to"]) == ("2026-09-19", "2026-09-25")


@pytest.mark.parametrize("day, hours", [
    ("2026-03-29", 23),   # clocks go forward
    ("2025-10-26", 25),   # clocks go back
    ("2026-09-26", 0),    # tomorrow: nothing due yet
])
def test_daily_expected_hours_follow_clock_changes(client, day, hours):
    (summary,) = data(client.get(f"/v1/stations/athenry/daily?from={day}&to={day}"))
    assert summary["hours_expected"] == hours


@pytest.mark.parametrize("query, message", [
    ("from=2026-09-26&to=2026-09-25", "'from' must not be after 'to'"),
    ("from=2024-01-01&to=2026-01-01", "at most 366 days"),
    ("from=25/09/2026", "'from' must be a date"),
])
def test_daily_validation(client, query, message):
    assert message in problem(client.get(f"/v1/stations/athenry/daily?{query}"), 400)


# --- Across stations ---------------------------------------------------------

def test_latest_everywhere(client):
    obs = data(client.get("/v1/observations/latest"))
    assert [o["station_id"] for o in obs] == ["athenry", "dublin-airport"]
    assert obs[1]["station"] == {
        "id": "dublin-airport", "name": "Dublin Airport", "county": "Dublin",
        "location": {"lat": 53.428, "lon": -6.241},
    }
    assert obs[1]["observed_at"] == "2026-09-24T19:00:00Z"


def test_latest_for_listed_stations(client):
    listed = data(client.get("/v1/observations/latest?stations=dublin-airport"))
    assert [o["station_id"] for o in listed] == ["dublin-airport"]
    # Met Éireann's IDs work too.
    listed = data(client.get("/v1/observations/latest?stations=dublin,athenry"))
    assert [o["station_id"] for o in listed] == ["athenry", "dublin-airport"]
    assert data(client.get("/v1/observations/latest?stations=cork-airport")) == []
    assert "No station 'mars'" in problem(
        client.get("/v1/observations/latest?stations=dublin-airport,mars"), 400)


def test_nearest_observation(client):
    near_dublin = "lat=53.35&lon=-6.26"
    # Dublin Airport's reading is 10.5 hours old; within 12 hours it is used.
    obs = data(client.get(f"/v1/observations/nearest?{near_dublin}&max_age_h=12"))
    assert obs["station"]["id"] == "dublin-airport"
    assert obs["station"]["distance_km"] == pytest.approx(9.3, abs=0.5)

    # With the default 3 hours only Athenry qualifies, and it is too far away.
    detail = problem(client.get(f"/v1/observations/nearest?{near_dublin}"), 404)
    assert "No station within 80 km has reported in the last 3 hours" in detail
    assert "The nearest that has is Athenry (168 km away)" in detail

    obs = data(client.get(f"/v1/observations/nearest?{near_dublin}&max_distance_km=200"))
    assert obs["station"]["id"] == "athenry"

    detail = problem(client.get(f"/v1/observations/nearest?{near_dublin}&max_age_h=1"), 404)
    assert "No station has reported in the last 1 hours" in detail


def test_health(client):
    health = data(client.get("/v1/health"))
    assert health["status"] == "degraded"
    assert (health["stations_ok"], health["stations_total"]) == (1, 25)
    by_id = {s["id"]: s for s in health["stations"]}
    assert by_id["athenry"]["status"] == "ok"
    assert by_id["athenry"]["last_ingest"]["rows_upserted"] == 5
    assert by_id["dublin-airport"]["status"] == "stale"
    assert by_id["cork-airport"] == {"id": "cork-airport", "name": "Cork Airport",
                                     "status": "no_data", "last_observed_at": None,
                                     "last_ingest": None}


def test_errors_are_problem_json(client):
    assert client.get("/v1/nope").headers["Access-Control-Allow-Origin"] == "*"
    problem(client.get("/v1/nope"), 404)
    problem(client.post("/v1/stations"), 405)
    assert "accepts: no parameters" in problem(client.get("/v1/health?verbose=1"), 400)


def test_problem_body_is_json(client):
    body = json.loads(client.get("/v1/stations/nowhere").get_data(as_text=True))
    assert set(body) == {"type", "title", "status", "detail"}
