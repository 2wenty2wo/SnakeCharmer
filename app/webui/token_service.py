import json
import logging
import os
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

TOKEN_EXPIRING_SOON_SECONDS = 3600


@dataclass
class TokenMetadata:
    created_at: int | None
    expires_in: int | None
    has_refresh_token: bool


class TokenService:
    """Safe token file interactions for the web UI."""

    def __init__(self, token_path: str):
        self.token_path = token_path

    def read_metadata(self) -> TokenMetadata | None:
        token = self._read_token_file()
        if token is None:
            return None
        return TokenMetadata(
            created_at=_as_int(token.get("created_at")),
            expires_in=_as_int(token.get("expires_in")),
            has_refresh_token=bool(token.get("refresh_token")),
        )

    def status(self, now: float | None = None) -> str:
        metadata = self.read_metadata()
        if metadata is None:
            return "missing"

        if metadata.created_at is None or metadata.expires_in is None:
            return "expired"

        current_time = now if now is not None else time.time()
        expires_at = metadata.created_at + metadata.expires_in
        if current_time >= expires_at:
            return "expired"
        if current_time >= expires_at - TOKEN_EXPIRING_SOON_SECONDS:
            return "expiring_soon"
        return "valid"

    def delete(self) -> bool:
        if not os.path.exists(self.token_path):
            return False
        try:
            os.remove(self.token_path)
            log.info("Deleted Trakt token file")
            return True
        except OSError as e:
            log.warning("Failed to delete Trakt token file: %s", e)
            return False

    def _read_token_file(self) -> dict | None:
        if not os.path.exists(self.token_path):
            return None
        try:
            with open(self.token_path) as f:
                token = json.load(f)
            if isinstance(token, dict):
                return token
            return None
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to read token metadata: %s", e)
            return None


def _as_int(value) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
