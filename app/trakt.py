import json
import logging
import os
import time
from dataclasses import dataclass, field

import requests

from app.config import TraktConfig, TraktSource
from app.http_client import REQUEST_TIMEOUT, RetryClient

log = logging.getLogger(__name__)

BASE_URL = "https://api.trakt.tv"
TOKEN_FILE = "trakt_token.json"  # nosec B105


@dataclass
class TraktShow:
    title: str
    tvdb_id: int
    imdb_id: str | None = None
    year: int | None = None
    poster_url: str | None = None
    network: str | None = None
    genres: list[str] = field(default_factory=list)


class TraktClient(RetryClient):
    _service_name = "Trakt"

    def __init__(
        self,
        config: TraktConfig,
        config_dir: str = ".",
        max_retries: int = 3,
        retry_backoff: float = 2.0,
    ):
        self.config = config
        self.token_path = os.path.join(config_dir, TOKEN_FILE)
        session = requests.Session()
        session.headers.update(
            {
                "Content-Type": "application/json",
                "trakt-api-version": "2",
                "trakt-api-key": config.client_id,
            }
        )
        super().__init__(
            session=session,
            base_url=BASE_URL,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
        )

    def get_shows(self, source: TraktSource | str) -> list[TraktShow]:
        """Fetch shows from a configured Trakt source."""
        normalized = self._normalize_source(source)
        source_name = normalized.label

        if normalized.type == "trending":
            return self._fetch_public("/shows/trending", source_name, nested_key="show")
        if normalized.type == "popular":
            return self._fetch_public("/shows/popular", source_name)
        if normalized.type == "watched":
            return self._fetch_public("/shows/watched/weekly", source_name, nested_key="show")
        if normalized.type == "watchlist":
            self._ensure_auth()
            return self._fetch_user_list(
                f"/users/{self.config.username}/watchlist/shows",
                source_name,
                nested_key="show",
            )
        if normalized.type == "user_list":
            if normalized.requires_auth:
                self._ensure_auth()
            return self._fetch_user_list(
                f"/users/{normalized.owner}/lists/{normalized.list_slug}/items/shows",
                source_name,
                nested_key="show",
            )

        raise ValueError(f"Unsupported Trakt source type: {normalized.type}")

    def _normalize_source(self, source: TraktSource | str) -> TraktSource:
        if isinstance(source, TraktSource):
            return source
        list_name = str(source).strip()
        if list_name in {"trending", "popular", "watched", "watchlist"}:
            return TraktSource(type=list_name)
        return TraktSource(
            type="user_list",
            owner=self.config.username,
            list_slug=list_name,
            # When a bare string is provided we treat it as a user list owned by the
            # configured user. Default to requiring auth so private lists work.
            auth=True,
        )

    def _fetch_shows(
        self,
        path: str,
        source_list: str,
        nested_key: str | None = None,
        limit: int | None = None,
    ) -> list[TraktShow]:
        """Fetch shows from a Trakt endpoint with pagination.

        When *limit* is given, at most that many shows are returned and the page
        size is capped accordingly.  When *limit* is ``None`` all pages are
        fetched.
        """
        page_size = min(limit, 100) if limit is not None else 100
        params: dict = {"limit": page_size, "page": 1, "extended": "full"}
        shows: list[TraktShow] = []

        while limit is None or len(shows) < limit:
            resp = self._request("GET", path, params=params)
            items = resp.json()
            if not items:
                break

            for item in items:
                show_data = item.get(nested_key, item) if nested_key else item
                show = self._parse_show(show_data)
                if show:
                    shows.append(show)

            try:
                page_count = int(resp.headers.get("X-Pagination-Page-Count", 1))
            except (ValueError, TypeError):
                page_count = 1
            if params["page"] >= page_count:
                break
            params["page"] += 1

        if limit is not None:
            shows = shows[:limit]
        log.info("Fetched %d shows from Trakt list '%s'", len(shows), source_list)
        return shows

    def _fetch_public(
        self, path: str, source_list: str, nested_key: str | None = None
    ) -> list[TraktShow]:
        """Fetch shows from a public Trakt endpoint with limit support."""
        return self._fetch_shows(path, source_list, nested_key, limit=self.config.limit)

    def _fetch_user_list(
        self, path: str, source_list: str, nested_key: str | None = None
    ) -> list[TraktShow]:
        """Fetch shows from a user-specific Trakt endpoint with pagination."""
        return self._fetch_shows(path, source_list, nested_key, limit=None)

    def _parse_show(self, data: dict) -> TraktShow | None:
        """Parse a show object from Trakt API response."""
        ids = data.get("ids", {})
        tvdb_id = ids.get("tvdb")
        title = data.get("title", "Unknown")

        if not tvdb_id:
            log.warning("Skipping '%s' - no TVDB ID available", title)
            return None

        try:
            tvdb_id_int = int(tvdb_id)
        except (ValueError, TypeError):
            log.warning("Skipping '%s' - malformed TVDB ID: %r", title, tvdb_id)
            return None

        # Extract poster URL from images
        poster_url = None
        images = data.get("images", {})
        poster_data = images.get("poster")

        # Handle both dict format {thumb: url} and list format [url1, url2, ...]
        if isinstance(poster_data, dict) and "thumb" in poster_data:
            poster_url = poster_data["thumb"]
        elif isinstance(poster_data, list) and poster_data:
            # List of poster entries - handle both string URLs and object entries.
            first_entry = poster_data[0]
            if isinstance(first_entry, str):
                url = first_entry
            elif isinstance(first_entry, dict):
                thumb = first_entry.get("thumb")
                url = thumb if isinstance(thumb, str) else None
            else:
                url = None

            if url and not url.startswith("http"):
                url = f"https://{url}"
            poster_url = url

        # Extract network and genres
        network = data.get("network")
        genres = data.get("genres", [])

        return TraktShow(
            title=title,
            tvdb_id=tvdb_id_int,
            imdb_id=ids.get("imdb"),
            year=data.get("year"),
            poster_url=poster_url,
            network=network,
            genres=genres if genres else [],
        )

    # --- OAuth Device Auth ---

    def _ensure_auth(self) -> None:
        """Ensure we have a valid OAuth token for user endpoints."""
        token = self._load_token()
        if token:
            self.session.headers["Authorization"] = f"Bearer {token['access_token']}"
            return
        self._authenticate()

    def _load_token(self) -> dict | None:
        """Load and validate existing OAuth token."""
        if not os.path.exists(self.token_path):
            return None

        try:
            with open(self.token_path, encoding="utf-8") as f:
                token = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to read token file: %s", e)
            return None

        # Check if token is expired (with 1 hour buffer)
        created_at = token.get("created_at", 0)
        expires_in = token.get("expires_in", 0)
        if time.time() > created_at + expires_in - 3600:
            log.info("Token expired, attempting refresh")
            refreshed = self._refresh_token(token)
            return refreshed

        return token

    def _refresh_token(self, token: dict) -> dict | None:
        """Refresh an expired OAuth token."""
        try:
            resp = self.session.post(
                f"{BASE_URL}/oauth/token",
                timeout=REQUEST_TIMEOUT,
                json={
                    "refresh_token": token.get("refresh_token"),
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                    "grant_type": "refresh_token",
                },
            )
            resp.raise_for_status()
            new_token = resp.json()
            self._save_token(new_token)
            log.info("Token refreshed successfully")
            return new_token
        except requests.RequestException as e:
            log.warning("Token refresh failed: %s", e)
            return None

    def _authenticate(self) -> None:
        """Run the OAuth device code authentication flow."""
        log.info("Starting Trakt device authentication...")

        # Step 1: Get device code
        resp = self._request(
            "POST",
            "/oauth/device/code",
            json={
                "client_id": self.config.client_id,
            },
        )
        device = resp.json()

        user_code = device["user_code"]
        verification_url = device["verification_url"]
        expires_in = int(float(device["expires_in"]))
        interval = int(float(device["interval"]))

        print()
        print("=" * 50)
        print("  Trakt Authentication Required")
        print("=" * 50)
        print(f"  1. Go to: {verification_url}")
        print(f"  2. Enter code: {user_code}")
        print("=" * 50)
        print()

        # Step 2: Poll for authorization
        deadline = time.time() + expires_in
        while time.time() < deadline:
            time.sleep(interval)
            try:
                poll_resp = self.session.post(
                    f"{BASE_URL}/oauth/device/token",
                    timeout=REQUEST_TIMEOUT,
                    json={
                        "code": device["device_code"],
                        "client_id": self.config.client_id,
                        "client_secret": self.config.client_secret,
                    },
                )

                if poll_resp.status_code == 200:
                    token = poll_resp.json()
                    self._save_token(token)
                    self.session.headers["Authorization"] = f"Bearer {token['access_token']}"
                    log.info("Authentication successful!")
                    return
                elif poll_resp.status_code == 400:
                    # Pending - user hasn't authorized yet
                    continue
                elif poll_resp.status_code == 404:
                    log.error("Invalid device code")
                    break
                elif poll_resp.status_code == 409:
                    log.error("Code already used")
                    break
                elif poll_resp.status_code == 410:
                    log.error("Code expired")
                    break
                elif poll_resp.status_code == 418:
                    log.error("User denied the authorization")
                    break
                elif poll_resp.status_code == 429:
                    # Slow down
                    time.sleep(interval)
                    continue
                else:
                    log.error(
                        "Unexpected poll response %d: %s",
                        poll_resp.status_code,
                        poll_resp.text,
                    )
                    break
            except requests.RequestException as e:
                log.warning("Poll request failed: %s", e)
                continue

        log.error("Authentication failed or timed out")
        raise SystemExit(1)

    def _save_token(self, token: dict) -> None:
        """Persist OAuth token to disk."""
        token.setdefault("created_at", int(time.time()))
        with open(self.token_path, "w", encoding="utf-8") as f:
            json.dump(token, f, indent=2)
        log.debug("Token saved to %s", self.token_path)

    # --- HTTP Helpers ---

    def _handle_rate_limit(
        self, resp: requests.Response, method: str, url: str, **kwargs
    ) -> requests.Response | None:
        """Handle Trakt 429 rate limiting using the Retry-After header."""
        if resp.status_code != 429:
            return None
        try:
            retry_after = int(resp.headers.get("Retry-After", 10))
        except (TypeError, ValueError):
            retry_after = 10
        log.warning("Rate limited, waiting %ds", retry_after)
        time.sleep(retry_after)
        resp = self.session.request(method, url, **kwargs)
        if resp.status_code == 429:
            resp.raise_for_status()
        return resp
