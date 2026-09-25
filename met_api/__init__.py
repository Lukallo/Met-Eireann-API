"""A clean JSON API over Met Éireann's open weather observations."""

import os

from flask import Flask

from . import api, cli, db, openapi


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True, static_folder=None)
    app.config.from_mapping(
        DATABASE=os.path.join(app.instance_path, "met.sqlite3"),
        METWEB_BASE_URL="https://prodapi.metweb.ie",
        METWEB_TIMEOUT=15,
        METWEB_TIMEZONE="Europe/Dublin",  # zone the feed's local times are in
        INGEST_DELAY_SECONDS=1.0,
        STALE_AFTER_HOURS=3,
        NEAREST_MAX_DISTANCE_KM=80,  # everywhere in Ireland is within 80 km of a main station
    )
    # Environment overrides, e.g. MET_API_DATABASE=/var/lib/met-api/met.sqlite3
    app.config.from_prefixed_env("MET_API")
    if test_config:
        app.config.update(test_config)

    app.json.sort_keys = False
    app.json.ensure_ascii = False
    app.teardown_appcontext(db.close_db)
    app.register_blueprint(api.bp)
    app.register_blueprint(openapi.docs_bp)
    api.register_error_handlers(app)
    cli.register(app)

    @app.after_request
    def allow_cross_origin(response):
        # Read-only public data, so any web page or API tool may call it, errors included.
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    return app
