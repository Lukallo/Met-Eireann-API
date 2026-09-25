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
    """Registry stations with a live feed, optionally narrowed to ``station_ids``."""
    polled = [s for s in stations.load_csv() if s["metweb_slug"]]
    if not station_ids:
        return polled
    known = {s["id"] for s in polled}
    unknown = sorted(set(station_ids) - known)
    if unknown:
        raise click.BadParameter(
            f"no live feed for: {', '.join(unknown)}", param_hint="--station"
        )
    return [s for s in polled if s["id"] in station_ids]


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
        if example:
            raw = json.loads(example["raw_json"])
            row = {k: v for k, v in example.items() if k != "raw_json"}
            click.echo("\nEarliest raw record:\n" + json.dumps(raw, indent=2, ensure_ascii=False))
            click.echo("Normalised as:\n" + json.dumps(row, indent=2, ensure_ascii=False))
