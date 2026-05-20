from flask import Blueprint, current_app, jsonify, request

from logdash import collector
from logdash.health import compute_health

bp = Blueprint("api", __name__, url_prefix="/api")

_RANGE_MINUTES = {"1h": 60, "6h": 360, "24h": 1440}
_HOURLY_RANGES = {"7d": 168, "30d": 720}  # hours


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


@bp.route("/server/<name>/series")
def server_series(name):
    metric = request.args.get("metric", "events")
    range_str = request.args.get("range", "1h")
    pipeline_id = request.args.get("pipeline", "")

    storage = current_app.config.get("storage")
    if not storage:
        return jsonify([])

    if range_str in _HOURLY_RANGES:
        hours = _HOURLY_RANGES[range_str]
        rows = _query_hourly(storage, name, metric, pipeline_id, hours)
    elif range_str in _RANGE_MINUTES:
        minutes = _RANGE_MINUTES[range_str]
        rows = _query_samples(storage, name, metric, pipeline_id, minutes)
    else:
        return jsonify([])

    # Storage returns newest-first; reverse so charts read left-to-right (oldest → newest)
    rows.reverse()
    return jsonify(rows)


def _query_samples(storage, server, metric, pipeline_id, minutes):
    if metric == "events":
        return storage.query_event_samples(server, minutes)
    if metric == "jvm":
        return storage.query_jvm_samples(server, minutes)
    if metric == "pipeline" and pipeline_id:
        return storage.query_pipeline_samples(server, pipeline_id, minutes)
    return []


def _query_hourly(storage, server, metric, pipeline_id, hours):
    """Query hourly rollups and normalize field names to match the chart's expectations."""
    if metric == "events":
        rows = storage.query_hourly_events(server, hours)
        for r in rows:
            r["events_in"]       = r.pop("events_in_delta",       0)
            r["events_out"]      = r.pop("events_out_delta",      0)
            r["events_filtered"] = r.pop("events_filtered_delta", 0)
        return rows
    if metric == "jvm":
        rows = storage.query_hourly_jvm(server, hours)
        for r in rows:
            r["heap_used_bytes"] = r.pop("heap_used_avg", 0)
            r["heap_max_bytes"]  = r.pop("heap_max_avg",  0)
        return rows
    if metric == "pipeline" and pipeline_id:
        rows = storage.query_hourly_pipeline(server, pipeline_id, hours)
        for r in rows:
            r["events_in"]  = r.pop("events_in_delta",  0)
            r["events_out"] = r.pop("events_out_delta", 0)
        return rows
    return []
