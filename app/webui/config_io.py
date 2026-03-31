import contextlib
import logging
import os
import tempfile

import yaml

from app.config import AppConfig, ConfigError

log = logging.getLogger(__name__)


def config_to_dict(config: AppConfig) -> dict:
    """Convert an AppConfig dataclass tree back to a plain dict matching the YAML schema."""
    trakt = {
        "client_id": config.trakt.client_id,
        "client_secret": config.trakt.client_secret,
        "username": config.trakt.username,
        "sources": [],
        "limit": config.trakt.limit,
    }

    for source in config.trakt.sources:
        source_dict: dict = {"type": source.type}
        if source.type == "user_list":
            source_dict["owner"] = source.owner
            source_dict["list_slug"] = source.list_slug
        if source.auth is not None:
            source_dict["auth"] = source.auth

        medusa_opts: dict = {}
        if source.medusa.quality is not None:
            medusa_opts["quality"] = source.medusa.quality
        if source.medusa.required_words:
            medusa_opts["required_words"] = source.medusa.required_words
        if medusa_opts:
            source_dict["medusa"] = medusa_opts

        trakt["sources"].append(source_dict)

    result = {
        "trakt": trakt,
        "medusa": {
            "url": config.medusa.url,
            "api_key": config.medusa.api_key,
        },
        "sync": {
            "dry_run": config.sync.dry_run,
            "interval": config.sync.interval,
            "max_retries": config.sync.max_retries,
            "retry_backoff": config.sync.retry_backoff,
            "log_format": config.sync.log_format,
        },
        "health": {
            "enabled": config.health.enabled,
            "port": config.health.port,
        },
        "webui": {
            "enabled": config.webui.enabled,
            "port": config.webui.port,
        },
    }
    return result


def save_config(config_dict: dict, path: str) -> None:
    """Atomically write config dict to a YAML file."""
    target_dir = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=target_dir, suffix=".yaml", prefix=".config_")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path, path)
        log.info("Config saved to %s", path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def save_app_config(config: AppConfig, path: str) -> None:
    """Serialize an AppConfig and save it to a YAML file."""
    config_dict = config_to_dict(config)
    save_config(config_dict, path)


def reload_config(path: str) -> AppConfig:
    """Load and validate config from file. Raises ConfigError on validation failure."""
    try:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    except FileNotFoundError as e:
        raise ConfigError([f"Config file not found: {path}"]) from e
    except yaml.YAMLError as e:
        raise ConfigError([f"Failed to parse {path}: {e}"]) from e
    return load_config_dict(raw, path)


def load_config_dict(raw: dict, path: str) -> AppConfig:
    """Build and validate AppConfig from an in-memory dict."""
    # Re-use load_config internals but we need to avoid sys.exit.
    from app.config import (
        HealthConfig,
        MedusaConfig,
        SyncConfig,
        TraktConfig,
        WebUIConfig,
        _legacy_lists_to_sources,
        _normalize_trakt_lists,
        _normalize_trakt_sources,
        _to_bool,
        _validate,
    )

    trakt_raw = raw.get("trakt", {})
    medusa_raw = raw.get("medusa", {})
    sync_raw = raw.get("sync", {})
    health_raw = raw.get("health", {})
    webui_raw = raw.get("webui", {})

    trakt_lists = _normalize_trakt_lists(trakt_raw)
    username = str(trakt_raw.get("username", ""))
    has_sources_key = "sources" in trakt_raw

    if has_sources_key:
        trakt_sources = _normalize_trakt_sources(trakt_raw)
    else:
        trakt_sources = _legacy_lists_to_sources(trakt_lists, username)

    trakt = TraktConfig(
        client_id=str(trakt_raw.get("client_id", "")),
        client_secret=str(trakt_raw.get("client_secret", "")),
        username=str(trakt_raw.get("username", "")),
        lists=trakt_lists,
        sources=trakt_sources,
        limit=int(trakt_raw.get("limit", 50)),
    )

    medusa = MedusaConfig(
        url=str(medusa_raw.get("url", "")).rstrip("/"),
        api_key=str(medusa_raw.get("api_key", "")),
    )

    sync = SyncConfig(
        dry_run=_to_bool(sync_raw.get("dry_run", False)),
        interval=int(sync_raw.get("interval", 0)),
        max_retries=int(sync_raw.get("max_retries", 3)),
        retry_backoff=float(sync_raw.get("retry_backoff", 2.0)),
        log_format=str(sync_raw.get("log_format", "text")).strip().lower(),
    )

    health = HealthConfig(
        enabled=_to_bool(health_raw.get("enabled", False)),
        port=int(health_raw.get("port", 8095)),
    )

    webui = WebUIConfig(
        enabled=_to_bool(webui_raw.get("enabled", False)),
        port=int(webui_raw.get("port", 8089)),
    )

    config = AppConfig(
        trakt=trakt,
        medusa=medusa,
        sync=sync,
        health=health,
        webui=webui,
        config_dir=os.path.dirname(os.path.abspath(path)),
    )

    _validate(config)
    return config
