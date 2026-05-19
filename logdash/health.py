_ORDER = {"green": 0, "yellow": 1, "red": 2, "unknown": -1}


def compute_health(server_data: dict) -> dict:
    """
    Derive a health status from a server snapshot entry.
    Returns {"status": "green"|"yellow"|"red"|"unknown", "reasons": [str]}
    """
    if server_data.get("reachable") is None:
        return {"status": "unknown", "reasons": ["Awaiting first poll"]}

    if not server_data.get("reachable"):
        return {"status": "red", "reasons": ["Server unreachable"]}

    stats = server_data.get("stats") or {}
    worst = "green"
    reasons: list[str] = []

    # Logstash self-reported status
    ls_status = (stats.get("status") or "").lower()
    if ls_status == "red":
        worst = "red"
        reasons.append("Logstash reports status: red")
    elif ls_status == "yellow":
        worst = _escalate(worst, "yellow")
        reasons.append("Logstash reports status: yellow")

    # JVM heap pressure
    mem = (stats.get("jvm") or {}).get("mem") or {}
    heap_used = mem.get("heap_used_in_bytes") or 0
    heap_max = mem.get("heap_max_in_bytes") or 1
    heap_pct = (heap_used / heap_max) * 100
    if heap_pct >= 95:
        worst = "red"
        reasons.append(f"JVM heap critical: {heap_pct:.0f}%")
    elif heap_pct >= 80:
        worst = _escalate(worst, "yellow")
        reasons.append(f"JVM heap high: {heap_pct:.0f}%")

    # Per-pipeline reload failures
    for pid, pdata in (stats.get("pipelines") or {}).items():
        reloads = (pdata or {}).get("reloads") or {}
        if reloads.get("failures", 0) > 0 and reloads.get("last_failure_timestamp"):
            worst = _escalate(worst, "yellow")
            reasons.append(f"Pipeline '{pid}' has reload failures")

    # Node-level reload failures
    node_reloads = stats.get("reloads") or {}
    if node_reloads.get("failures", 0) > 0 and not reasons:
        worst = _escalate(worst, "yellow")
        reasons.append("Node has pipeline reload failures")

    if not reasons:
        reasons.append("All systems normal")

    return {"status": worst, "reasons": reasons}


def _escalate(current: str, candidate: str) -> str:
    if _ORDER.get(candidate, 0) > _ORDER.get(current, 0):
        return candidate
    return current
