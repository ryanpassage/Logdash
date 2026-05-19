import logging
import os

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify

from config import Config
from logdash import collector
from logdash.routes import api as api_routes
from logdash.routes import dashboard as dashboard_routes
from logdash.routes import server as server_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def _format_uptime(ms: int) -> str:
    if not ms:
        return "—"
    secs = int(ms / 1000)
    days, secs = divmod(secs, 86400)
    hours, secs = divmod(secs, 3600)
    mins = secs // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _format_rate(r: float) -> str:
    r = float(r or 0)
    if r >= 1_000_000:
        return f"{r / 1_000_000:.1f}M"
    if r >= 1_000:
        return f"{r / 1_000:.1f}K"
    return f"{r:.0f}"


def _format_bytes(b: int) -> str:
    b = int(b or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}" if unit != "B" else f"{b} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def create_app() -> Flask:
    app = Flask(__name__)

    app.jinja_env.globals.update(
        format_uptime=_format_uptime,
        format_rate=_format_rate,
        format_bytes=_format_bytes,
    )

    app.register_blueprint(dashboard_routes.bp)
    app.register_blueprint(api_routes.bp)
    app.register_blueprint(server_routes.bp)

    @app.route("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    # Start the background collector once per process.
    # The WERKZEUG_RUN_MAIN guard prevents a double-start when Flask's reloader
    # is active in development (the reloader forks a child process where the
    # actual server runs; we only want the scheduler in that child).
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        storage = None
        if Config.STORAGE_CONNECTION_STRING:
            from logdash.storage import StorageAdapter
            storage = StorageAdapter(Config.STORAGE_CONNECTION_STRING)
            logging.getLogger(__name__).info("Azure Table Storage enabled")
        else:
            logging.getLogger(__name__).warning(
                "AZURE_STORAGE_CONNECTION_STRING not set — running without persistence"
            )
        app.config["storage"] = storage
        collector.start(
            Config.SERVERS,
            Config.POLL_INTERVAL,
            storage=storage,
            sample_interval=Config.SAMPLE_INTERVAL,
        )

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=Config.PORT, debug=True)
