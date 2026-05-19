from flask import Blueprint, render_template

from logdash import collector
from logdash.health import compute_health

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def index():
    servers = _build_server_list()
    return render_template("dashboard.html", servers=servers)


@bp.route("/_partial/server-cards")
def server_cards_partial():
    servers = _build_server_list()
    return render_template("partials/server_cards.html", servers=servers)


def _build_server_list() -> list[dict]:
    snap = collector.get_snapshot()
    if snap is None:
        return []
    result = []
    for name, data in snap.get_all().items():
        result.append(_enrich(name, data))
    result.sort(key=lambda s: s["name"])
    return result


def _enrich(name: str, data: dict) -> dict:
    stats = data.get("stats") or {}
    info = data.get("info") or {}
    health = compute_health(data)

    jvm = stats.get("jvm") or {}
    mem = jvm.get("mem") or {}
    heap_used = mem.get("heap_used_in_bytes") or 0
    heap_max = mem.get("heap_max_in_bytes") or 1
    heap_pct = int((heap_used / heap_max) * 100) if heap_max else 0

    pipelines = stats.get("pipelines") or {}
    pipeline_count = len(pipelines)
    reload_failures = sum(
        1 for p in pipelines.values()
        if (p or {}).get("reloads", {}).get("failures", 0) > 0
    )

    version = stats.get("version") or info.get("version") or ""
    hostname = stats.get("hostname") or info.get("host") or ""
    uptime_ms = jvm.get("uptime_in_millis") or 0
    cpu_pct = (stats.get("process") or {}).get("cpu", {}).get("percent") or 0

    return {
        "name": name,
        "health": health,
        "reachable": data.get("reachable"),
        "last_seen": data.get("last_seen"),
        "version": version,
        "hostname": hostname,
        "heap_pct": heap_pct,
        "uptime_ms": uptime_ms,
        "pipeline_count": pipeline_count,
        "reload_failures": reload_failures,
        "events_in": data.get("events_in", 0.0),
        "events_out": data.get("events_out", 0.0),
        "events_filtered": data.get("events_filtered", 0.0),
        "cpu_pct": cpu_pct,
    }
