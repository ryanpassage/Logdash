import time
from logdash.snapshot import ServerSnapshot


def _make_stats(events_in=1000, events_out=900):
    return {
        "status": "green",
        "events": {"in": events_in, "filtered": events_in, "out": events_out},
        "jvm": {"uptime_in_millis": 60_000},
    }


def test_ensure_entry_creates_unknown_state():
    snap = ServerSnapshot()
    snap.ensure_entry("ls-01")
    data = snap.get("ls-01")
    assert data["name"] == "ls-01"
    assert data["reachable"] is None


def test_update_marks_reachable():
    snap = ServerSnapshot()
    snap.update("ls-01", {"version": "8.17"}, _make_stats())
    data = snap.get("ls-01")
    assert data["reachable"] is True
    assert data["last_seen"] is not None


def test_mark_unreachable():
    snap = ServerSnapshot()
    snap.update("ls-01", {}, _make_stats())
    snap.mark_unreachable("ls-01")
    data = snap.get("ls-01")
    assert data["reachable"] is False


def test_rate_is_zero_on_first_poll():
    snap = ServerSnapshot()
    snap.update("ls-01", {}, _make_stats(1000))
    data = snap.get("ls-01")
    # No previous sample → rate should be 0
    assert data["events_in"] == 0.0
    assert data["events_out"] == 0.0


def test_rate_computed_on_second_poll():
    snap = ServerSnapshot()
    snap.update("ls-01", {}, _make_stats(events_in=0, events_out=0))
    time.sleep(0.1)
    snap.update("ls-01", {}, _make_stats(events_in=100, events_out=80))
    data = snap.get("ls-01")
    # Rate should be positive after delta
    assert data["events_in"] > 0
    assert data["events_out"] > 0


def test_get_all_returns_copy():
    snap = ServerSnapshot()
    snap.ensure_entry("ls-01")
    snap.ensure_entry("ls-02")
    all_data = snap.get_all()
    assert "ls-01" in all_data
    assert "ls-02" in all_data
    # Mutating the result should not affect the snapshot
    all_data["ls-01"]["reachable"] = "mutated"
    assert snap.get("ls-01")["reachable"] != "mutated"


def test_negative_delta_clamped_to_zero():
    snap = ServerSnapshot()
    snap.update("ls-01", {}, _make_stats(events_in=500))
    time.sleep(0.1)
    # Counter reset (e.g. Logstash restart) — should not produce negative rate
    snap.update("ls-01", {}, _make_stats(events_in=0))
    data = snap.get("ls-01")
    assert data["events_in"] >= 0.0
