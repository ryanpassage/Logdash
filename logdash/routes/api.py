from flask import Blueprint, jsonify

from logdash import collector
from logdash.health import compute_health

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/snapshot")
def snapshot():
    """Return the current in-memory snapshot as JSON for JS consumers."""
    snap = collector.get_snapshot()
    if snap is None:
        return jsonify({})
    result = {}
    for name, data in snap.get_all().items():
        health = compute_health(data)
        stats = data.get("stats") or {}
        jvm = stats.get("jvm") or {}
        mem = jvm.get("mem") or {}
        heap_used = mem.get("heap_used_in_bytes") or 0
        heap_max = mem.get("heap_max_in_bytes") or 1
        result[name] = {
            "reachable": data.get("reachable"),
            "last_seen": data.get("last_seen"),
            "health_status": health["status"],
            "health_reasons": health["reasons"],
            "version": (stats.get("version") or data.get("info", {}).get("version")),
            "events_in": data.get("events_in", 0.0),
            "events_out": data.get("events_out", 0.0),
            "heap_pct": int((heap_used / heap_max) * 100) if heap_max else 0,
            "pipeline_count": len(stats.get("pipelines") or {}),
        }
    return jsonify(result)
