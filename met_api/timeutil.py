"""Timestamp helpers. Stored and returned times are UTC ``YYYY-MM-DDTHH:MM:SSZ``."""

import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

IRISH_TZ = ZoneInfo("Europe/Dublin")
ISO_Z = "%Y-%m-%dT%H:%M:%SZ"
HOUR = timedelta(hours=1)

# How long after the hour a reading is expected to have been published and stored.
READING_DELAY = timedelta(hours=1)


def utcnow():
    """Current time in UTC. Tests monkeypatch this."""
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_iso(dt):
    return dt.astimezone(timezone.utc).strftime(ISO_Z)


def from_iso(text):
    """Parse a stored ``...Z`` timestamp back into an aware datetime."""
    return datetime.strptime(text, ISO_Z).replace(tzinfo=timezone.utc)


def parse_query_time(text):
    """Parse an ISO 8601 date or datetime from a query string into aware UTC.

    Values without an offset are taken as UTC. An unencoded ``+`` in a URL
    arrives as a space, so ``12:00:00 01:00`` is read as ``12:00:00+01:00``.
    Raises ``ValueError`` if the text isn't a valid date or datetime.
    """
    text = re.sub(r"(T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?) (\d{2}:?\d{2})$", r"\1+\2", text.strip())
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def floor_hour(dt):
    return dt.replace(minute=0, second=0, microsecond=0)


def hour_slots(start, end):
    """Every whole hour from ``start`` to ``end`` inclusive, as ``...Z`` strings."""
    slot = floor_hour(start)
    if slot < start:
        slot += HOUR
    slots = []
    while slot <= end:
        slots.append(to_iso(slot))
        slot += HOUR
    return slots


def due_by(now):
    """The latest hour whose reading should be stored by ``now``."""
    return floor_hour(now - READING_DELAY)


def irish_date(dt):
    return dt.astimezone(IRISH_TZ).date()


def irish_day_bounds(day):
    """UTC start (inclusive) and end (exclusive) of an Irish calendar day.

    The day is 23 or 25 hours long when the clocks change.
    """
    start = datetime.combine(day, time(), IRISH_TZ)
    end = datetime.combine(day + timedelta(days=1), time(), IRISH_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)
