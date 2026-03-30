import logging
import os
import sys
from dataclasses import dataclass, field

import yaml

log = logging.getLogger(__name__)

PUBLIC_LISTS = {"trending", "popular", "watched"}


@dataclass
class TraktConfig:
    client_id: str = ""
    client_secret: str = ""
    username: str = ""
    lists: list[str] = field(default_factory=lambda: ["watchlist"])
    limit: int = 50


@dataclass
class MedusaConfig:
    url: str = ""
    api_key: str = ""


@dataclass
class SyncConfig:
    dry_run: bool = False
    interval: int = 0


@dataclass
class AppConfig:
    trakt: TraktConfig = field(default_factory=TraktConfig)
    medusa: MedusaConfig = field(default_factory=MedusaConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    config_dir: str = "."


ENV_MAP = {
    "SNAKECHARMER_TRAKT_CLIENT_ID": ("trakt", "client_id"),
    "SNAKECHARMER_TRAKT_CLIENT_SECRET": ("trakt", "client_secret"),
    "SNAKECHARMER_TRAKT_USERNAME": ("trakt", "username"),
    "SNAKECHARMER_TRAKT_LIST": ("trakt", "list"),
    "SNAKECHARMER_TRAKT_LISTS": ("trakt", "lists"),
    "SNAKECHARMER_TRAKT_LIMIT": ("trakt", "limit"),
    "SNAKECHARMER_MEDUSA_URL": ("medusa", "url"),
    "SNAKECHARMER_MEDUSA_API_KEY": ("medusa", "api_key"),
    "SNAKECHARMER_SYNC_DRY_RUN": ("sync", "dry_run"),
    "SNAKECHARMER_SYNC_INTERVAL": ("sync", "interval"),
}


def load_config(path: str) -> AppConfig:
    """Load configuration from YAML file with environment variable overrides."""
    raw = {}
    try:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    except FileNotFoundError:
        log.warning("Config file %s not found, using environment variables only", path)
    except yaml.YAMLError as e:
        log.error("Failed to parse %s: %s", path, e)
        sys.exit(1)

    # Build nested config dict from YAML
    trakt_raw = raw.get("trakt", {})
    medusa_raw = raw.get("medusa", {})
    sync_raw = raw.get("sync", {})

    # Apply environment variable overrides
    for env_var, (section, key) in ENV_MAP.items():
        value = os.environ.get(env_var)
        if value is not None:
            target = {"trakt": trakt_raw, "medusa": medusa_raw, "sync": sync_raw}[section]
            target[key] = value

    # Build config objects
    trakt_lists = _normalize_trakt_lists(trakt_raw)

    trakt = TraktConfig(
        client_id=str(trakt_raw.get("client_id", "")),
        client_secret=str(trakt_raw.get("client_secret", "")),
        username=str(trakt_raw.get("username", "")),
        lists=trakt_lists,
        limit=int(trakt_raw.get("limit", 50)),
    )

    medusa = MedusaConfig(
        url=str(medusa_raw.get("url", "")).rstrip("/"),
        api_key=str(medusa_raw.get("api_key", "")),
    )

    sync = SyncConfig(
        dry_run=_to_bool(sync_raw.get("dry_run", False)),
        interval=int(sync_raw.get("interval", 0)),
    )

    config = AppConfig(
        trakt=trakt,
        medusa=medusa,
        sync=sync,
        config_dir=os.path.dirname(os.path.abspath(path)),
    )

    _validate(config)
    return config


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("true", "1", "yes")


def _normalize_trakt_lists(trakt_raw: dict) -> list[str]:
    raw_lists = trakt_raw.get("lists", trakt_raw.get("list", "watchlist"))
    if isinstance(raw_lists, list):
        parsed_lists = [str(item).strip() for item in raw_lists if str(item).strip()]
    else:
        parsed_lists = [str(raw_lists).strip()]

    return parsed_lists or ["watchlist"]


def _validate(config: AppConfig) -> None:
    """Validate required configuration fields."""
    errors = []

    if not config.trakt.client_id:
        errors.append("trakt.client_id is required")

    if not config.medusa.url:
        errors.append("medusa.url is required")

    if not config.medusa.api_key:
        errors.append("medusa.api_key is required")

    if not config.trakt.lists:
        errors.append("trakt.lists must include at least one list")

    for list_name in config.trakt.lists:
        is_public = list_name in PUBLIC_LISTS
        if not is_public and not config.trakt.username:
            errors.append(
                f"trakt.username is required for list '{list_name}' "
                f"(only {', '.join(sorted(PUBLIC_LISTS))} work without a username)"
            )
        if not is_public and not config.trakt.client_secret:
            errors.append(
                f"trakt.client_secret is required for list '{list_name}' "
                "(OAuth is required for personal lists)"
            )

    if errors:
        for err in errors:
            log.error("Config error: %s", err)
        sys.exit(1)
