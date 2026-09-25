import pytest
import requests

from met_api import upstream
from met_api.upstream import UpstreamError, normalise

from .conftest import load_fixture

FETCHED = "2026-09-25T05:10:00Z"


def rec(date, time, **fields):
    return {"date": date, "reportTime": time, **fields}


def rows_for(payload, **kw):
    rows, skipped = normalise(payload, "athenry", fetched_at=FETCHED, **kw)
    return rows, skipped


def test_synthetic_feed_parses_cleanly():
    payload = load_fixture("synthetic_athenry_today.json")
    rows, skipped = rows_for(payload)
    assert skipped == []
    assert upstream.missing_fields(payload) == []
    assert [r["observed_at"] for r in rows] == [
        "2026-09-24T23:00:00Z",  # 00:00 Irish summer time is 23:00 UTC the day before
        "2026-09-25T00:00:00Z",
        "2026-09-25T01:00:00Z",
        "2026-09-25T02:00:00Z",
        "2026-09-25T03:00:00Z",
    ]
    assert {r["local_date"] for r in rows} == {"2026-09-25"}


def test_strings_become_typed_values():
    first = rows_for(load_fixture("synthetic_athenry_today.json"))[0][0]
    assert first["temperature_c"] == 11.0
    assert first["humidity_pct"] == 93 and isinstance(first["humidity_pct"], int)
    assert first["pressure_msl_hpa"] == 1012.0
    assert first["rainfall_mm"] == 0.0
    assert first["wind_speed_kt"] == 9
    assert first["wind_gust_kt"] is None  # "-"
    assert first["wind_dir_deg"] == 225
    assert first["wind_dir_cardinal"] == "SW"
    assert first["weather_symbol"] == "04n"
    assert first["weather_description"] == "Cloudy"
    assert first["fetched_at"] == FETCHED
    assert '"reportTime": "00:00"' in first["raw_json"]


def test_placeholders_and_wind_edge_cases():
    rows = rows_for(load_fixture("synthetic_athenry_today.json"))[0]
    assert rows[1]["wind_gust_kt"] == 22
    assert rows[2]["pressure_msl_hpa"] is None   # "n/a"
    assert rows[2]["rainfall_mm"] == 0.2
    assert rows[3]["wind_dir_deg"] is None       # "" with a "Calm" cardinal
    assert rows[3]["wind_dir_cardinal"] == "Calm"
    assert rows[4]["wind_dir_deg"] is None       # not invented from "NNE"
    assert rows[4]["wind_dir_cardinal"] == "NNE"
    assert rows[4]["humidity_pct"] is None       # "-"
    assert rows[4]["rainfall_mm"] is None        # ""


@pytest.mark.parametrize("value, expected", [
    ("12", 12.0), (" 7.5 ", 7.5), ("-3", -3.0), ("0.2mm", 0.2), ("1012 hPa", 1012.0),
    (8, 8.0), (None, None), ("", None), ("-", None), ("N/A", None), ("Tr", None), (True, None),
])
def test_number_parsing(value, expected):
    assert upstream._number(value) == expected


def test_winter_time_is_utc():
    rows, _ = rows_for([rec("15-01-2026", "12:00", temperature="5")])
    assert rows[0]["observed_at"] == "2026-01-15T12:00:00Z"


def test_clocks_going_back_gives_two_distinct_hours():
    # 25 Oct 2026: 01:00 happens twice (01:00 IST = 00:00Z, then 01:00 GMT = 01:00Z).
    times = ["00:00", "01:00", "01:00", "02:00"]
    expected = ["2026-10-24T23:00:00Z", "2026-10-25T00:00:00Z",
                "2026-10-25T01:00:00Z", "2026-10-25T02:00:00Z"]
    payload = [rec("25-10-2026", t, temperature=str(i)) for i, t in enumerate(times)]

    rows, _ = rows_for(payload)
    assert [r["observed_at"] for r in rows] == expected
    assert [r["temperature_c"] for r in rows] == [0, 1, 2, 3]

    # Same result if the feed lists the newest hour first.
    rows, _ = rows_for(list(reversed(payload)))
    assert [r["observed_at"] for r in rows] == expected
    assert [r["temperature_c"] for r in rows] == [0, 1, 2, 3]


def test_alternative_formats():
    rows, skipped = rows_for([
        rec("2026-09-25", "13:00:00"),            # ISO date, seconds on the time
        {"DATE": "25/09/2026", "REPORTTIME": "14:00", "WindSpeed": "4"},  # other casing
        rec("25-09-2026", "24:00"),               # end-of-day written as 24:00
    ])
    assert skipped == []
    assert [r["observed_at"] for r in rows] == [
        "2026-09-25T12:00:00Z", "2026-09-25T13:00:00Z", "2026-09-25T23:00:00Z"]
    assert rows[1]["wind_speed_kt"] == 4
    assert rows[2]["local_date"] == "2026-09-26"


def test_records_without_a_usable_time_are_skipped_not_fatal():
    good = rec("25-09-2026", "10:00")
    bad = [rec("25-09-2026", "-"), rec("", "10:00"), rec("31-02-2026", "10:00"),
           rec("25-09-2026", "25:00"), "not a dict"]
    rows, skipped = rows_for([good, *bad])
    assert len(rows) == 1
    assert skipped == bad


def test_non_list_payload_is_an_error():
    with pytest.raises(UpstreamError):
        rows_for({"error": "unknown station"})


def test_empty_feed_after_midnight():
    assert rows_for([]) == ([], [])


def test_missing_fields_report():
    assert "windgust" in upstream.missing_fields([{"temperature": "1"}])


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


def test_fetch_builds_url_and_returns_payload():
    session = FakeSession(FakeResponse(200, [{"a": 1}]))
    status, payload = upstream.fetch("oak-park", "yesterday", base_url="https://x.test/",
                                     timeout=5, session=session)
    assert (status, payload) == (200, [{"a": 1}])
    url, kwargs = session.calls[0]
    assert url == "https://x.test/observations/oak-park/yesterday"
    assert kwargs["timeout"] == 5 and "User-Agent" in kwargs["headers"]


@pytest.mark.parametrize("session, status", [
    (FakeSession(FakeResponse(404, None)), 404),
    (FakeSession(FakeResponse(200, ValueError("bad json"))), 200),
    (FakeSession(error=requests.ConnectionError("down")), None),
])
def test_fetch_errors(session, status):
    with pytest.raises(UpstreamError) as info:
        upstream.fetch("athenry", base_url="https://x.test", session=session)
    assert info.value.status == status


def test_fetch_rejects_unknown_feed():
    with pytest.raises(ValueError):
        upstream.fetch("athenry", "tomorrow", base_url="https://x.test")


# --- probe checks ------------------------------------------------------------

from datetime import datetime, timezone  # noqa: E402


@pytest.mark.parametrize("latest, now, status", [
    ("2026-09-25 14:00", "2026-09-25T13:20:00", "ok"),       # later than UTC: must be local time
    ("2026-09-25 13:00", "2026-09-25T13:20:00", "unknown"),  # could be either
    ("2026-09-25 15:00", "2026-09-25T13:20:00", "warn"),     # later than Irish time: wrong zone
    ("2026-01-15 13:00", "2026-01-15T13:20:00", "ok"),       # winter: Irish time is UTC
    (None, "2026-09-25T13:20:00", "unknown"),
])
def test_check_timezone(latest, now, status):
    latest_dt = datetime.fromisoformat(latest) if latest else None
    now_dt = datetime.fromisoformat(now).replace(tzinfo=timezone.utc)
    assert upstream.check_timezone(latest_dt, now_dt)[0] == status


@pytest.mark.parametrize("amounts, pattern", [
    (["0.0", "0.4", "0.1", "0.0"], "hourly"),
    (["0.0", "0.2", "0.5", "0.9"], "rising"),
    (["0.0", "0.0", "-", "0.0"], None),
])
def test_rainfall_pattern(amounts, pattern):
    payload = [rec("25-09-2026", f"{h:02d}:00", rainfall=a) for h, a in enumerate(amounts)]
    assert upstream.rainfall_pattern(payload) == pattern


def test_latest_local_time():
    payload = load_fixture("synthetic_athenry_today.json")
    assert upstream.latest_local_time(payload) == datetime(2026, 9, 25, 4, 0)
