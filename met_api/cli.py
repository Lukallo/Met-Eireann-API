"""``flask init-db``, ``flask ingest`` and ``flask probe``."""

import functools
import json
import os
import sys
from pathlib import Path

import click
import requests
from flask import current_app

from . import db, stations, timeutil, upstream
from .ingest import ingest


def _open_db():
    """Connect, create any missing tables, and sync the station registry."""
    path = current_app.config["DATABASE"]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = db.connect(path)
    db.init_db(conn)
    db.sync_stations(conn, stations.load_csv())
    return conn


def _fetcher():
    cfg = current_app.config
    return functools.partial(
        upstream.fetch,
        base_url=cfg["METWEB_BASE_URL"],
        timeout=cfg["METWEB_TIMEOUT"],
        session=requests.Session(),
    )


def _polled_stations(station_ids):
    """Registry stations with a live feed, optionally narrowed to ``station_ids``.

    Met Éireann's IDs (``dublin``) work as well as ours (``dublin-airport``).
    """
    polled = [s for s in stations.load_csv() if s["metweb_slug"]]
    if not station_ids:
        return polled
    lookup = {**{s["metweb_slug"]: s for s in polled}, **{s["id"]: s for s in polled}}
    unknown = sorted(set(station_ids) - set(lookup))
    if unknown:
        raise click.BadParameter(
            f"no live feed for: {', '.join(unknown)}", param_hint="--station"
        )
    chosen = {lookup[i]["id"] for i in station_ids}
    return [s for s in polled if s["id"] in chosen]


station_option = click.option(
    "--station", "station_ids", multiple=True, metavar="ID",
    help="Only this station (repeatable). Defaults to every station with a feed.",
)


def register(app):
    @app.cli.command("init-db")
    def init_db_command():
        """Create the tables and load the station registry."""
        conn = _open_db()
        count = conn.execute("SELECT COUNT(*) FROM stations WHERE active = 1").fetchone()[0]
        conn.close()
        click.echo(f"Database ready at {current_app.config['DATABASE']} ({count} stations).")

    @app.cli.command("ingest")
    @click.option("--feed", type=click.Choice(upstream.FEEDS), default="today", show_default=True)
    @station_option
    def ingest_command(feed, station_ids):
        """Fetch the latest observations and store them."""
        selected = _polled_stations(station_ids)
        conn = _open_db()
        try:
            results = ingest(
                conn, selected, feed, _fetcher(),
                tz=current_app.config["METWEB_TIMEZONE"],
                delay_seconds=current_app.config["INGEST_DELAY_SECONDS"],
            )
        finally:
            conn.close()
        failed = 0
        for r in results:
            status = "ok" if r.error is None else "WARN" if r.rows else "FAIL"
            failed += status == "FAIL"
            line = f"{status:4} {r.station_id:18} http={r.http_status} rows={r.rows}"
            click.echo(line + (f"  {r.error}" if r.error else ""))
        click.echo(f"{feed}: {len(results) - failed}/{len(results)} stations stored data.")
        if results and failed == len(results):
            sys.exit(1)

    @app.cli.command("probe")
    @click.option("--save", type=click.Path(file_okay=False, path_type=Path),
                  help="Save each raw response here as <slug>-<feed>.json (for test fixtures).")
    @station_option
    def probe_command(save, station_ids):
        """Check every station's feed and report how well it parses. Writes nothing to the database."""
        fetch = _fetcher()
        tz = current_app.config["METWEB_TIMEZONE"]
        if save:
            save.mkdir(parents=True, exist_ok=True)
        example = None
        newest = None          # newest reading time across the "today" feeds
        rain = {"hourly": 0, "rising": 0}
        wind_example = None
        for station in _polled_stations(station_ids):
            for feed in upstream.FEEDS:
                label = f"{station['metweb_slug']}/{feed}"
                try:
                    status, payload = fetch(station["metweb_slug"], feed)
                except upstream.UpstreamError as e:
                    click.echo(f"FAIL {label:28} {e}")
                    continue
                if save:
                    (save / f"{station['metweb_slug']}-{feed}.json").write_text(
                        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                    )
                try:
                    fetched_at = timeutil.to_iso(timeutil.utcnow())
                    rows, skipped = upstream.normalise(
                        payload, station["id"], fetched_at=fetched_at, tz=tz
                    )
                except upstream.UpstreamError as e:
                    click.echo(f"FAIL {label:28} http={status} {e}")
                    continue
                missing = upstream.missing_fields(payload)
                ok = not skipped and not missing and rows
                click.echo(
                    f"{'ok' if ok else 'WARN':4} {label:28} http={status} records={len(payload)}"
                    f" parsed={len(rows)} skipped={len(skipped)}"
                    + (f" missing_fields={','.join(missing)}" if missing else "")
                )
                if example is None and rows:
                    example = rows[0]
                if feed == "today":
                    latest = upstream.latest_local_time(payload)
                    if latest is not None and (newest is None or latest > newest):
                        newest = latest
                pattern = upstream.rainfall_pattern(payload)
                if pattern:
                    rain[pattern] += 1
                if wind_example is None:
                    wind_example = next((r for r in reversed(rows) if r["wind_speed_kt"]), None)
        if example:
            raw = json.loads(example["raw_json"])
            row = {k: v for k, v in example.items() if k != "raw_json"}
            click.echo("\nEarliest raw record:\n" + json.dumps(raw, indent=2, ensure_ascii=False))
            click.echo("Normalised as:\n" + json.dumps(row, indent=2, ensure_ascii=False))

        click.echo("\nChecks:")
        status, message = upstream.check_timezone(newest, timeutil.utcnow(), tz)
        click.echo(f"  {status:7} times: {message}")
        if rain["hourly"]:
            click.echo(f"  ok      rainfall: went down during the day in {rain['hourly']} feed(s), "
                       "so it is the amount per hour, not a running total")
        elif rain["rising"]:
            click.echo(f"  warn    rainfall: only ever rose during the day in {rain['rising']} feed(s); "
                       "it may be a running total, which would make daily totals wrong")
        else:
            click.echo("  unknown rainfall: no rain in any feed, so per-hour vs running total "
                       "can't be told yet")
        if wind_example:
            local = timeutil.from_iso(wind_example["observed_at"]).astimezone(timeutil.IRISH_TZ)
            kt = wind_example["wind_speed_kt"]
            click.echo(f"  check   wind units: {wind_example['station_id']} at {local:%H:%M} reported "
                       f"{kt} (read as knots = {round(kt * 1.852)} km/h). Compare with the same "
                       "hour on met.ie's observations page")
