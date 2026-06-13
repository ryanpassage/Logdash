import threading
import time
from datetime import datetime, timezone


class ServerSnapshot:
    """Thread-safe in-memory cache of the latest polled state for all servers."""

    def __init__(self):
        self._lock = threading.RLock()
        self._data: dict[str, dict] = {}
        self._prev: dict[str, dict] = {}

    def ensure_entry(self, name: str) -> None:
        """Pre-populate an entry so the dashboard shows the server immediately."""
        with self._lock:
            if name not in self._data:
                self._data[name] = _empty_entry(name)

    def update(
        self,
        name: str,
        info: dict | None,
        stats: dict | None,
        health_report: dict | None = None,
    ) -> None:
        now = time.monotonic()
        wall = datetime.now(timezone.utc).isoformat()
        with self._lock:
            rate = self._compute_rate(name, stats, now)
            self._prev[name] = {
                "events_in": _dig(stats, "events", "in"),
                "events_out": _dig(stats, "events", "out"),
                "events_filtered": _dig(stats, "events", "filtered"),
                "ts": now,
            }
            self._data[name] = {
                "name": name,
                "reachable": True,
                "last_seen": wall,
                "last_seen_ts": now,
                "info": info or {},
                "stats": stats or {},
                "health_report": health_report or {},
                "events_in": rate["in"],
                "events_out": rate["out"],
                "events_filtered": rate["filtered"],
            }

    def mark_unreachable(self, name: str) -> None:
        with self._lock:
            existing = self._data.get(name, _empty_entry(name))
            existing["reachable"] = False
            existing["events_in"] = 0.0
            existing["events_out"] = 0.0
            existing["events_filtered"] = 0.0
            self._data[name] = existing

    def get(self, name: str) -> dict:
        with self._lock:
            return dict(self._data.get(name, _empty_entry(name)))

    def get_all(self) -> dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._data.items()}

    def _compute_rate(self, name: str, stats: dict | None, now: float) -> dict:
        zero = {"in": 0.0, "out": 0.0, "filtered": 0.0}
        if not stats:
            return zero
        prev = self._prev.get(name)
        if not prev or prev.get("ts") is None:
            return zero
        dt = now - prev["ts"]
        if dt < 0.01:
            return zero
        cur_in = _dig(stats, "events", "in")
        cur_out = _dig(stats, "events", "out")
        cur_filtered = _dig(stats, "events", "filtered")
        return {
            "in": max(0.0, (cur_in - prev["events_in"]) / dt),
            "out": max(0.0, (cur_out - prev["events_out"]) / dt),
            "filtered": max(0.0, (cur_filtered - prev["events_filtered"]) / dt),
        }


def _dig(d: dict | None, *keys) -> float:
    if d is None:
        return 0.0
    for k in keys:
        if not isinstance(d, dict):
            return 0.0
        d = d.get(k, 0)
    return float(d or 0)


def _empty_entry(name: str) -> dict:
    return {
        "name": name,
        "reachable": None,
        "last_seen": None,
        "last_seen_ts": None,
        "info": {},
        "stats": {},
        "health_report": {},
        "events_in": 0.0,
        "events_out": 0.0,
        "events_filtered": 0.0,
    }
