import json
import logging
import os

logger = logging.getLogger(__name__)

# Required environment variables:
#   LOGSTASH_SERVERS  - JSON array of {"name": str, "url": str}
#
# Optional environment variables (defaults in parentheses):
#   AZURE_STORAGE_CONNECTION_STRING  - Needed for Phase 2 persistence
#   LOGDASH_POLL_INTERVAL_SECONDS    (10)
#   LOGDASH_SAMPLE_INTERVAL_SECONDS  (60)
#   LOGDASH_SAMPLE_RETENTION_DAYS    (30)
#   LOGDASH_ROLLUP_RETENTION_DAYS    (365)
#   LOGDASH_HTTP_TIMEOUT_SECONDS     (5)
#   PORT                             (5000) — set automatically by Azure App Service


def _load_servers():
    raw = os.environ.get("LOGSTASH_SERVERS", "[]")
    try:
        servers = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("LOGSTASH_SERVERS is not valid JSON: %s", exc)
        return []
    if not isinstance(servers, list):
        logger.error("LOGSTASH_SERVERS must be a JSON array")
        return []
    validated = []
    for entry in servers:
        if isinstance(entry, dict) and "name" in entry and "url" in entry:
            validated.append({"name": entry["name"], "url": entry["url"].rstrip("/")})
        else:
            logger.warning("Skipping invalid LOGSTASH_SERVERS entry: %s", entry)
    return validated


class Config:
    SERVERS = _load_servers()
    POLL_INTERVAL = int(os.environ.get("LOGDASH_POLL_INTERVAL_SECONDS", "10"))
    SAMPLE_INTERVAL = int(os.environ.get("LOGDASH_SAMPLE_INTERVAL_SECONDS", "60"))
    SAMPLE_RETENTION_DAYS = int(os.environ.get("LOGDASH_SAMPLE_RETENTION_DAYS", "30"))
    ROLLUP_RETENTION_DAYS = int(os.environ.get("LOGDASH_ROLLUP_RETENTION_DAYS", "365"))
    HTTP_TIMEOUT = int(os.environ.get("LOGDASH_HTTP_TIMEOUT_SECONDS", "5"))
    STORAGE_CONNECTION_STRING = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    PORT = int(os.environ.get("PORT", "5000"))
