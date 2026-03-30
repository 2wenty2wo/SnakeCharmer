import logging

import requests

from app.config import MedusaConfig

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30

# Medusa individual quality values (bitmask flags)
_QUALITY_VALUES = {
    "na": 0,
    "unknown": 1,
    "sdtv": 2,
    "sddvd": 4,
    "hdtv": 8,
    "rawhdtv": 16,
    "fullhdtv": 32,
    "hdwebdl": 64,
    "fullhdwebdl": 128,
    "hdbluray": 256,
    "fullhdbluray": 512,
    "uhd4ktv": 1024,
    "uhd4kwebdl": 2048,
    "uhd4kbluray": 4096,
    "uhd8ktv": 8192,
    "uhd8kwebdl": 16384,
    "uhd8kbluray": 32768,
}

# Medusa quality presets (bitmask combinations of individual values)
_QUALITY_PRESETS = {
    "any": 65518,
    "sd": 6,
    "hd": 1000,
    "hd720p": 328,
    "hd1080p": 672,
    "uhd": 64512,
    "uhd4k": 7168,
    "uhd8k": 57344,
}

# Combined lookup: name → bitmask value
_QUALITY_BY_NAME = {**_QUALITY_VALUES, **_QUALITY_PRESETS}


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

    def add_show(self, tvdb_id: int, title: str, add_options: dict | None = None) -> bool:
        """Add a show to Medusa by TVDB ID.

        Returns True if added, False if already exists.
        """
        payload: dict = {"id": {"tvdb": tvdb_id}}

        if add_options:
            config: dict = {}
            quality = add_options.get("quality")
            if quality is not None:
                config["qualities"] = {
                    "allowed": resolve_quality(quality),
                    "preferred": [],
                }
            required_words = add_options.get("required_words")
            if required_words:
                config["release"] = {"requiredWords": required_words}
            if config:
                payload["config"] = config

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
