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


def start(servers: list[dict], poll_interval: int) -> None:
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
                _snapshot.update(client.name, info, stats)
            health = compute_health(_snapshot.get(client.name))
            logger.debug("%s → %s: %s", client.name, health["status"], health["reasons"][0])
        except Exception:
            logger.exception("Unexpected error polling %s", client.name)
            _snapshot.mark_unreachable(client.name)
