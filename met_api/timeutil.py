"""Timestamp helpers. Stored and returned times are UTC ``YYYY-MM-DDTHH:MM:SSZ``."""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IRISH_TZ = ZoneInfo("Europe/Dublin")
ISO_Z = "%Y-%m-%dT%H:%M:%SZ"


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
