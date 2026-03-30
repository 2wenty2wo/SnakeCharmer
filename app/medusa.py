import logging

import requests

from app.config import MedusaConfig

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30


class MedusaClient:
    def __init__(self, config: MedusaConfig):
        self.base_url = f"{config.url}/api/v2"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "x-api-key": config.api_key,
            }
        )

    def get_existing_tvdb_ids(self) -> set[int]:
        """Fetch all existing show TVDB IDs from Medusa."""
        resp = self._request("GET", "/series")
        series_list = resp.json()

        tvdb_ids = set()
        for series in series_list:
            tvdb_id = series.get("id", {}).get("tvdb")
            if tvdb_id:
                tvdb_ids.add(int(tvdb_id))

        log.info("Found %d existing shows in Medusa", len(tvdb_ids))
        return tvdb_ids

    def add_show(self, tvdb_id: int, title: str) -> bool:
        """Add a show to Medusa by TVDB ID.

        Returns True if added, False if already exists.
        """
        try:
            self._request(
                "POST",
                "/series",
                json={
                    "id": {"tvdb": tvdb_id},
                },
            )
            log.info("Added: %s (tvdb:%d)", title, tvdb_id)
            return True
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 409:
                log.debug("Already exists: %s (tvdb:%d)", title, tvdb_id)
                return False
            raise

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Make an HTTP request to the Medusa API."""
        url = f"{self.base_url}{path}"
        try:
            kwargs.setdefault("timeout", REQUEST_TIMEOUT)
            resp = self.session.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.ConnectionError:
            log.error("Cannot reach Medusa at %s - is it running?", self.base_url)
            raise
