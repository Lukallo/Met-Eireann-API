import pytest


def data(resp, status=200):
    assert resp.status_code == status, resp.get_json()
    return resp.get_json()["data"]


def problem(resp, status):
    assert resp.status_code == status
    assert resp.content_type == "application/problem+json"
    body = resp.get_json()
    assert body["status"] == status
    return body["detail"]


def test_index_and_envelope(client):
    resp = client.get("/v1/")
    body = resp.get_json()
    assert "/v1/stations" in body["data"]["endpoints"]
    assert body["meta"]["generated_at"] == "2026-09-25T05:30:00Z"
    assert "Met Éireann" in body["meta"]["attribution"]
    assert resp.headers["Access-Control-Allow-Origin"] == "*"


# --- Stations ----------------------------------------------------------------

def test_list_stations(client):
    body = client.get("/v1/stations").get_json()
    assert body["meta"]["count"] == 103
    athenry = next(s for s in body["data"] if s["id"] == "athenry")
    assert athenry == {
        "id": "athenry",
        "name": "Athenry",
        "type": "synoptic",
        "location": {"lat": 53.289167, "lon": -8.785556, "elevation_m": None},
        "county": "Galway",
        "feeds": ["observations"],
        "upstream": {"metweb_slug": "athenry", "station_number": None},
    }


@pytest.mark.parametrize("query, expected", [
    ("type=synoptic", 25),
    ("type=automatic", 78),
    ("county=CORK", 4),
    ("bbox=-6.5,53.2,-6.0,53.5", 5),  # Dublin area: 3 synoptic, 2 automatic
])
def test_station_filters(client, query, expected):
    assert len(data(client.get(f"/v1/stations?{query}"))) == expected


@pytest.mark.parametrize("query, message", [
    ("type=radar", "'type' must be one of"),
    ("bbox=1,2,3", "'bbox' must be"),
    ("bbox=-6,53,-7,54", "minimums"),
])
def test_station_filter_errors(client, query, message):
    assert message in problem(client.get(f"/v1/stations?{query}"), 400)


def test_nearest_stations(client):
    body = client.get("/v1/stations/nearest?lat=52.7038&lon=-8.8642&type=synoptic").get_json()
    ids = [s["id"] for s in body["data"]]
    assert ids[0] == "shannon" and len(ids) == 3
    assert body["data"][0]["distance_km"] == pytest.approx(4.1, abs=0.2)
    assert body["meta"]["query"] == {"lat": 52.7038, "lon": -8.8642}

    everything = data(client.get("/v1/stations/nearest?lat=54.0769&lon=-7.6117&limit=1"))
    assert [s["id"] for s in everything] == ["ballinamore"]


@pytest.mark.parametrize("query, message", [
    ("lon=-8", "'lat' is required"),
    ("lat=abc&lon=-8", "'lat' must be a number"),
    ("lat=100&lon=-8", "'lat' must be between"),
    ("lat=nan&lon=-8", "'lat' must be between"),
    ("lat=53&lon=-8&limit=0", "'limit' must be between"),
    ("lat=53&lon=-8&limit=two", "'limit' must be a whole number"),
])
def test_nearest_stations_validation(client, query, message):
    assert message in problem(client.get(f"/v1/stations/nearest?{query}"), 400)


def test_station_detail_embeds_latest(client):
    station = data(client.get("/v1/stations/athenry"))
    assert station["latest_observation"]["observed_at"] == "2026-09-25T03:00:00Z"
    assert data(client.get("/v1/stations/cork"))["latest_observation"] is None


def test_unknown_station_is_404(client):
    assert "No station with id 'nowhere'" in problem(client.get("/v1/stations/nowhere"), 404)
    problem(client.get("/v1/stations/nowhere/observations"), 404)


# --- Observations ------------------------------------------------------------

def test_station_latest(client):
    obs = data(client.get("/v1/stations/athenry/observations/latest"))
    assert obs == {
        "station_id": "athenry",
        "observed_at": "2026-09-25T03:00:00Z",
        "local_time": "2026-09-25T04:00:00+01:00",
        "temperature_c": 9.0,
        "humidity_pct": None,
        "pressure_hpa": 1012.0,
        "rainfall_mm": None,
        "wind": {
            "speed_kt": 9, "speed_kmh": 16.7, "gust_kt": None, "gust_kmh": None,
            "direction_deg": 22.5, "direction_cardinal": "NNE",
        },
        "weather": {"symbol": "03n", "description": "Partly cloudy"},
    }


def test_include_raw(client):
    obs = data(client.get("/v1/stations/athenry/observations/latest?include=raw"))
    assert obs["raw"]["reportTime"] == "04:00"


def test_station_latest_without_data(client):
    detail = problem(client.get("/v1/stations/glenveagh-national-park/observations/latest"), 404)
    assert "No observations stored" in detail


def test_history_defaults_to_last_24_hours(client):
    body = client.get("/v1/stations/athenry/observations").get_json()
    assert body["meta"]["from"] == "2026-09-24T05:30:00Z"
    assert body["meta"]["to"] == "2026-09-25T05:30:00Z"
    assert body["meta"]["count"] == 5 and body["meta"]["next"] is None
    times = [o["observed_at"] for o in body["data"]]
    assert times == sorted(times)


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
    seen = []
    pages = 0
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
])
def test_history_validation(client, query, message):
    assert message in problem(client.get(f"/v1/stations/athenry/observations?{query}"), 400)


def test_latest_everywhere(client):
    obs = data(client.get("/v1/observations/latest"))
    assert [o["station_id"] for o in obs] == ["athenry", "dublin"]
    assert obs[1]["station"] == {
        "id": "dublin", "name": "Dublin Airport", "type": "synoptic",
        "location": {"lat": 53.428, "lon": -6.241},
    }
    assert obs[1]["observed_at"] == "2026-09-24T19:00:00Z"


def test_latest_for_listed_stations(client):
    assert [o["station_id"] for o in data(client.get("/v1/observations/latest?stations=dublin"))] \
        == ["dublin"]
    assert data(client.get("/v1/observations/latest?stations=cork")) == []
    assert "Unknown station id(s): mars" in problem(
        client.get("/v1/observations/latest?stations=dublin,mars"), 400)


def test_nearest_observation_skips_stations_that_have_gone_quiet(client):
    near_dublin = "lat=53.35&lon=-6.26"
    # Dublin's last reading is 10.5 hours old, so with the default 3 h limit Athenry wins.
    obs = data(client.get(f"/v1/observations/nearest?{near_dublin}"))
    assert obs["station"]["id"] == "athenry"
    assert obs["station"]["distance_km"] == pytest.approx(170, abs=10)

    obs = data(client.get(f"/v1/observations/nearest?{near_dublin}&max_age_h=12"))
    assert obs["station"]["id"] == "dublin"

    detail = problem(client.get(f"/v1/observations/nearest?{near_dublin}&max_age_h=1"), 404)
    assert "No station has reported in the last 1 hours" in detail


def test_health(client):
    health = data(client.get("/v1/health"))
    assert health["status"] == "degraded"
    assert (health["stations_ok"], health["stations_total"]) == (1, 25)
    by_id = {s["id"]: s for s in health["stations"]}
    assert by_id["athenry"]["status"] == "ok"
    assert by_id["athenry"]["last_ingest"]["rows_upserted"] == 5
    assert by_id["dublin"]["status"] == "stale"
    assert by_id["cork"] == {"id": "cork", "name": "Cork Airport", "status": "no_data",
                             "last_observed_at": None, "last_ingest": None}


def test_errors_are_problem_json(client):
    problem(client.get("/v1/nope"), 404)
    problem(client.post("/v1/stations"), 405)
