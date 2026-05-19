import logging
import time
from datetime import datetime, timezone

from azure.data.tables import TableServiceClient

logger = logging.getLogger(__name__)

# Largest safe value for a 20-digit zero-padded integer (10^19 - 1).
# Subtracting current epoch-ms gives a RowKey that sorts newest-first,
# since Azure Table Storage orders rows lexicographically ascending.
_MAX_TICKS = 9_999_999_999_999_999_999


def _inverted_ticks() -> str:
    now_ms = int(time.time() * 1000)
    return f"{_MAX_TICKS - now_ms:020d}"


class StorageAdapter:
    """Thin wrapper around azure-data-tables for all LogDash writes."""

    def __init__(self, connection_string: str) -> None:
        self._service = TableServiceClient.from_connection_string(connection_string)
        self._tables: dict[str, object] = {}

    def _get_table(self, name: str):
        if name not in self._tables:
            try:
                self._tables[name] = self._service.create_table_if_not_exists(name)
            except Exception as exc:
                logger.warning("Could not create table %s: %s", name, exc)
                self._tables[name] = self._service.get_table_client(name)
        return self._tables[name]

    def write_server(self, name: str, info: dict, stats: dict) -> None:
        entity = {
            "PartitionKey": "server",
            "RowKey": name,
            "version": (info or {}).get("version", ""),
            "address": (info or {}).get("http_address", ""),
            "last_seen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        try:
            self._get_table("Servers").upsert_entity(entity)
        except Exception as exc:
            logger.warning("Failed to write Servers for %s: %s", name, exc)

    def write_event_sample(self, server: str, stats: dict) -> None:
        events = (stats or {}).get("events") or {}
        queue_size = sum(
            ((p or {}).get("queue") or {}).get("events_count", 0)
            for p in ((stats or {}).get("pipelines") or {}).values()
        )
        entity = {
            "PartitionKey": server,
            "RowKey": _inverted_ticks(),
            "events_in": int(events.get("in", 0)),
            "events_out": int(events.get("out", 0)),
            "events_filtered": int(events.get("filtered", 0)),
            "duration_ms": int(events.get("duration_in_millis", 0)),
            "queue_size": int(queue_size),
        }
        try:
            self._get_table("EventSamples").create_entity(entity)
        except Exception as exc:
            logger.warning("Failed to write EventSamples for %s: %s", server, exc)

    def write_pipeline_samples(self, server: str, stats: dict) -> None:
        table = self._get_table("PipelineSamples")
        for pid, pdata in ((stats or {}).get("pipelines") or {}).items():
            events = (pdata or {}).get("events") or {}
            queue = (pdata or {}).get("queue") or {}
            entity = {
                "PartitionKey": f"{server}|{pid}",
                "RowKey": _inverted_ticks(),
                "events_in": int(events.get("in", 0)),
                "events_out": int(events.get("out", 0)),
                "events_filtered": int(events.get("filtered", 0)),
                "duration_ms": int(events.get("duration_in_millis", 0)),
                "queue_size": int(queue.get("events_count", 0)),
                "workers": int((pdata or {}).get("workers", 0)),
            }
            try:
                table.create_entity(entity)
            except Exception as exc:
                logger.warning("Failed to write PipelineSamples for %s|%s: %s", server, pid, exc)

    def write_jvm_sample(self, server: str, stats: dict) -> None:
        jvm = (stats or {}).get("jvm") or {}
        mem = jvm.get("mem") or {}
        collectors = (jvm.get("gc") or {}).get("collectors") or {}
        young = collectors.get("young") or {}
        old = collectors.get("old") or {}
        entity = {
            "PartitionKey": server,
            "RowKey": _inverted_ticks(),
            "heap_used_bytes": int(mem.get("heap_used_in_bytes", 0)),
            "heap_max_bytes": int(mem.get("heap_max_in_bytes", 0)),
            "threads_count": int((jvm.get("threads") or {}).get("count", 0)),
            "gc_young_count": int(young.get("collection_count", 0)),
            "gc_young_time_ms": int(young.get("collection_time_in_millis", 0)),
            "gc_old_count": int(old.get("collection_count", 0)),
            "gc_old_time_ms": int(old.get("collection_time_in_millis", 0)),
        }
        try:
            self._get_table("JvmSamples").create_entity(entity)
        except Exception as exc:
            logger.warning("Failed to write JvmSamples for %s: %s", server, exc)

    def write_health(self, server: str, health: dict) -> None:
        entity = {
            "PartitionKey": server,
            "RowKey": _inverted_ticks(),
            "status": health.get("status", "unknown"),
            "reason": "; ".join(health.get("reasons", [])),
        }
        try:
            self._get_table("Health").create_entity(entity)
        except Exception as exc:
            logger.warning("Failed to write Health for %s: %s", server, exc)

    def query_event_samples(self, server: str, minutes: int) -> list[dict]:
        since = _since_inverted(minutes)
        try:
            rows = self._get_table("EventSamples").query_entities(
                query_filter=f"PartitionKey eq '{server}' and RowKey le '{since}'",
                select=["RowKey", "events_in", "events_out", "events_filtered", "queue_size"],
            )
            return [_row_to_sample(r) for r in rows]
        except Exception as exc:
            logger.warning("query EventSamples %s: %s", server, exc)
            return []

    def query_jvm_samples(self, server: str, minutes: int) -> list[dict]:
        since = _since_inverted(minutes)
        try:
            rows = self._get_table("JvmSamples").query_entities(
                query_filter=f"PartitionKey eq '{server}' and RowKey le '{since}'",
                select=["RowKey", "heap_used_bytes", "heap_max_bytes", "threads_count"],
            )
            return [_row_to_sample(r) for r in rows]
        except Exception as exc:
            logger.warning("query JvmSamples %s: %s", server, exc)
            return []

    def query_pipeline_samples(self, server: str, pipeline_id: str, minutes: int) -> list[dict]:
        since = _since_inverted(minutes)
        pk = f"{server}|{pipeline_id}"
        try:
            rows = self._get_table("PipelineSamples").query_entities(
                query_filter=f"PartitionKey eq '{pk}' and RowKey le '{since}'",
                select=["RowKey", "events_in", "events_out", "events_filtered", "queue_size"],
            )
            return [_row_to_sample(r) for r in rows]
        except Exception as exc:
            logger.warning("query PipelineSamples %s|%s: %s", server, pipeline_id, exc)
            return []


def _since_inverted(minutes: int) -> str:
    """Return the inverted-ticks RowKey corresponding to `minutes` ago."""
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - minutes * 60 * 1000
    return f"{_MAX_TICKS - since_ms:020d}"


def _row_to_sample(row) -> dict:
    """Convert a Table Storage entity back to a plain dict with an ISO timestamp."""
    row_key = row.get("RowKey", "0")
    ts_ms = _MAX_TICKS - int(row_key)
    ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
    result = {"ts": ts}
    skip = {"PartitionKey", "RowKey", "etag", "Timestamp", "metadata"}
    for k, v in row.items():
        if k not in skip:
            result[k] = v
    return result
