"""Query-string handling. Every problem becomes a 400 with a message saying what to send."""

import functools
import math
from datetime import date

from flask import abort, request

from .stations import TYPES
from .timeutil import parse_query_time


def accepts(*names):
    """Reject parameters the endpoint doesn't know, so a typo isn't silently ignored."""
    def decorator(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            unknown = sorted(set(request.args) - set(names))
            if unknown:
                allowed = ", ".join(names) if names else "no parameters"
                abort(400, f"Unknown parameter {', '.join(repr(u) for u in unknown)}. "
                           f"This endpoint accepts: {allowed}.")
            return view(*args, **kwargs)
        wrapper.accepted_params = names
        return wrapper
    return decorator


def float_arg(name, low, high, default=None, required=False):
    raw = request.args.get(name)
    if raw is None:
        if required:
            abort(400, f"'{name}' is required.")
        return default
    try:
        value = float(raw)
    except ValueError:
        abort(400, f"'{name}' must be a number.")
    if not math.isfinite(value) or not low <= value <= high:
        abort(400, f"'{name}' must be between {low} and {high}.")
    return value


def int_arg(name, default, low, high):
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        abort(400, f"'{name}' must be a whole number.")
    if not low <= value <= high:
        abort(400, f"'{name}' must be between {low} and {high}.")
    return value


def bool_arg(name, default=False):
    raw = request.args.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in ("true", "1", "yes"):
        return True
    if value in ("false", "0", "no"):
        return False
    abort(400, f"'{name}' must be true or false.")


def time_arg(name):
    raw = request.args.get(name)
    if raw is None:
        return None
    try:
        return parse_query_time(raw)
    except ValueError:
        abort(400, f"'{name}' must be an ISO 8601 date or datetime, e.g. 2026-09-25T12:00:00Z.")


def date_arg(name):
    raw = request.args.get(name)
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        abort(400, f"'{name}' must be a date, e.g. 2026-09-25.")


def type_arg():
    value = request.args.get("type")
    if value is not None and value not in TYPES:
        abort(400, f"'type' must be one of: {', '.join(TYPES)}.")
    return value


def bbox_arg():
    raw = request.args.get("bbox")
    if raw is None:
        return None
    try:
        min_lon, min_lat, max_lon, max_lat = (float(p) for p in raw.split(","))
    except ValueError:
        abort(400, "'bbox' must be min_lon,min_lat,max_lon,max_lat.")
    if min_lon > max_lon or min_lat > max_lat:
        abort(400, "'bbox' minimums must not exceed its maximums.")
    return min_lon, min_lat, max_lon, max_lat


def include_raw():
    value = request.args.get("include")
    if value is not None and value != "raw":
        abort(400, "'include' only accepts 'raw'.")
    return value == "raw"
