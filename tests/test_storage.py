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
