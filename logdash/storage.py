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


def _inverted_hour_ticks(hour_ts: int) -> str:
    """Inverted RowKey at hour resolution; hour_ts is Unix seconds truncated to the hour."""
    return f"{_MAX_TICKS - hour_ts * 1000:020d}"


def _hour_range(hour_start_ts: int) -> tuple[str, str]:
    """Return (rk_low, rk_high) RowKey bounds for the given hour.

    Inverted-ticks order: end of hour (more recent) → smaller RowKey (rk_low);
    start of hour (older) → larger RowKey (rk_high).
    """
    rk_low  = f"{_MAX_TICKS - (hour_start_ts + 3600) * 1000:020d}"
    rk_high = f"{_MAX_TICKS - hour_start_ts * 1000:020d}"
    return rk_low, rk_high


def _cutoff_inverted_days(days: int) -> str:
    """Return the RowKey boundary; rows with RowKey > this value are older than `days` days."""
    cutoff_ms = int(time.time() * 1000) - days * 86400 * 1000
    return f"{_MAX_TICKS - cutoff_ms:020d}"


def _since_inverted_hours(hours: int) -> str:
    """Return inverted RowKey for `hours` ago (at hour resolution)."""
    now = int(time.time())
    since_ts = now - hours * 3600
    since_hour = since_ts - (since_ts % 3600)
    return f"{_MAX_TICKS - since_hour * 1000:020d}"


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

    # ── Rollup ────────────────────────────────────────────────────────────

    def rollup_events(self, server: str, hour_start_ts: int) -> None:
        """Aggregate EventSamples for the completed hour into HourlyRollups."""
        rk_low, rk_high = _hour_range(hour_start_ts)
        try:
            rows = list(self._get_table("EventSamples").query_entities(
                query_filter=f"PartitionKey eq '{server}' and RowKey ge '{rk_low}' and RowKey le '{rk_high}'",
                select=["RowKey", "events_in", "events_out", "events_filtered", "queue_size"],
            ))
        except Exception as exc:
            logger.warning("rollup_events query failed for %s: %s", server, exc)
            return
        if len(rows) < 2:
            return

        rows.sort(key=lambda r: r["RowKey"], reverse=True)  # largest RowKey = oldest first
        first, last = rows[0], rows[-1]
        queue_sizes = [int(r.get("queue_size", 0)) for r in rows]
        entity = {
            "PartitionKey": f"{server}|events",
            "RowKey": _inverted_hour_ticks(hour_start_ts),
            "hour_ts": hour_start_ts,
            "events_in_delta":       max(0, int(last.get("events_in", 0))       - int(first.get("events_in", 0))),
            "events_out_delta":      max(0, int(last.get("events_out", 0))      - int(first.get("events_out", 0))),
            "events_filtered_delta": max(0, int(last.get("events_filtered", 0)) - int(first.get("events_filtered", 0))),
            "queue_size_avg": int(sum(queue_sizes) / len(queue_sizes)),
            "queue_size_max": max(queue_sizes),
            "sample_count": len(rows),
        }
        try:
            self._get_table("HourlyRollups").upsert_entity(entity)
        except Exception as exc:
            logger.warning("rollup_events write failed for %s: %s", server, exc)

    def rollup_jvm(self, server: str, hour_start_ts: int) -> None:
        """Aggregate JvmSamples for the completed hour into HourlyRollups."""
        rk_low, rk_high = _hour_range(hour_start_ts)
        try:
            rows = list(self._get_table("JvmSamples").query_entities(
                query_filter=f"PartitionKey eq '{server}' and RowKey ge '{rk_low}' and RowKey le '{rk_high}'",
                select=["RowKey", "heap_used_bytes", "heap_max_bytes", "threads_count"],
            ))
        except Exception as exc:
            logger.warning("rollup_jvm query failed for %s: %s", server, exc)
            return
        if not rows:
            return

        heap_used = [int(r.get("heap_used_bytes", 0)) for r in rows]
        heap_max  = [int(r.get("heap_max_bytes",  0)) for r in rows]
        threads   = [int(r.get("threads_count",   0)) for r in rows]
        n = len(rows)
        entity = {
            "PartitionKey": f"{server}|jvm",
            "RowKey": _inverted_hour_ticks(hour_start_ts),
            "hour_ts": hour_start_ts,
            "heap_used_avg": int(sum(heap_used) / n),
            "heap_used_max": max(heap_used),
            "heap_max_avg":  int(sum(heap_max)  / n),
            "threads_avg":   int(sum(threads)   / n),
            "sample_count": n,
        }
        try:
            self._get_table("HourlyRollups").upsert_entity(entity)
        except Exception as exc:
            logger.warning("rollup_jvm write failed for %s: %s", server, exc)

    def rollup_pipelines(self, server: str, pipeline_ids: list[str], hour_start_ts: int) -> None:
        """Aggregate PipelineSamples for each known pipeline into HourlyRollups."""
        for pid in pipeline_ids:
            self._rollup_one_pipeline(server, pid, hour_start_ts)

    def _rollup_one_pipeline(self, server: str, pid: str, hour_start_ts: int) -> None:
        rk_low, rk_high = _hour_range(hour_start_ts)
        pk = f"{server}|{pid}"
        try:
            rows = list(self._get_table("PipelineSamples").query_entities(
                query_filter=f"PartitionKey eq '{pk}' and RowKey ge '{rk_low}' and RowKey le '{rk_high}'",
                select=["RowKey", "events_in", "events_out", "events_filtered", "queue_size"],
            ))
        except Exception as exc:
            logger.warning("rollup_pipeline query failed for %s|%s: %s", server, pid, exc)
            return
        if len(rows) < 2:
            return

        rows.sort(key=lambda r: r["RowKey"], reverse=True)
        first, last = rows[0], rows[-1]
        queue_sizes = [int(r.get("queue_size", 0)) for r in rows]
        entity = {
            "PartitionKey": f"{server}|{pid}|pipeline",
            "RowKey": _inverted_hour_ticks(hour_start_ts),
            "hour_ts": hour_start_ts,
            "events_in_delta":       max(0, int(last.get("events_in", 0))       - int(first.get("events_in", 0))),
            "events_out_delta":      max(0, int(last.get("events_out", 0))      - int(first.get("events_out", 0))),
            "events_filtered_delta": max(0, int(last.get("events_filtered", 0)) - int(first.get("events_filtered", 0))),
            "queue_size_avg": int(sum(queue_sizes) / len(queue_sizes)),
            "queue_size_max": max(queue_sizes),
            "sample_count": len(rows),
        }
        try:
            self._get_table("HourlyRollups").upsert_entity(entity)
        except Exception as exc:
            logger.warning("rollup_pipeline write failed for %s|%s: %s", server, pid, exc)

    # ── Purge ─────────────────────────────────────────────────────────────

    def purge_old_samples(self, sample_retention_days: int, rollup_retention_days: int) -> None:
        """Delete per-minute samples older than sample_retention_days and hourly rollups older than rollup_retention_days."""
        sample_cutoff = _cutoff_inverted_days(sample_retention_days)
        rollup_cutoff = _cutoff_inverted_days(rollup_retention_days)
        for table_name in ("EventSamples", "JvmSamples", "PipelineSamples"):
            self._purge_table(table_name, sample_cutoff)
        self._purge_table("HourlyRollups", rollup_cutoff)

    def _purge_table(self, table_name: str, cutoff_inverted: str) -> None:
        """Delete all entities with RowKey > cutoff_inverted (i.e., older than the cutoff)."""
        try:
            table = self._get_table(table_name)
            old_rows = list(table.query_entities(
                query_filter=f"RowKey gt '{cutoff_inverted}'",
                select=["PartitionKey", "RowKey"],
            ))
        except Exception as exc:
            logger.warning("Purge query failed for %s: %s", table_name, exc)
            return

        if not old_rows:
            return

        by_partition: dict[str, list[str]] = {}
        for row in old_rows:
            by_partition.setdefault(row["PartitionKey"], []).append(row["RowKey"])

        deleted = 0
        for pk, row_keys in by_partition.items():
            for rk in row_keys:
                try:
                    table.delete_entity(pk, rk)
                    deleted += 1
                except Exception as exc:
                    logger.debug("Delete failed %s/%s/%s: %s", table_name, pk, rk, exc)

        if deleted:
            logger.info("Purged %d rows from %s", deleted, table_name)

    # ── Hourly rollup queries ──────────────────────────────────────────────

    def query_hourly_events(self, server: str, hours: int) -> list[dict]:
        """Query HourlyRollups for event aggregates spanning the last `hours` hours."""
        since = _since_inverted_hours(hours)
        try:
            rows = self._get_table("HourlyRollups").query_entities(
                query_filter=f"PartitionKey eq '{server}|events' and RowKey le '{since}'",
                select=["RowKey", "hour_ts", "events_in_delta", "events_out_delta",
                        "events_filtered_delta", "queue_size_avg"],
            )
            return [_hourly_row_to_sample(r) for r in rows]
        except Exception as exc:
            logger.warning("query_hourly_events %s: %s", server, exc)
            return []

    def query_hourly_jvm(self, server: str, hours: int) -> list[dict]:
        """Query HourlyRollups for JVM aggregates spanning the last `hours` hours."""
        since = _since_inverted_hours(hours)
        try:
            rows = self._get_table("HourlyRollups").query_entities(
                query_filter=f"PartitionKey eq '{server}|jvm' and RowKey le '{since}'",
                select=["RowKey", "hour_ts", "heap_used_avg", "heap_used_max",
                        "heap_max_avg", "threads_avg"],
            )
            return [_hourly_row_to_sample(r) for r in rows]
        except Exception as exc:
            logger.warning("query_hourly_jvm %s: %s", server, exc)
            return []

    def query_hourly_pipeline(self, server: str, pipeline_id: str, hours: int) -> list[dict]:
        """Query HourlyRollups for pipeline aggregates spanning the last `hours` hours."""
        since = _since_inverted_hours(hours)
        pk = f"{server}|{pipeline_id}|pipeline"
        try:
            rows = self._get_table("HourlyRollups").query_entities(
                query_filter=f"PartitionKey eq '{pk}' and RowKey le '{since}'",
                select=["RowKey", "hour_ts", "events_in_delta", "events_out_delta",
                        "events_filtered_delta", "queue_size_avg"],
            )
            return [_hourly_row_to_sample(r) for r in rows]
        except Exception as exc:
            logger.warning("query_hourly_pipeline %s|%s: %s", server, pipeline_id, exc)
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


def _hourly_row_to_sample(row) -> dict:
    """Convert a HourlyRollups entity to a plain dict with an ISO timestamp at hour resolution."""
    hour_ts = row.get("hour_ts")
    if hour_ts:
        ts = datetime.fromtimestamp(int(hour_ts), tz=timezone.utc).isoformat()
    else:
        ts_ms = _MAX_TICKS - int(row.get("RowKey", "0"))
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
    result = {"ts": ts}
    skip = {"PartitionKey", "RowKey", "etag", "Timestamp", "metadata", "hour_ts"}
    for k, v in row.items():
        if k not in skip:
            result[k] = v
    return result
