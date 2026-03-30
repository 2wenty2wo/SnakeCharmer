import json
import logging
import os
import time
from dataclasses import dataclass

import requests

from app.config import TraktConfig

log = logging.getLogger(__name__)

BASE_URL = "https://api.trakt.tv"
TOKEN_FILE = "trakt_token.json"
REQUEST_TIMEOUT = 30


@dataclass
class TraktShow:
    title: str
    tvdb_id: int
    imdb_id: str | None = None
    year: int | None = None


class TraktClient:
    def __init__(self, config: TraktConfig, config_dir: str = "."):
        self.config = config
        self.token_path = os.path.join(config_dir, TOKEN_FILE)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "trakt-api-version": "2",
                "trakt-api-key": config.client_id,
            }
        )

    def get_shows(self, target_list: str) -> list[TraktShow]:
        """Fetch shows from a target Trakt list."""
        list_name = target_list

        if list_name == "trending":
            return self._fetch_public("/shows/trending", list_name, nested_key="show")
        elif list_name == "popular":
            return self._fetch_public("/shows/popular", list_name)
        elif list_name == "watched":
            return self._fetch_public("/shows/watched/weekly", list_name, nested_key="show")
        elif list_name == "watchlist":
            self._ensure_auth()
            return self._fetch_user_list(
                f"/users/{self.config.username}/watchlist/shows",
                list_name,
                nested_key="show",
            )
        else:
            # Custom user list
            self._ensure_auth()
            return self._fetch_user_list(
                f"/users/{self.config.username}/lists/{list_name}/items/shows",
                list_name,
                nested_key="show",
            )

    def _fetch_public(
        self, path: str, source_list: str, nested_key: str | None = None
    ) -> list[TraktShow]:
        """Fetch shows from a public Trakt endpoint with limit support."""
        page_size = min(self.config.limit, 100)
        params = {"limit": page_size, "page": 1}
        shows = []

        while len(shows) < self.config.limit:
            resp = self._request("GET", path, params=params)
            items = resp.json()
            if not items:
                break

            for item in items:
                show_data = item.get(nested_key, item) if nested_key else item
                show = self._parse_show(show_data)
                if show:
                    shows.append(show)

            page_count = int(resp.headers.get("X-Pagination-Page-Count", 1))
            if params["page"] >= page_count:
                break
            params["page"] += 1

        shows = shows[: self.config.limit]
        log.info("Fetched %d shows from Trakt list '%s'", len(shows), source_list)
        return shows

    def _fetch_user_list(
        self, path: str, source_list: str, nested_key: str | None = None
    ) -> list[TraktShow]:
        """Fetch shows from a user-specific Trakt endpoint with pagination."""
        params = {"page": 1, "limit": 100}
        shows = []

        while True:
            resp = self._request("GET", path, params=params)
            items = resp.json()
            if not items:
                break

            for item in items:
                show_data = item.get(nested_key, item) if nested_key else item
                show = self._parse_show(show_data)
                if show:
                    shows.append(show)

            page_count = int(resp.headers.get("X-Pagination-Page-Count", 1))
            if params["page"] >= page_count:
                break
            params["page"] += 1

        log.info("Fetched %d shows from Trakt list '%s'", len(shows), source_list)
        return shows

    def _parse_show(self, data: dict) -> TraktShow | None:
        """Parse a show object from Trakt API response."""
        ids = data.get("ids", {})
        tvdb_id = ids.get("tvdb")
        title = data.get("title", "Unknown")

        if not tvdb_id:
            log.warning("Skipping '%s' - no TVDB ID available", title)
            return None

        return TraktShow(
            title=title,
            tvdb_id=int(tvdb_id),
            imdb_id=ids.get("imdb"),
            year=data.get("year"),
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
            with open(self.token_path) as f:
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
        expires_in = device["expires_in"]
        interval = device["interval"]

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
            except requests.RequestException as e:
                log.warning("Poll request failed: %s", e)
                continue

        log.error("Authentication failed or timed out")
        raise SystemExit(1)

    def _save_token(self, token: dict) -> None:
        """Persist OAuth token to disk."""
        with open(self.token_path, "w") as f:
            json.dump(token, f, indent=2)
        log.debug("Token saved to %s", self.token_path)

    # --- HTTP Helpers ---

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Make an HTTP request to the Trakt API with rate limit handling."""
        url = f"{BASE_URL}{path}"
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        resp = self.session.request(method, url, **kwargs)

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 10))
            log.warning("Rate limited, waiting %ds", retry_after)
            time.sleep(retry_after)
            resp = self.session.request(method, url, **kwargs)

        resp.raise_for_status()
        return resp
