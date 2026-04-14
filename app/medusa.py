import logging

import requests

from app.config import (
    _QUALITY_BY_NAME,
    _QUALITY_VALUES,
    MedusaConfig,
)
from app.http_client import REQUEST_TIMEOUT, RetryClient

# Re-export for backward compatibility
__all__ = ["MedusaClient", "REQUEST_TIMEOUT", "resolve_quality"]

log = logging.getLogger(__name__)


def _bitmask_to_quality_list(bitmask: int) -> list[int]:
    """Decompose a bitmask into a sorted list of individual quality values."""
    return sorted(v for v in _QUALITY_VALUES.values() if v and bitmask & v)


def resolve_quality(quality: str | list[str]) -> list[int]:
    """Resolve a quality name (or list of names) into a list of Medusa quality integers.

    Accepts individual quality keys (e.g. "hdtv"), preset keys (e.g. "hd720p"),
    or a list of either. Returns a sorted, deduplicated list of individual
    quality integer values suitable for Medusa's config.qualities.allowed field.
    """
    names = [quality] if isinstance(quality, str) else quality
    combined_bitmask = 0
    for name in names:
        key = name.strip().lower()
        if key not in _QUALITY_BY_NAME:
            raise ValueError(
                f"Unknown Medusa quality '{name}'. "
                f"Valid values: {', '.join(sorted(_QUALITY_BY_NAME))}"
            )
        combined_bitmask |= _QUALITY_BY_NAME[key]
    return _bitmask_to_quality_list(combined_bitmask)


class MedusaClient(RetryClient):
    _service_name = "Medusa"

    def __init__(self, config: MedusaConfig, max_retries: int = 3, retry_backoff: float = 2.0):
        self._config_url = config.url
        session = requests.Session()
        session.headers.update(
            {
                "Content-Type": "application/json",
                "x-api-key": config.api_key,
            }
        )
        super().__init__(
            session=session,
            base_url=f"{config.url}/api/v2",
            max_retries=max_retries,
            retry_backoff=retry_backoff,
        )

    def get_existing_tvdb_ids(self) -> set[int]:
        """Fetch all existing show TVDB IDs from Medusa."""
        resp = self._request("GET", "/series")
        series_list = resp.json()

        tvdb_ids = set()
        for series in series_list:
            tvdb_id = series.get("id", {}).get("tvdb")
            if tvdb_id:
                try:
                    tvdb_ids.add(int(tvdb_id))
                except (ValueError, TypeError):
                    log.warning("Skipping series with malformed TVDB ID: %r", tvdb_id)

        log.info("Found %d existing shows in Medusa", len(tvdb_ids))
        return tvdb_ids

    def get_series_list(self) -> list[dict]:
        """Fetch all series from Medusa with display info."""
        resp = self._request("GET", "/series")
        series_list = resp.json()

        shows = []
        for series in series_list:
            tvdb_id = series.get("id", {}).get("tvdb")
            if not tvdb_id:
                continue
            try:
                tvdb_id_int = int(tvdb_id)
            except (ValueError, TypeError):
                log.warning("Skipping series with malformed TVDB ID: %r", tvdb_id)
                continue
            year = series.get("year")
            if isinstance(year, dict):
                year = year.get("start") or year.get("end")
            shows.append(
                {
                    "title": series.get("title", "Unknown"),
                    "tvdb_id": tvdb_id_int,
                    "imdb_id": series.get("id", {}).get("imdb"),
                    "year": year,
                    "status": series.get("status"),
                    "network": series.get("network"),
                }
            )
        shows.sort(key=lambda s: s["title"].lower())
        log.info("Fetched %d shows from Medusa library", len(shows))
        return shows

    def add_show(self, tvdb_id: int, title: str, add_options: dict | None = None) -> bool:
        """Add a show to Medusa by TVDB ID.

        Returns True if added, False if already exists.
        """
        payload: dict = {"id": {"tvdb": tvdb_id}}

        if add_options:
            options: dict = {}
            quality = add_options.get("quality")
            if quality is not None:
                options["quality"] = {
                    "allowed": resolve_quality(quality),
                    "preferred": [],
                }
            required_words = add_options.get("required_words")
            if required_words:
                options["release"] = {"requiredWords": required_words}
            if options:
                payload["options"] = options

        try:
            self._request(
                "POST",
                "/series",
                json=payload,
            )
            log.info("Added: %s (tvdb:%d)", title, tvdb_id)
            return True
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 409:
                log.debug("Already exists: %s (tvdb:%d)", title, tvdb_id)
                return False
            raise

    def _on_connection_exhausted(self, exc: requests.ConnectionError) -> None:
        log.error("Cannot reach Medusa at %s - is it running?", self._config_url)
