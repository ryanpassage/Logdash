import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler

from logdash.health import compute_health
from logdash.logstash_client import LogstashClient
from logdash.snapshot import ServerSnapshot

logger = logging.getLogger(__name__)

_snapshot: ServerSnapshot | None = None
_scheduler: BackgroundScheduler | None = None


def get_snapshot() -> ServerSnapshot | None:
    return _snapshot


def start(
    servers: list[dict],
    poll_interval: int,
    storage=None,
    sample_interval: int = 60,
    sample_retention_days: int = 30,
    rollup_retention_days: int = 365,
) -> None:
    global _snapshot, _scheduler

    _snapshot = ServerSnapshot()
    clients = [LogstashClient(s["name"], s["url"]) for s in servers]

    for s in servers:
        _snapshot.ensure_entry(s["name"])

    if not clients:
        logger.warning("No Logstash servers configured — collector is idle")
        return

    # Seed the snapshot immediately so the dashboard isn't empty on first load
    threading.Thread(target=_poll_all, args=(clients,), daemon=True, name="logdash-seed").start()

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        _poll_all,
        trigger="interval",
        seconds=poll_interval,
        args=[clients],
        id="collector-poll",
        max_instances=1,
        coalesce=True,
    )
    if storage is not None:
        _scheduler.add_job(
            _write_samples,
            trigger="interval",
            seconds=sample_interval,
            args=[clients, storage],
            id="collector-sample",
            max_instances=1,
            coalesce=True,
        )
        _scheduler.add_job(
            _do_rollup,
            trigger="interval",
            hours=1,
            args=[clients, storage],
            id="collector-rollup",
            max_instances=1,
            coalesce=True,
        )
        _scheduler.add_job(
            _do_purge,
            trigger="interval",
            hours=24,
            args=[storage, sample_retention_days, rollup_retention_days],
            id="collector-purge",
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            "Storage sampling enabled — writing every %ds, rollup every 1h, purge every 24h",
            sample_interval,
        )
    _scheduler.start()
    logger.info("Collector started — polling %d server(s) every %ds", len(clients), poll_interval)


def stop() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Collector stopped")


def _poll_all(clients: list[LogstashClient]) -> None:
    for client in clients:
        try:
            info, stats = client.fetch_all()
            if info is None and stats is None:
                _snapshot.mark_unreachable(client.name)
            else:
                # Pull the structured Health Report only when the node responds,
                # so non-green nodes surface pipeline-level diagnoses.
                health_report = client.get_health_report()
                _snapshot.update(client.name, info, stats, health_report)
            health = compute_health(_snapshot.get(client.name))
            logger.debug("%s → %s: %s", client.name, health["status"], health["reasons"][0])
        except Exception:
            logger.exception("Unexpected error polling %s", client.name)
            _snapshot.mark_unreachable(client.name)


def _write_samples(clients: list[LogstashClient], storage) -> None:
    for client in clients:
        data = _snapshot.get(client.name)
        if not data.get("reachable"):
            continue
        try:
            info = data.get("info") or {}
            stats = data.get("stats") or {}
            health = compute_health(data)
            storage.write_server(client.name, info, stats)
            storage.write_event_sample(client.name, stats)
            storage.write_pipeline_samples(client.name, stats)
            storage.write_jvm_sample(client.name, stats)
            storage.write_health(client.name, health)
            logger.debug("Wrote sample for %s", client.name)
        except Exception:
            logger.exception("Unexpected error writing samples for %s", client.name)


def _do_rollup(clients: list[LogstashClient], storage) -> None:
    """Aggregate per-minute samples from the previous completed hour into HourlyRollups."""
    import time as _time
    now = int(_time.time())
    prev_hour_start = now - (now % 3600) - 3600
    for client in clients:
        data = _snapshot.get(client.name) if _snapshot else {}
        try:
            pipeline_ids = list(((data.get("stats") or {}).get("pipelines") or {}).keys())
            storage.rollup_events(client.name, prev_hour_start)
            storage.rollup_jvm(client.name, prev_hour_start)
            storage.rollup_pipelines(client.name, pipeline_ids, prev_hour_start)
            logger.debug("Rolled up hour %d for %s", prev_hour_start, client.name)
        except Exception:
            logger.exception("Rollup failed for %s", client.name)


def _do_purge(storage, sample_retention_days: int, rollup_retention_days: int) -> None:
    """Delete expired rows from all sample and rollup tables."""
    try:
        storage.purge_old_samples(sample_retention_days, rollup_retention_days)
        logger.info("Purge complete")
    except Exception:
        logger.exception("Purge failed")
