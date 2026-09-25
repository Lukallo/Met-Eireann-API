"""Poll the observations feed for a set of stations and store what comes back."""

import json
import time
from dataclasses import dataclass

from . import db, timeutil, upstream


@dataclass
class Result:
    station_id: str
    http_status: int | None
    rows: int
    error: str | None


def ingest(conn, stations, feed, fetch, *, tz="Europe/Dublin", delay_seconds=0.0):
    """Fetch, normalise and upsert one feed for each station.

    ``fetch(slug, feed)`` returns ``(http_status, payload)``. A failure at one
    station is logged to ``ingest_runs`` and the loop moves on.
    """
    results = []
    for i, station in enumerate(stations):
        if i and delay_seconds:
            time.sleep(delay_seconds)  # be polite to the upstream server
        started = timeutil.to_iso(timeutil.utcnow())
        status, rows, error = None, 0, None
        try:
            status, payload = fetch(station["metweb_slug"], feed)
            parsed, skipped = upstream.normalise(payload, station["id"], fetched_at=started, tz=tz)
            rows = db.upsert_observations(conn, parsed)
            if skipped:
                sample = json.dumps(skipped[0], ensure_ascii=False)[:300]
                error = f"skipped {len(skipped)} record(s) with no usable date/time, e.g. {sample}"
        except upstream.UpstreamError as e:
            status, error = e.status, str(e)
        except Exception as e:  # a parser bug at one station shouldn't stop the others
            error = f"{type(e).__name__}: {e}"
        db.log_ingest_run(conn, station["id"], feed, started, status, rows, error)
        results.append(Result(station["id"], status, rows, error))
    return results
