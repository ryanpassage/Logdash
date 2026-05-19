import responses as resp_mock
import pytest
from logdash.logstash_client import LogstashClient


BASE = "http://ls-test:9600"


@pytest.fixture
def client():
    return LogstashClient("test", BASE, timeout=2)


@resp_mock.activate
def test_get_info_success(client):
    resp_mock.add(resp_mock.GET, f"{BASE}/", json={"version": "8.17.1", "status": "green"})
    data = client.get_info()
    assert data["version"] == "8.17.1"


@resp_mock.activate
def test_get_stats_success(client):
    payload = {"status": "green", "jvm": {"uptime_in_millis": 123456}}
    resp_mock.add(resp_mock.GET, f"{BASE}/_node/stats", json=payload)
    data = client.get_stats()
    assert data["jvm"]["uptime_in_millis"] == 123456


@resp_mock.activate
def test_returns_none_on_connection_error(client):
    # No mocked route → requests raises ConnectionError
    data = client.get_info()
    assert data is None


@resp_mock.activate
def test_returns_none_on_http_error(client):
    resp_mock.add(resp_mock.GET, f"{BASE}/", status=500)
    data = client.get_info()
    assert data is None


@resp_mock.activate
def test_fetch_all_returns_tuple(client):
    resp_mock.add(resp_mock.GET, f"{BASE}/", json={"version": "8.17.1"})
    resp_mock.add(resp_mock.GET, f"{BASE}/_node/stats", json={"status": "green"})
    info, stats = client.fetch_all()
    assert info["version"] == "8.17.1"
    assert stats["status"] == "green"


@resp_mock.activate
def test_fetch_all_returns_none_tuple_on_failure(client):
    # Nothing registered → both fail
    info, stats = client.fetch_all()
    assert info is None
    assert stats is None
