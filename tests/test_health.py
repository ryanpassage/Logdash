from logdash.health import compute_health


def _base_data(status="green", heap_used=500_000_000, heap_max=1_000_000_000, pipelines=None):
    return {
        "reachable": True,
        "stats": {
            "status": status,
            "jvm": {"mem": {"heap_used_in_bytes": heap_used, "heap_max_in_bytes": heap_max}},
            "pipelines": pipelines or {},
            "reloads": {"successes": 0, "failures": 0},
        },
    }


def test_green_when_healthy():
    result = compute_health(_base_data())
    assert result["status"] == "green"


def test_unknown_when_no_data():
    result = compute_health({"reachable": None, "stats": {}})
    assert result["status"] == "unknown"


def test_red_when_unreachable():
    result = compute_health({"reachable": False, "stats": {}})
    assert result["status"] == "red"
    assert "unreachable" in result["reasons"][0].lower()


def test_red_when_logstash_reports_red():
    result = compute_health(_base_data(status="red"))
    assert result["status"] == "red"


def test_yellow_when_logstash_reports_yellow():
    result = compute_health(_base_data(status="yellow"))
    assert result["status"] == "yellow"


def test_yellow_when_heap_above_80():
    data = _base_data(heap_used=850_000_000, heap_max=1_000_000_000)
    result = compute_health(data)
    assert result["status"] == "yellow"
    assert "heap" in result["reasons"][0].lower()


def test_red_when_heap_above_95():
    data = _base_data(heap_used=970_000_000, heap_max=1_000_000_000)
    result = compute_health(data)
    assert result["status"] == "red"
    assert "critical" in result["reasons"][0].lower()


def test_yellow_when_pipeline_has_reload_failure():
    pipelines = {
        "main": {
            "reloads": {
                "failures": 2,
                "last_failure_timestamp": "2026-01-01T00:00:00Z",
                "successes": 0,
            }
        }
    }
    result = compute_health(_base_data(pipelines=pipelines))
    assert result["status"] == "yellow"
    assert "main" in result["reasons"][0]


def test_green_no_false_positives_on_empty_pipelines():
    data = _base_data(pipelines={"main": {"reloads": {"failures": 0, "successes": 5, "last_failure_timestamp": None}}})
    result = compute_health(data)
    assert result["status"] == "green"


def test_red_beats_yellow():
    data = _base_data(status="yellow", heap_used=970_000_000, heap_max=1_000_000_000)
    result = compute_health(data)
    assert result["status"] == "red"


# ── Health Report API ────────────────────────────────────────────────────────

def _report(status, cause, pipeline_id="firewall-logs"):
    return {
        "status": status,
        "symptom": "1 indicator is unhealthy (`pipelines`)",
        "indicators": {
            "pipelines": {
                "status": status,
                "symptom": f"1 indicator is unhealthy (`{pipeline_id}`)",
                "indicators": {
                    pipeline_id: {
                        "status": status,
                        "symptom": "The pipeline is unhealthy",
                        "diagnosis": [{"cause": cause, "action": "check the logs"}],
                    }
                },
            }
        },
    }


def test_health_report_surfaces_pipeline_diagnosis():
    data = _base_data()
    data["health_report"] = _report("red", "pipeline is not running")
    result = compute_health(data)
    assert result["status"] == "red"
    assert any("firewall-logs" in r and "not running" in r for r in result["reasons"])


def test_health_report_drives_status_without_stats_status():
    # No status in /_node/stats; the health report alone should make it yellow.
    data = _base_data(status="")
    data["health_report"] = _report("yellow", "experiencing backpressure")
    result = compute_health(data)
    assert result["status"] == "yellow"
    assert any("backpressure" in r for r in result["reasons"])


def test_health_report_falls_back_to_symptom_without_diagnosis():
    data = _base_data(status="")
    data["health_report"] = {
        "status": "red",
        "symptom": "node-level failure",
        "indicators": {},
    }
    result = compute_health(data)
    assert result["status"] == "red"
    assert "node-level failure" in result["reasons"]


def test_green_health_report_leaves_status_green():
    data = _base_data(status="")
    data["health_report"] = {"status": "green", "symptom": "all good", "indicators": {}}
    result = compute_health(data)
    assert result["status"] == "green"
