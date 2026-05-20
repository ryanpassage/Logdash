import time
from unittest.mock import MagicMock, patch

from logdash.storage import StorageAdapter, _inverted_ticks, _since_inverted, _row_to_sample


# ---------------------------------------------------------------------------
# _inverted_ticks
# ---------------------------------------------------------------------------

def test_inverted_ticks_is_20_chars():
    t = _inverted_ticks()
    assert len(t) == 20


def test_inverted_ticks_decreases_over_time():
    t1 = _inverted_ticks()
    time.sleep(0.05)
    t2 = _inverted_ticks()
    assert t2 < t1, "later ticks should sort lexicographically earlier (newest-first)"


def test_inverted_ticks_is_numeric_string():
    t = _inverted_ticks()
    assert t.isdigit()


# ---------------------------------------------------------------------------
# Helper: build a StorageAdapter backed by a mock TableServiceClient
# ---------------------------------------------------------------------------

def _make_adapter():
    mock_service = MagicMock()
    mock_table = MagicMock()
    mock_service.create_table_if_not_exists.return_value = mock_table
    with patch("logdash.storage.TableServiceClient") as mock_cls:
        mock_cls.from_connection_string.return_value = mock_service
        adapter = StorageAdapter("UseDevelopmentStorage=true")
    adapter._service = mock_service
    return adapter, mock_service, mock_table


# ---------------------------------------------------------------------------
# Lazy table creation
# ---------------------------------------------------------------------------

def test_get_table_creates_once_and_caches():
    adapter, mock_service, mock_table = _make_adapter()
    t1 = adapter._get_table("Servers")
    t2 = adapter._get_table("Servers")
    assert t1 is t2
    mock_service.create_table_if_not_exists.assert_called_once_with("Servers")


def test_get_table_falls_back_on_error():
    adapter, mock_service, mock_table = _make_adapter()
    mock_service.create_table_if_not_exists.side_effect = Exception("quota exceeded")
    fallback = MagicMock()
    mock_service.get_table_client.return_value = fallback
    t = adapter._get_table("EventSamples")
    assert t is fallback


# ---------------------------------------------------------------------------
# write_server
# ---------------------------------------------------------------------------

def test_write_server_upserts_correct_keys():
    adapter, mock_service, mock_table = _make_adapter()
    info = {"version": "8.17.0", "http_address": "0.0.0.0:9600"}
    adapter.write_server("ls-01", info, {})
    mock_table.upsert_entity.assert_called_once()
    entity = mock_table.upsert_entity.call_args[0][0]
    assert entity["PartitionKey"] == "server"
    assert entity["RowKey"] == "ls-01"
    assert entity["version"] == "8.17.0"
    assert entity["address"] == "0.0.0.0:9600"
    assert "last_seen" in entity


def test_write_server_handles_missing_info():
    adapter, mock_service, mock_table = _make_adapter()
    adapter.write_server("ls-01", {}, {})
    entity = mock_table.upsert_entity.call_args[0][0]
    assert entity["version"] == ""
    assert entity["address"] == ""


def test_write_server_swallows_exception():
    adapter, mock_service, mock_table = _make_adapter()
    mock_table.upsert_entity.side_effect = Exception("network error")
    adapter.write_server("ls-01", {}, {})  # should not raise


# ---------------------------------------------------------------------------
# write_event_sample
# ---------------------------------------------------------------------------

def test_write_event_sample_correct_fields():
    adapter, mock_service, mock_table = _make_adapter()
    stats = {
        "events": {"in": 1000, "out": 900, "filtered": 1000, "duration_in_millis": 500},
        "pipelines": {"main": {"queue": {"events_count": 42}}},
    }
    adapter.write_event_sample("ls-01", stats)
    entity = mock_table.create_entity.call_args[0][0]
    assert entity["PartitionKey"] == "ls-01"
    assert len(entity["RowKey"]) == 20
    assert entity["events_in"] == 1000
    assert entity["events_out"] == 900
    assert entity["queue_size"] == 42


# ---------------------------------------------------------------------------
# write_pipeline_samples
# ---------------------------------------------------------------------------

def test_write_pipeline_samples_one_entity_per_pipeline():
    adapter, mock_service, mock_table = _make_adapter()
    stats = {
        "pipelines": {
            "main": {"events": {"in": 100, "out": 80, "filtered": 100, "duration_in_millis": 10},
                     "queue": {"events_count": 5}, "workers": 2},
            "beats": {"events": {"in": 50, "out": 50, "filtered": 50, "duration_in_millis": 5},
                      "queue": {"events_count": 0}, "workers": 1},
        }
    }
    adapter.write_pipeline_samples("ls-01", stats)
    assert mock_table.create_entity.call_count == 2
    partition_keys = {c[0][0]["PartitionKey"] for c in mock_table.create_entity.call_args_list}
    assert "ls-01|main" in partition_keys
    assert "ls-01|beats" in partition_keys


def test_write_pipeline_samples_empty_pipelines():
    adapter, mock_service, mock_table = _make_adapter()
    adapter.write_pipeline_samples("ls-01", {"pipelines": {}})
    mock_table.create_entity.assert_not_called()


# ---------------------------------------------------------------------------
# write_jvm_sample
# ---------------------------------------------------------------------------

def test_write_jvm_sample_gc_fields():
    adapter, mock_service, mock_table = _make_adapter()
    stats = {
        "jvm": {
            "mem": {"heap_used_in_bytes": 512_000_000, "heap_max_in_bytes": 1_024_000_000},
            "threads": {"count": 40},
            "gc": {
                "collectors": {
                    "young": {"collection_count": 120, "collection_time_in_millis": 3000},
                    "old": {"collection_count": 2, "collection_time_in_millis": 800},
                }
            },
        }
    }
    adapter.write_jvm_sample("ls-01", stats)
    entity = mock_table.create_entity.call_args[0][0]
    assert entity["heap_used_bytes"] == 512_000_000
    assert entity["heap_max_bytes"] == 1_024_000_000
    assert entity["threads_count"] == 40
    assert entity["gc_young_count"] == 120
    assert entity["gc_old_time_ms"] == 800


# ---------------------------------------------------------------------------
# write_health
# ---------------------------------------------------------------------------

def test_write_health_correct_fields():
    adapter, mock_service, mock_table = _make_adapter()
    health = {"status": "yellow", "reasons": ["JVM heap high: 82%", "Node has reload failures"]}
    adapter.write_health("ls-01", health)
    entity = mock_table.create_entity.call_args[0][0]
    assert entity["PartitionKey"] == "ls-01"
    assert entity["status"] == "yellow"
    assert "JVM heap high" in entity["reason"]
    assert "reload failures" in entity["reason"]


# ---------------------------------------------------------------------------
# _since_inverted / _row_to_sample
# ---------------------------------------------------------------------------

def test_since_inverted_is_20_chars():
    assert len(_since_inverted(60)) == 20


def test_since_inverted_greater_than_current():
    # N minutes ago has a larger inverted value (older = larger RowKey)
    current = _inverted_ticks()
    since = _since_inverted(60)
    assert since > current


def test_row_to_sample_reconstructs_timestamp():
    from logdash.storage import _MAX_TICKS
    now_ms = int(time.time() * 1000)
    row_key = f"{_MAX_TICKS - now_ms:020d}"
    row = {"RowKey": row_key, "PartitionKey": "ls-01", "events_in": 100}
    sample = _row_to_sample(row)
    assert "ts" in sample
    assert "events_in" in sample
    assert "PartitionKey" not in sample
    assert "RowKey" not in sample


# ---------------------------------------------------------------------------
# query_event_samples / query_jvm_samples / query_pipeline_samples
# ---------------------------------------------------------------------------

def test_query_event_samples_returns_list():
    adapter, mock_service, mock_table = _make_adapter()
    from logdash.storage import _MAX_TICKS
    now_ms = int(time.time() * 1000)
    row_key = f"{_MAX_TICKS - now_ms:020d}"
    mock_table.query_entities.return_value = [
        {"RowKey": row_key, "PartitionKey": "ls-01", "events_in": 100, "events_out": 90, "events_filtered": 5, "queue_size": 0},
    ]
    rows = adapter.query_event_samples("ls-01", 60)
    assert len(rows) == 1
    assert rows[0]["events_in"] == 100
    assert "ts" in rows[0]


def test_query_event_samples_returns_empty_on_exception():
    adapter, mock_service, mock_table = _make_adapter()
    mock_table.query_entities.side_effect = Exception("not found")
    rows = adapter.query_event_samples("ls-01", 60)
    assert rows == []


def test_query_jvm_samples_returns_list():
    adapter, mock_service, mock_table = _make_adapter()
    from logdash.storage import _MAX_TICKS
    now_ms = int(time.time() * 1000)
    row_key = f"{_MAX_TICKS - now_ms:020d}"
    mock_table.query_entities.return_value = [
        {"RowKey": row_key, "PartitionKey": "ls-01", "heap_used_bytes": 500, "heap_max_bytes": 1000, "threads_count": 40},
    ]
    rows = adapter.query_jvm_samples("ls-01", 60)
    assert len(rows) == 1
    assert rows[0]["heap_used_bytes"] == 500


def test_query_pipeline_samples_uses_correct_partition_key():
    adapter, mock_service, mock_table = _make_adapter()
    mock_table.query_entities.return_value = []
    adapter.query_pipeline_samples("ls-01", "main", 60)
    call_kwargs = mock_table.query_entities.call_args[1]
    assert "ls-01|main" in call_kwargs["query_filter"]


# ---------------------------------------------------------------------------
# Helper functions (Phase 4)
# ---------------------------------------------------------------------------

def test_inverted_hour_ticks_is_20_chars():
    from logdash.storage import _inverted_hour_ticks
    now = int(time.time())
    hour_ts = now - (now % 3600)
    assert len(_inverted_hour_ticks(hour_ts)) == 20


def test_hour_range_low_less_than_high():
    from logdash.storage import _hour_range
    now = int(time.time())
    hour_ts = now - (now % 3600) - 3600
    rk_low, rk_high = _hour_range(hour_ts)
    assert rk_low < rk_high, "rk_low (end of hour) should be smaller than rk_high (start of hour)"


def test_cutoff_inverted_days_greater_than_current():
    from logdash.storage import _cutoff_inverted_days, _inverted_ticks
    current = _inverted_ticks()
    cutoff = _cutoff_inverted_days(30)
    assert cutoff > current, "30-day-old rows have larger RowKey than current"


def test_hourly_row_to_sample_uses_hour_ts():
    from logdash.storage import _hourly_row_to_sample
    hour_ts = 1_700_000_000
    row = {
        "RowKey": "99999999999999999999",
        "PartitionKey": "ls-01|events",
        "hour_ts": hour_ts,
        "events_in_delta": 1234,
    }
    sample = _hourly_row_to_sample(row)
    assert "ts" in sample
    assert "2023" in sample["ts"]  # sanity-check the year
    assert sample["events_in_delta"] == 1234
    assert "RowKey" not in sample
    assert "hour_ts" not in sample


# ---------------------------------------------------------------------------
# rollup_events
# ---------------------------------------------------------------------------

def _make_event_rows(n: int, base_in: int = 0):
    """Generate n fake EventSamples rows newest-first (smallest RowKey first)."""
    from logdash.storage import _MAX_TICKS
    rows = []
    for i in range(n):
        ts_ms = int(time.time() * 1000) - i * 60_000
        rows.append({
            "RowKey": f"{_MAX_TICKS - ts_ms:020d}",
            "PartitionKey": "ls-01",
            "events_in": base_in + i * 100,
            "events_out": base_in + i * 90,
            "events_filtered": base_in + i * 100,
            "queue_size": 5,
        })
    return rows


def test_rollup_events_writes_hourly_row():
    adapter, mock_service, mock_table = _make_adapter()
    mock_table.query_entities.return_value = _make_event_rows(10)
    hour_ts = int(time.time()) - 7200
    hour_ts -= hour_ts % 3600
    adapter.rollup_events("ls-01", hour_ts)
    mock_table.upsert_entity.assert_called_once()
    entity = mock_table.upsert_entity.call_args[0][0]
    assert entity["PartitionKey"] == "ls-01|events"
    assert entity["sample_count"] == 10
    assert entity["hour_ts"] == hour_ts


def test_rollup_events_skips_with_fewer_than_2_rows():
    adapter, mock_service, mock_table = _make_adapter()
    mock_table.query_entities.return_value = _make_event_rows(1)
    adapter.rollup_events("ls-01", int(time.time()) - 7200)
    mock_table.upsert_entity.assert_not_called()


def test_rollup_events_delta_is_nonnegative():
    adapter, mock_service, mock_table = _make_adapter()
    mock_table.query_entities.return_value = _make_event_rows(5, base_in=1000)
    adapter.rollup_events("ls-01", int(time.time()) - 7200)
    entity = mock_table.upsert_entity.call_args[0][0]
    assert entity["events_in_delta"] >= 0
    assert entity["events_out_delta"] >= 0


# ---------------------------------------------------------------------------
# rollup_jvm
# ---------------------------------------------------------------------------

def _make_jvm_rows(n: int):
    from logdash.storage import _MAX_TICKS
    rows = []
    for i in range(n):
        ts_ms = int(time.time() * 1000) - i * 60_000
        rows.append({
            "RowKey": f"{_MAX_TICKS - ts_ms:020d}",
            "PartitionKey": "ls-01",
            "heap_used_bytes": 500_000_000 + i * 1_000_000,
            "heap_max_bytes": 1_000_000_000,
            "threads_count": 40,
        })
    return rows


def test_rollup_jvm_writes_hourly_row():
    adapter, mock_service, mock_table = _make_adapter()
    mock_table.query_entities.return_value = _make_jvm_rows(5)
    hour_ts = int(time.time()) - 7200
    hour_ts -= hour_ts % 3600
    adapter.rollup_jvm("ls-01", hour_ts)
    mock_table.upsert_entity.assert_called_once()
    entity = mock_table.upsert_entity.call_args[0][0]
    assert entity["PartitionKey"] == "ls-01|jvm"
    assert "heap_used_avg" in entity
    assert "heap_used_max" in entity
    assert entity["sample_count"] == 5


def test_rollup_jvm_skips_with_no_rows():
    adapter, mock_service, mock_table = _make_adapter()
    mock_table.query_entities.return_value = []
    adapter.rollup_jvm("ls-01", int(time.time()) - 7200)
    mock_table.upsert_entity.assert_not_called()


# ---------------------------------------------------------------------------
# rollup_pipelines
# ---------------------------------------------------------------------------

def test_rollup_pipelines_writes_one_row_per_pipeline():
    adapter, mock_service, mock_table = _make_adapter()
    from logdash.storage import _MAX_TICKS

    def pipeline_rows(pid):
        ts_ms = int(time.time() * 1000)
        return [
            {"RowKey": f"{_MAX_TICKS - ts_ms:020d}",         "PartitionKey": f"ls-01|{pid}",
             "events_in": 200, "events_out": 180, "events_filtered": 200, "queue_size": 0},
            {"RowKey": f"{_MAX_TICKS - (ts_ms - 3600000):020d}", "PartitionKey": f"ls-01|{pid}",
             "events_in": 100, "events_out": 90,  "events_filtered": 100, "queue_size": 0},
        ]

    mock_table.query_entities.side_effect = lambda **kw: (
        pipeline_rows("main") if "ls-01|main" in kw["query_filter"] else pipeline_rows("beats")
    )
    adapter.rollup_pipelines("ls-01", ["main", "beats"], int(time.time()) - 7200)
    assert mock_table.upsert_entity.call_count == 2
    pks = {c[0][0]["PartitionKey"] for c in mock_table.upsert_entity.call_args_list}
    assert "ls-01|main|pipeline" in pks
    assert "ls-01|beats|pipeline" in pks


# ---------------------------------------------------------------------------
# purge_old_samples
# ---------------------------------------------------------------------------

def test_purge_table_deletes_old_rows():
    adapter, mock_service, mock_table = _make_adapter()
    from logdash.storage import _cutoff_inverted_days
    old_rk = f"{int(_cutoff_inverted_days(30).replace('0', '9', 1)):020d}"  # simulate a very old row

    # Simulate finding 2 old rows
    mock_table.query_entities.return_value = [
        {"PartitionKey": "ls-01", "RowKey": old_rk},
        {"PartitionKey": "ls-01", "RowKey": "99999999999999999998"},
    ]
    adapter._purge_table("EventSamples", _cutoff_inverted_days(30))
    assert mock_table.delete_entity.call_count == 2


def test_purge_table_skips_when_no_old_rows():
    adapter, mock_service, mock_table = _make_adapter()
    from logdash.storage import _cutoff_inverted_days
    mock_table.query_entities.return_value = []
    adapter._purge_table("EventSamples", _cutoff_inverted_days(30))
    mock_table.delete_entity.assert_not_called()


def test_purge_old_samples_covers_all_tables():
    adapter, mock_service, mock_table = _make_adapter()
    mock_table.query_entities.return_value = []
    adapter.purge_old_samples(30, 365)
    # Should have queried 4 tables: EventSamples, JvmSamples, PipelineSamples, HourlyRollups
    assert mock_table.query_entities.call_count == 4


# ---------------------------------------------------------------------------
# query_hourly_events / query_hourly_jvm / query_hourly_pipeline
# ---------------------------------------------------------------------------

def _make_hourly_row(pk: str, hour_ts: int, **fields):
    from logdash.storage import _inverted_hour_ticks
    row = {"RowKey": _inverted_hour_ticks(hour_ts), "PartitionKey": pk, "hour_ts": hour_ts}
    row.update(fields)
    return row


def test_query_hourly_events_returns_normalized():
    adapter, mock_service, mock_table = _make_adapter()
    hour_ts = int(time.time()) - 3600
    hour_ts -= hour_ts % 3600
    mock_table.query_entities.return_value = [
        _make_hourly_row("ls-01|events", hour_ts,
                         events_in_delta=500, events_out_delta=480,
                         events_filtered_delta=500, queue_size_avg=3),
    ]
    rows = adapter.query_hourly_events("ls-01", 24)
    assert len(rows) == 1
    assert rows[0]["events_in_delta"] == 500
    assert "ts" in rows[0]


def test_query_hourly_jvm_returns_normalized():
    adapter, mock_service, mock_table = _make_adapter()
    hour_ts = int(time.time()) - 3600
    hour_ts -= hour_ts % 3600
    mock_table.query_entities.return_value = [
        _make_hourly_row("ls-01|jvm", hour_ts,
                         heap_used_avg=600_000_000, heap_used_max=700_000_000,
                         heap_max_avg=1_000_000_000, threads_avg=40),
    ]
    rows = adapter.query_hourly_jvm("ls-01", 48)
    assert len(rows) == 1
    assert rows[0]["heap_used_avg"] == 600_000_000


def test_query_hourly_pipeline_uses_correct_partition_key():
    adapter, mock_service, mock_table = _make_adapter()
    mock_table.query_entities.return_value = []
    adapter.query_hourly_pipeline("ls-01", "main", 168)
    call_kwargs = mock_table.query_entities.call_args[1]
    assert "ls-01|main|pipeline" in call_kwargs["query_filter"]


def test_query_hourly_returns_empty_on_exception():
    adapter, mock_service, mock_table = _make_adapter()
    mock_table.query_entities.side_effect = Exception("timeout")
    assert adapter.query_hourly_events("ls-01", 24) == []
    assert adapter.query_hourly_jvm("ls-01", 24) == []
    assert adapter.query_hourly_pipeline("ls-01", "main", 24) == []
