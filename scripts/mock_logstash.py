#!/usr/bin/env python3
"""
Mock Logstash monitoring API server for local development.

Usage:
    python scripts/mock_logstash.py [PORT] [STATUS]

    PORT   — port to listen on (default: 9601)
    STATUS — node status: green, yellow, or red (default: green)

Run multiple instances on different ports to simulate a cluster:
    python scripts/mock_logstash.py 9601 green
    python scripts/mock_logstash.py 9602 yellow

Then start the app:
    LOGSTASH_SERVERS='[{"name":"ls-01","url":"http://127.0.0.1:9601"},{"name":"ls-02","url":"http://127.0.0.1:9602"}]' \
      flask --app app run --debug
"""

import sys
from flask import Flask, jsonify

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9601
STATUS = sys.argv[2] if len(sys.argv) > 2 else "green"

app = Flask(__name__)


@app.get("/")
def info():
    return jsonify({
        "version": "8.17.1",
        "name": f"mock-ls-{PORT}",
        "status": STATUS,
        "http_address": f"127.0.0.1:{PORT}",
    })


@app.get("/_node/stats")
def stats():
    heap_percent = {"green": 45, "yellow": 82, "red": 96}[STATUS]
    cpu_percent = {"green": 10, "yellow": 55, "red": 90}[STATUS]
    pipeline_state = "stopped" if STATUS == "red" else "running"
    reload_failures = 1 if STATUS == "yellow" else 0

    return jsonify({
        "status": STATUS,
        "jvm": {
            "uptime_in_millis": 7_200_000,
            "mem": {
                "heap_used_percent": heap_percent,
                "heap_used_in_bytes": int(1_073_741_824 * heap_percent / 100),
                "heap_max_in_bytes": 1_073_741_824,
            },
            "threads": {"count": 55},
            "gc": {
                "collectors": {
                    "old": {"collection_count": 3, "collection_time_in_millis": 120},
                    "young": {"collection_count": 210, "collection_time_in_millis": 900},
                }
            },
        },
        "process": {
            "cpu": {"percent": cpu_percent},
            "open_file_descriptors": 180,
            "max_file_descriptors": 65535,
        },
        "events": {
            "in": 5_000_000,
            "out": 4_999_500,
            "filtered": 4_999_500,
            "duration_in_millis": 2_000_000,
        },
        "pipelines": {
            "firewall-logs": {
                "events": {
                    "in": 3_000_000,
                    "out": 2_999_800,
                    "filtered": 2_999_800,
                    "duration_in_millis": 1_200_000,
                },
                "reloads": {"successes": 1, "failures": reload_failures},
                "queue": {"type": "persisted", "events_count": 12},
            },
            "switch-logs": {
                "events": {
                    "in": 2_000_000,
                    "out": 1_999_700,
                    "filtered": 1_999_700,
                    "duration_in_millis": 800_000,
                },
                "reloads": {"successes": 0, "failures": 0},
                "queue": {"type": "memory", "events_count": 3},
            },
        },
        "reloads": {"successes": 1, "failures": reload_failures},
        "os": {
            "cpu": {"percent": cpu_percent},
            "load_average": {"1m": 0.8, "5m": 0.6, "15m": 0.5},
        },
    })


@app.get("/_node/hot_threads")
def hot_threads():
    return jsonify({
        "hot_threads": {
            "time": "2026-05-19T12:00:00",
            "busiest_threads": 2,
            "threads": [
                {
                    "name": "LogStash::Runner",
                    "percent_of_cpu_time": 18.5,
                    "state": "timed_waiting",
                    "traces": [
                        "mock.Thread.run(Thread.java:1)",
                        "mock.Runner.call(Runner.java:42)",
                    ],
                },
                {
                    "name": "[main]<firewall-logs",
                    "percent_of_cpu_time": 11.2,
                    "state": "waiting",
                    "traces": [
                        "mock.Worker.execute(Worker.java:88)",
                        "mock.Pipeline.run(Pipeline.java:205)",
                    ],
                },
            ],
        }
    })


if __name__ == "__main__":
    print(f"Mock Logstash ({STATUS}) listening on http://127.0.0.1:{PORT}")
    app.run(port=PORT, debug=False)
