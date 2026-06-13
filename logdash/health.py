_ORDER = {"green": 0, "yellow": 1, "red": 2, "unknown": -1}


def compute_health(server_data: dict) -> dict:
    """
    Derive a health status from a server snapshot entry.
    Returns {"status": "green"|"yellow"|"red"|"unknown", "reasons": [str]}

    The Logstash Health Report API (`/_health_report`, stored under
    ``health_report``) is the richest source — it carries per-pipeline symptoms
    and diagnoses, so we mine it first. Older nodes that don't expose it fall
    back to the heuristics derived from ``/_node/stats``.
    """
    if server_data.get("reachable") is None:
        return {"status": "unknown", "reasons": ["Awaiting first poll"]}

    if not server_data.get("reachable"):
        return {"status": "red", "reasons": ["Server unreachable"]}

    stats = server_data.get("stats") or {}
    report = server_data.get("health_report") or {}
    worst = "green"
    reasons: list[str] = []

    # 1. Logstash Health Report API — authoritative, with pipeline-level detail.
    report_status = (report.get("status") or "").lower()
    have_report = report_status in ("yellow", "red")
    if have_report:
        worst = _escalate(worst, report_status)
        detail = _health_report_reasons(report)
        if detail:
            reasons.extend(detail)
        elif report.get("symptom"):
            reasons.append(report["symptom"])
        else:
            reasons.append(f"Logstash health report: {report_status}")

    # 2. Fall back to the /_node/stats status field when no health report exists.
    if not have_report:
        ls_status = (stats.get("status") or "").lower()
        if ls_status == "red":
            worst = "red"
            reasons.append("Logstash reports status: red")
        elif ls_status == "yellow":
            worst = _escalate(worst, "yellow")
            reasons.append("Logstash reports status: yellow")

    # 3. JVM heap pressure — always evaluated (not always covered by the report).
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

    # 4. Per-pipeline reload failures from /_node/stats — only when the health
    #    report didn't already surface pipeline-level reasons (avoids duplicates).
    if not have_report:
        for pid, pdata in (stats.get("pipelines") or {}).items():
            reloads = (pdata or {}).get("reloads") or {}
            if reloads.get("failures", 0) > 0 and reloads.get("last_failure_timestamp"):
                worst = _escalate(worst, "yellow")
                reasons.append(f"Pipeline '{pid}' has reload failures")

        node_reloads = stats.get("reloads") or {}
        if node_reloads.get("failures", 0) > 0 and not reasons:
            worst = _escalate(worst, "yellow")
            reasons.append("Node has pipeline reload failures")

    reasons = _dedupe(reasons)
    if not reasons:
        reasons.append("All systems normal")

    return {"status": worst, "reasons": reasons}


def _health_report_reasons(report: dict) -> list[str]:
    """Walk the Health Report indicator tree and collect human-readable reasons
    from the unhealthy leaf indicators (e.g. per-pipeline diagnoses)."""
    reasons: list[str] = []
    _walk_indicators([], report.get("indicators") or {}, reasons)
    return reasons


def _walk_indicators(path: list[str], indicators: dict, reasons: list[str]) -> None:
    for name, node in (indicators or {}).items():
        if not isinstance(node, dict):
            continue
        status = (node.get("status") or "").lower()
        if status in ("green", "", "unknown"):
            continue

        children = node.get("indicators") or {}
        if children:
            _walk_indicators(path + [name], children, reasons)
            continue

        # Unhealthy leaf indicator: prefer concrete diagnoses, else the symptom.
        label = _indicator_label(path + [name])
        causes = [
            d.get("cause")
            for d in (node.get("diagnosis") or [])
            if isinstance(d, dict) and d.get("cause")
        ]
        if causes:
            reasons.extend(f"{label}: {cause}" for cause in causes)
        elif node.get("symptom"):
            reasons.append(f"{label}: {node['symptom']}")
        else:
            reasons.append(f"{label}: status {status}")


def _indicator_label(path: list[str]) -> str:
    """Human-friendly label for an indicator path, e.g. ['pipelines','main'] →
    "Pipeline 'main'"."""
    if len(path) >= 2 and path[0] == "pipelines":
        return f"Pipeline '{path[-1]}'"
    return path[-1].replace("_", " ").capitalize() if path else "Node"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _escalate(current: str, candidate: str) -> str:
    if _ORDER.get(candidate, 0) > _ORDER.get(current, 0):
        return candidate
    return current
