import logging
import os
import sys

import yaml

from app.models import (  # noqa: F401 — re-exported as the public config API
    PUBLIC_LISTS,
    SOURCE_TYPES,
    AppConfig,
    ConfigError,
    HealthConfig,
    MedusaAddOptions,
    MedusaConfig,
    NotifyConfig,
    SyncConfig,
    TraktConfig,
    TraktSource,
    WebUIConfig,
)

log = logging.getLogger(__name__)


ENV_MAP = {
    "SNAKECHARMER_TRAKT_CLIENT_ID": ("trakt", "client_id"),
    "SNAKECHARMER_TRAKT_CLIENT_SECRET": ("trakt", "client_secret"),
    "SNAKECHARMER_TRAKT_USERNAME": ("trakt", "username"),
    "SNAKECHARMER_TRAKT_LIMIT": ("trakt", "limit"),
    "SNAKECHARMER_MEDUSA_URL": ("medusa", "url"),
    "SNAKECHARMER_MEDUSA_API_KEY": ("medusa", "api_key"),
    "SNAKECHARMER_SYNC_DRY_RUN": ("sync", "dry_run"),
    "SNAKECHARMER_SYNC_INTERVAL": ("sync", "interval"),
    "SNAKECHARMER_SYNC_MAX_RETRIES": ("sync", "max_retries"),
    "SNAKECHARMER_SYNC_RETRY_BACKOFF": ("sync", "retry_backoff"),
    "SNAKECHARMER_SYNC_LOG_FORMAT": ("sync", "log_format"),
    "SNAKECHARMER_HEALTH_ENABLED": ("health", "enabled"),
    "SNAKECHARMER_HEALTH_PORT": ("health", "port"),
    "SNAKECHARMER_WEBUI_ENABLED": ("webui", "enabled"),
    "SNAKECHARMER_WEBUI_PORT": ("webui", "port"),
    "SNAKECHARMER_NOTIFY_ENABLED": ("notify", "enabled"),
    "SNAKECHARMER_NOTIFY_URLS": ("notify", "urls"),
}

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


def load_config(path: str, skip_validate: bool = False) -> AppConfig:
    """Load configuration from YAML file with environment variable overrides."""
    raw = {}
    try:
        with open(path, encoding="utf-8") as f:
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
    health_raw = raw.get("health", {})
    webui_raw = raw.get("webui", {})
    notify_raw = raw.get("notify", {})

    # Apply environment variable overrides
    for env_var, (section, key) in ENV_MAP.items():
        value = os.environ.get(env_var)
        if value is not None:
            target = {
                "trakt": trakt_raw,
                "medusa": medusa_raw,
                "sync": sync_raw,
                "health": health_raw,
                "webui": webui_raw,
                "notify": notify_raw,
            }[section]
            target[key] = value

    # Build config objects
    trakt = TraktConfig(
        client_id=str(trakt_raw.get("client_id", "")),
        client_secret=str(trakt_raw.get("client_secret", "")),
        username=str(trakt_raw.get("username", "")),
        sources=_normalize_trakt_sources(trakt_raw),
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

    notify = NotifyConfig(
        enabled=_to_bool(notify_raw.get("enabled", False)),
        urls=_normalize_notify_urls(notify_raw),
        on_success=_to_bool(notify_raw.get("on_success", True)),
        on_failure=_to_bool(notify_raw.get("on_failure", True)),
        only_if_added=_to_bool(notify_raw.get("only_if_added", False)),
    )

    config = AppConfig(
        trakt=trakt,
        medusa=medusa,
        sync=sync,
        health=health,
        webui=webui,
        notify=notify,
        config_dir=os.path.dirname(os.path.abspath(path)),
    )

    if not skip_validate:
        try:
            _validate(config)
        except ConfigError as e:
            for err in e.errors:
                log.error("Config error: %s", err)
            sys.exit(1)

    return config


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("true", "1", "yes")


def _normalize_notify_urls(notify_raw: dict) -> list[str]:
    raw = notify_raw.get("urls", [])
    if isinstance(raw, list):
        return [str(u).strip() for u in raw if str(u).strip()]
    if isinstance(raw, str):
        return [u.strip() for u in raw.split(",") if u.strip()]
    return []


def _normalize_trakt_sources(trakt_raw: dict) -> list[TraktSource]:
    raw_sources = trakt_raw.get("sources", [])
    if not isinstance(raw_sources, list):
        return []

    sources: list[TraktSource] = []
    for item in raw_sources:
        if isinstance(item, str):
            normalized = item.strip()
            if normalized:
                if normalized in SOURCE_TYPES:
                    sources.append(TraktSource(type=normalized))
                else:
                    sources.append(TraktSource(type="user_list", list_slug=normalized))
            continue
        if not isinstance(item, dict):
            continue
        source_type = str(item.get("type", "")).strip()
        if not source_type:
            continue
        owner = str(item.get("owner", "")).strip()
        list_slug = str(item.get("list_slug", "")).strip()
        auth_raw = item.get("auth")
        auth = None if auth_raw is None else _to_bool(auth_raw)
        auto_approve = _to_bool(item.get("auto_approve", True))
        medusa_raw = item.get("medusa", {})
        medusa = _parse_medusa_add_options(medusa_raw)
        sources.append(
            TraktSource(
                type=source_type,
                owner=owner,
                list_slug=list_slug,
                auth=auth,
                auto_approve=auto_approve,
                medusa=medusa,
            )
        )
    return sources


def _parse_medusa_add_options(raw_options) -> MedusaAddOptions:
    if not isinstance(raw_options, dict):
        return MedusaAddOptions()
    quality = raw_options.get("quality")
    return MedusaAddOptions(
        quality=quality,
        required_words=raw_options.get("required_words", []),
    )


def get_config_errors(config: AppConfig) -> list[str]:
    """Return validation error messages, or empty list if config is valid."""
    errors = []

    if not config.trakt.client_id:
        errors.append("trakt.client_id is required")

    if not config.medusa.url:
        errors.append("medusa.url is required")

    if not config.medusa.api_key:
        errors.append("medusa.api_key is required")

    if not config.trakt.sources:
        errors.append("trakt.sources must include at least one source")

    for source in config.trakt.sources:
        if source.type not in SOURCE_TYPES:
            errors.append(
                f"trakt.sources.type '{source.type}' is invalid "
                f"(expected one of: {', '.join(sorted(SOURCE_TYPES))})"
            )
            continue

        if source.type == "watchlist":
            if not config.trakt.username:
                errors.append("trakt.username is required for source type 'watchlist'")
            if not config.trakt.client_secret:
                errors.append(
                    "trakt.client_secret is required for source type 'watchlist' "
                    "(OAuth is required for personal lists)"
                )

        if source.type == "user_list":
            if not source.owner:
                errors.append("trakt.sources[].owner is required for source type 'user_list'")
            if not source.list_slug:
                errors.append("trakt.sources[].list_slug is required for source type 'user_list'")
            if source.requires_auth:
                if not config.trakt.username:
                    errors.append(
                        "trakt.username is required when trakt.sources[].auth=true "
                        "for source type 'user_list'"
                    )
                if not config.trakt.client_secret:
                    errors.append(
                        "trakt.client_secret is required when trakt.sources[].auth=true "
                        "for source type 'user_list'"
                    )

        quality = source.medusa.quality
        if quality is not None and not isinstance(quality, (str, list)):
            errors.append("trakt.sources[].medusa.quality must be a string or list of strings")
        if isinstance(quality, list) and any(not isinstance(item, str) for item in quality):
            errors.append("trakt.sources[].medusa.quality must be a string or list of strings")
        if isinstance(quality, str) and quality.strip().lower() not in _QUALITY_BY_NAME:
            errors.append(
                f"trakt.sources[].medusa.quality contains invalid value '{quality}'. "
                f"Valid values: {', '.join(sorted(_QUALITY_BY_NAME))}"
            )
        if isinstance(quality, list) and all(isinstance(item, str) for item in quality):
            invalid = [q for q in quality if q.strip().lower() not in _QUALITY_BY_NAME]
            if invalid:
                errors.append(
                    f"trakt.sources[].medusa.quality contains invalid value(s) "
                    f"{', '.join(repr(q) for q in invalid)}. "
                    f"Valid values: {', '.join(sorted(_QUALITY_BY_NAME))}"
                )

        required_words = source.medusa.required_words
        if not isinstance(required_words, list):
            errors.append(
                "trakt.sources[].medusa.required_words must be a list of non-empty strings"
            )
        else:
            if any(not isinstance(word, str) or not word.strip() for word in required_words):
                errors.append(
                    "trakt.sources[].medusa.required_words must be a list of non-empty strings"
                )

    return errors


def get_section_errors(config: AppConfig, section: str) -> list[str]:
    """Return only validation errors relevant to a specific config section."""
    prefix = section + "."
    return [e for e in get_config_errors(config) if e.startswith(prefix)]


def _validate(config: AppConfig) -> None:
    """Validate required configuration fields."""
    errors = get_config_errors(config)
    if errors:
        raise ConfigError(errors)
