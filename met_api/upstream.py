"""Client and normaliser for Met Éireann's observations feed.

``GET {base}/observations/{slug}/{today|yesterday}`` returns a JSON array with
one object per hour. The known quirks, handled here:

* Numbers arrive as strings (``"temperature": "10"``). Missing values arrive
  as placeholder strings such as ``"-"``.
* There is no timestamp. Each record has a ``date`` and a ``reportTime``
  (``"13:00"``) in Irish local time, with no offset.
* Wind is in knots, with a cardinal direction (``"SW"``). Degrees are only
  stored when the feed gives them; they aren't invented from the cardinal point.

The exact format hasn't been checked against a live response yet (run
``flask probe``), so parsing is deliberately lenient. Field names are matched
case-insensitively, a few date formats are accepted, and a record whose time
can't be read is skipped and reported rather than failing the whole batch.
"""

import json
import re
from datetime import datetime, time, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

from .timeutil import to_iso

FEEDS = ("today", "yesterday")
USER_AGENT = "met-eireann-api/0.1 (+https://github.com/lukallo/met-eireann-api)"

# Fields we expect on each record, lower-cased. `flask probe` reports any missing.
EXPECTED_FIELDS = (
    "name", "temperature", "symbol", "weatherdescription", "windspeed", "windgust",
    "cardinalwinddirection", "humidity", "rainfall", "pressure", "date", "reporttime",
)

_MISSING = {"", "-", "--", "n/a", "na", "null", "none", "nan"}
_NUMBER = re.compile(r"(-?\d+(?:\.\d+)?)\s*[^\d\s]*")
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})(?::\d{2})?")
_DATE_FORMATS = ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y")
COMPASS_POINTS = {"N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"}


class UpstreamError(Exception):
    """The feed couldn't be fetched or wasn't in the expected shape."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def feed_url(base_url, slug, feed):
    return f"{base_url.rstrip('/')}/observations/{quote(slug)}/{feed}"


def fetch(slug, feed="today", *, base_url, timeout=15, session=None):
    """Return ``(http_status, payload)`` for one station's feed."""
    if feed not in FEEDS:
        raise ValueError(f"feed must be one of {FEEDS}")
    url = feed_url(base_url, slug, feed)
    try:
        resp = (session or requests).get(
            url, timeout=timeout, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
    except requests.RequestException as e:
        raise UpstreamError(f"{url}: {e}") from e
    if resp.status_code != 200:
        raise UpstreamError(f"{url}: HTTP {resp.status_code}", status=resp.status_code)
    try:
        return resp.status_code, resp.json()
    except ValueError as e:
        raise UpstreamError(f"{url}: response is not JSON", status=resp.status_code) from e


# --- Normalising -------------------------------------------------------------

def _lower_keys(record):
    return {str(k).lower(): v for k, v in record.items()}


def _number(value, integer=False):
    """Parse ``"12"``, ``"0.2mm"``, ``12`` and so on. Placeholders become ``None``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = float(value)
    else:
        text = str(value).strip()
        if text.lower() in _MISSING:
            return None
        m = _NUMBER.fullmatch(text)
        if not m:
            return None
        n = float(m.group(1))
    if integer and n.is_integer():
        return int(n)
    return n


def _text(value):
    if value is None:
        return None
    text = str(value).strip().strip('"').strip()
    return None if text.lower() in _MISSING else text


def _parse_date(value):
    text = _text(value)
    if text is None:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _local_datetime(rec):
    """Naive local datetime from ``date`` + ``reportTime``, or ``None``."""
    day = _parse_date(rec.get("date"))
    m = _CLOCK.fullmatch(_text(rec.get("reporttime")) or "")
    if day is None or m is None:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if (hour, minute) == (24, 0):
        return datetime.combine(day + timedelta(days=1), time())
    if hour > 23 or minute > 59:
        return None
    return datetime.combine(day, time(hour, minute))


def _cardinal(value):
    text = _text(value)
    if text is not None and text.upper() in COMPASS_POINTS:
        return text.upper()
    return text  # e.g. "Calm"


def normalise(payload, station_id, *, fetched_at, tz="Europe/Dublin"):
    """Turn one feed response into database rows.

    Returns ``(rows, skipped)``. ``skipped`` lists the raw records that had no
    usable date and time.
    """
    if not isinstance(payload, list):
        raise UpstreamError(f"expected a JSON array, got {type(payload).__name__}")
    zone = ZoneInfo(tz)

    parsed, skipped = [], []
    for raw in payload:
        if not isinstance(raw, dict):
            skipped.append(raw)
            continue
        rec = _lower_keys(raw)
        local = _local_datetime(rec)
        if local is None:
            skipped.append(raw)
        else:
            parsed.append((raw, rec, local))

    # The night the clocks go back, 01:00 happens twice. The feed gives both as
    # "01:00", so a repeated local time is the second (later) one, going by the
    # order of the feed.
    descending = len(parsed) > 1 and parsed[0][2] > parsed[-1][2]
    ordered = reversed(parsed) if descending else parsed
    seen = set()
    rows = []
    for raw, rec, local in ordered:
        fold = 1 if local in seen else 0
        seen.add(local)
        observed = local.replace(tzinfo=zone, fold=fold)
        rows.append({
            "station_id": station_id,
            "observed_at": to_iso(observed),
            "local_date": local.date().isoformat(),
            "temperature_c": _number(rec.get("temperature")),
            "humidity_pct": _number(rec.get("humidity"), integer=True),
            "pressure_msl_hpa": _number(rec.get("pressure")),
            "rainfall_mm": _number(rec.get("rainfall")),
            "wind_speed_kt": _number(rec.get("windspeed"), integer=True),
            "wind_gust_kt": _number(rec.get("windgust"), integer=True),
            "wind_dir_deg": _number(rec.get("winddirection"), integer=True),
            "wind_dir_cardinal": _cardinal(rec.get("cardinalwinddirection")),
            "weather_symbol": _text(rec.get("symbol")),
            "weather_description": _text(rec.get("weatherdescription")) or _text(rec.get("text")),
            "raw_json": json.dumps(raw, ensure_ascii=False, sort_keys=True),
            "fetched_at": fetched_at,
        })
    rows.sort(key=lambda r: r["observed_at"])
    return rows, skipped


def missing_fields(payload):
    """Expected field names that appear in none of the records (for ``flask probe``)."""
    present = set()
    for rec in payload if isinstance(payload, list) else []:
        if isinstance(rec, dict):
            present.update(str(k).lower() for k in rec)
    return [f for f in EXPECTED_FIELDS if f not in present]


# --- Checks for `flask probe` -------------------------------------------------
# These test assumptions that affect every reading and can't be confirmed from
# the documentation alone.

def latest_local_time(payload):
    """The newest ``date`` + ``reportTime`` in a feed, as a naive local datetime."""
    times = [_local_datetime(_lower_keys(r)) for r in payload if isinstance(r, dict)]
    times = [t for t in times if t is not None]
    return max(times) if times else None


def check_timezone(latest_naive, now_utc, tz="Europe/Dublin"):
    """Are the feed's times Irish local time? Returns ``(status, message)``.

    In summer Irish time is UTC+1. A feed time later than the current UTC time
    can only be local time, so that confirms it. In winter the two are equal and
    the question doesn't matter.
    """
    zone = ZoneInfo(tz)
    now_local = now_utc.astimezone(zone)
    if latest_naive is None:
        return "unknown", "no readings to check"
    if now_local.utcoffset().total_seconds() == 0:
        return "ok", "Irish time is currently the same as UTC, so the time zone can't be wrong"
    utc_naive = now_utc.replace(tzinfo=None)
    local_naive = now_local.replace(tzinfo=None)
    shown = latest_naive.strftime("%H:%M")
    if latest_naive > local_naive + timedelta(minutes=5):
        return "warn", (f"the newest reading ({shown}) is later than the current Irish time "
                        f"({local_naive:%H:%M}); the time zone setting is probably wrong")
    if latest_naive > utc_naive:
        return "ok", (f"the newest reading ({shown}) is later than the current UTC time "
                      f"({utc_naive:%H:%M}), so the feed uses Irish local time")
    return "unknown", (f"the newest reading ({shown}) isn't later than the current UTC time "
                       f"({utc_naive:%H:%M}); run probe again early in an hour to confirm")


def rainfall_pattern(payload):
    """``"hourly"`` if rainfall ever goes down during the day, ``"rising"`` if it only
    ever goes up (it may be a running total), or ``None`` if there was no rain."""
    series = []
    for raw in payload if isinstance(payload, list) else []:
        if isinstance(raw, dict):
            rec = _lower_keys(raw)
            when, amount = _local_datetime(rec), _number(rec.get("rainfall"))
            if when is not None and amount is not None:
                series.append((when, amount))
    amounts = [a for _, a in sorted(series)]
    steps = list(zip(amounts, amounts[1:]))
    if any(b < a for a, b in steps):
        return "hourly"
    if sum(b > a for a, b in steps) >= 2:
        return "rising"
    return None

