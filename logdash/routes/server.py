from flask import Blueprint, abort, render_template

from config import Config
from logdash import collector
from logdash.health import compute_health
from logdash.logstash_client import LogstashClient

bp = Blueprint("server", __name__)


@bp.route("/server/<name>")
def server_detail(name):
    snap = collector.get_snapshot()
    if snap is None:
        abort(404)
    data = snap.get(name)
    # Reject unknown servers (no entry was pre-populated)
    if data.get("reachable") is None and not data.get("last_seen"):
        abort(404)
    return render_template("server.html", server=_build_server(name, data))


@bp.route("/server/<name>/hot-threads")
def hot_threads(name):
    server_cfg = next((s for s in Config.SERVERS if s["name"] == name), None)
    if server_cfg is None:
        abort(404)
    client = LogstashClient(name, server_cfg["url"], timeout=Config.HTTP_TIMEOUT)
    data = client.get_hot_threads()
    if data is None:
        return render_template(
            "hot_threads.html",
            server_name=name,
            threads=None,
            error="Could not fetch hot threads — server may be unreachable.",
        )
    return render_template(
        "hot_threads.html",
        server_name=name,
        threads=data.get("hot_threads") or {},
        error=None,
    )


@bp.route("/server/<name>/pipeline/<path:pipeline_id>")
def pipeline_detail(name, pipeline_id):
    snap = collector.get_snapshot()
    if snap is None:
        abort(404)
    data = snap.get(name)
    stats = data.get("stats") or {}
    pipelines = stats.get("pipelines") or {}
    if pipeline_id not in pipelines:
        abort(404)
    pipeline = _build_pipeline(name, pipeline_id, pipelines[pipeline_id] or {})
    return render_template("pipeline.html", server_name=name, pipeline=pipeline)


# ── Builder helpers ────────────────────────────────────────────────────────────

def _build_server(name: str, data: dict) -> dict:
    stats = data.get("stats") or {}
    info = data.get("info") or {}
    health = compute_health(data)

    jvm = stats.get("jvm") or {}
    mem = jvm.get("mem") or {}
    heap_used = mem.get("heap_used_in_bytes", 0)
    heap_max = mem.get("heap_max_in_bytes", 1)
    heap_committed = mem.get("heap_committed_in_bytes", 0)
    heap_pct = int((heap_used / heap_max) * 100) if heap_max else 0
    threads = jvm.get("threads") or {}
    gc_collectors = (jvm.get("gc") or {}).get("collectors") or {}

    process = stats.get("process") or {}
    cpu = process.get("cpu") or {}

    os_stats = stats.get("os") or {}
    os_cpu = os_stats.get("cpu") or {}
    load_avg = os_cpu.get("load_average") or {}

    pipelines = stats.get("pipelines") or {}
    pipeline_list = sorted(
        [_pipeline_summary(pid, pdata or {}) for pid, pdata in pipelines.items()],
        key=lambda p: p["id"],
    )

    return {
        "name": name,
        "health": health,
        "reachable": data.get("reachable"),
        "last_seen": data.get("last_seen"),
        "version": stats.get("version") or info.get("version", ""),
        "hostname": stats.get("hostname") or info.get("host", ""),
        "uptime_ms": jvm.get("uptime_in_millis", 0),
        "events_in": data.get("events_in", 0.0),
        "events_out": data.get("events_out", 0.0),
        # JVM
        "heap_used": heap_used,
        "heap_max": heap_max,
        "heap_committed": heap_committed,
        "heap_pct": heap_pct,
        "thread_count": threads.get("count", 0),
        "thread_peak": threads.get("peak_count", 0),
        "gc_young_count": (gc_collectors.get("young") or {}).get("collection_count", 0),
        "gc_young_ms": (gc_collectors.get("young") or {}).get("collection_time_in_millis", 0),
        "gc_old_count": (gc_collectors.get("old") or {}).get("collection_count", 0),
        "gc_old_ms": (gc_collectors.get("old") or {}).get("collection_time_in_millis", 0),
        # Process
        "cpu_pct": cpu.get("percent", 0),
        "open_fds": process.get("open_file_descriptors", 0),
        "max_fds": process.get("max_file_descriptors", 0),
        # OS
        "os_cpu_pct": os_cpu.get("percent", 0),
        "load_1m": load_avg.get("1m", 0),
        "load_5m": load_avg.get("5m", 0),
        "load_15m": load_avg.get("15m", 0),
        # Pipelines
        "pipelines": pipeline_list,
    }


def _pipeline_summary(pid: str, pdata: dict) -> dict:
    events = pdata.get("events") or {}
    queue = pdata.get("queue") or {}
    reloads = pdata.get("reloads") or {}
    return {
        "id": pid,
        "workers": pdata.get("workers", 0),
        "batch_size": pdata.get("batch_size", 0),
        "events_in": events.get("in", 0),
        "events_out": events.get("out", 0),
        "events_filtered": events.get("filtered", 0),
        "duration_ms": events.get("duration_in_millis", 0),
        "queue_type": queue.get("type", ""),
        "queue_events": queue.get("events_count", 0),
        "queue_size_bytes": queue.get("queue_size_in_bytes", 0),
        "reload_successes": reloads.get("successes", 0),
        "reload_failures": reloads.get("failures", 0),
        "last_failure_ts": reloads.get("last_failure_timestamp"),
        "last_success_ts": reloads.get("last_success_timestamp"),
    }


def _build_pipeline(server_name: str, pipeline_id: str, pdata: dict) -> dict:
    summary = _pipeline_summary(pipeline_id, pdata)
    plugins = pdata.get("plugins") or {}

    def _fmt(p: dict) -> dict:
        ev = p.get("events") or {}
        return {
            "id": p.get("id", ""),
            "name": p.get("name", ""),
            "events_in": ev.get("in", 0),
            "events_out": ev.get("out", 0),
            "duration_ms": ev.get("duration_in_millis", 0),
        }

    return {
        **summary,
        "server_name": server_name,
        "inputs": [_fmt(p) for p in (plugins.get("inputs") or [])],
        "filters": [_fmt(p) for p in (plugins.get("filters") or [])],
        "outputs": [_fmt(p) for p in (plugins.get("outputs") or [])],
    }
