import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class LogstashClient:
    """HTTP client for a single Logstash node's monitoring API."""

    def __init__(self, name: str, url: str, timeout: int = 5):
        self.name = name
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._session = self._build_session()

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({"Accept": "application/json"})
        retry = Retry(total=1, backoff_factor=0.2, status_forcelist=[502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _get(self, path: str) -> dict | None:
        try:
            resp = self._session.get(f"{self.url}{path}", timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ConnectionError:
            logger.warning("Cannot connect to %s (%s)", self.name, self.url)
        except requests.exceptions.Timeout:
            logger.warning("Timeout fetching %s%s", self.url, path)
        except requests.exceptions.RequestException as exc:
            logger.warning("Error fetching %s%s: %s", self.url, path, exc)
        return None

    def get_info(self) -> dict | None:
        """GET / — basic node info: version, name, status."""
        return self._get("/")

    def get_stats(self) -> dict | None:
        """GET /_node/stats — jvm, process, events, pipelines, os, reloads."""
        return self._get("/_node/stats")

    def get_hot_threads(self) -> dict | None:
        """GET /_node/hot_threads — on-demand, used in drill-down view."""
        return self._get("/_node/hot_threads?human=true")

    def fetch_all(self) -> tuple[dict | None, dict | None]:
        """Fetch both info and stats for the dashboard collector."""
        info = self.get_info()
        stats = self.get_stats()
        return info, stats
