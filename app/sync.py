import logging
import time
from dataclasses import dataclass, field

from app.config import AppConfig
from app.medusa import MedusaClient
from app.trakt import TraktClient

log = logging.getLogger(__name__)


@dataclass
class SyncResult:
    total_fetched: int = 0
    unique_shows: int = 0
    already_in_medusa: int = 0
    added: int = 0
    skipped: int = 0
    failed: int = 0
    duration_seconds: float = 0.0
    per_source: dict[str, int] = field(default_factory=dict)
    success: bool = True


def run_sync(config: AppConfig) -> SyncResult:
    """Run a single sync cycle: fetch Trakt lists, compare with Medusa, add missing shows."""
    start_time = time.monotonic()
    result = SyncResult()

    trakt_client = TraktClient(
        config.trakt,
        config_dir=config.config_dir,
        max_retries=config.sync.max_retries,
        retry_backoff=config.sync.retry_backoff,
    )
    medusa_client = MedusaClient(
        config.medusa,
        max_retries=config.sync.max_retries,
        retry_backoff=config.sync.retry_backoff,
    )

    trakt_shows_by_tvdb = {}
    source_lists: dict[int, list[str]] = {}
    source_objs: dict[int, list] = {}
    list_counts: dict[str, int] = {}
    options_policy = "first_source_in_config_order"

    # Fetch shows from Trakt sources and dedupe by TVDB ID
    for source in config.trakt.sources:
        source_name = source.label
        try:
            list_shows = trakt_client.get_shows(source)
        except Exception as e:
            log.error("Failed to fetch Trakt source '%s': %s", source_name, e)
            result.success = False
            result.duration_seconds = time.monotonic() - start_time
            return result

        list_counts[source_name] = len(list_shows)
        result.total_fetched += len(list_shows)
        result.per_source[source_name] = len(list_shows)
        log.info("Source '%s' returned %d show(s)", source_name, len(list_shows))

        for show in list_shows:
            if show.tvdb_id not in trakt_shows_by_tvdb:
                trakt_shows_by_tvdb[show.tvdb_id] = show
            source_lists.setdefault(show.tvdb_id, []).append(source_name)
            source_objs.setdefault(show.tvdb_id, []).append(source)

    trakt_shows = list(trakt_shows_by_tvdb.values())
    result.unique_shows = len(trakt_shows)

    if not trakt_shows:
        joined_sources = ", ".join(source.label for source in config.trakt.sources)
        log.info("No shows found across configured Trakt sources: %s", joined_sources)
        result.duration_seconds = time.monotonic() - start_time
        return result

    # Fetch existing Medusa library
    try:
        existing_ids = medusa_client.get_existing_tvdb_ids()
    except Exception as e:
        log.error("Failed to fetch Medusa library: %s", e)
        result.success = False
        result.duration_seconds = time.monotonic() - start_time
        return result

    # Find missing shows
    missing = [s for s in trakt_shows if s.tvdb_id not in existing_ids]
    result.already_in_medusa = len(trakt_shows) - len(missing)

    if not missing:
        log.info(
            "Everything in sync! All %d unique Trakt shows are already in Medusa. Sources: %s",
            len(trakt_shows),
            ", ".join(f"{name}={count}" for name, count in list_counts.items()),
        )
        result.duration_seconds = time.monotonic() - start_time
        _log_summary(result, config.sync.dry_run)
        return result

    log.info(
        "%d unique show(s) to add to Medusa from sources: %s",
        len(missing),
        ", ".join(f"{name}={count}" for name, count in list_counts.items()),
    )

    # Add missing shows
    for show in missing:
        show_sources = source_objs.get(show.tvdb_id, [])
        selected_source = show_sources[0] if show_sources else None
        selected_options = _medusa_add_options_from_source(selected_source)
        selected_source_label = selected_source.label if selected_source else "unknown"
        option_keys = sorted(selected_options.keys()) if selected_options else []

        if config.sync.dry_run:
            log.info(
                "[DRY RUN] Would add: %s "
                "(tvdb:%d, source:%s, options_policy:%s, selected_source:%s, option_keys:%s)",
                show.title,
                show.tvdb_id,
                ",".join(source_lists.get(show.tvdb_id, [])),
                options_policy,
                selected_source_label,
                option_keys,
            )
            result.added += 1
            continue

        try:
            if medusa_client.add_show(show.tvdb_id, show.title, add_options=selected_options):
                log.info(
                    "Added: %s "
                    "(tvdb:%d, source:%s, options_policy:%s, selected_source:%s, option_keys:%s)",
                    show.title,
                    show.tvdb_id,
                    ",".join(source_lists.get(show.tvdb_id, [])),
                    options_policy,
                    selected_source_label,
                    option_keys,
                )
                result.added += 1
            else:
                result.skipped += 1
        except Exception as e:
            log.error("Failed to add '%s' (tvdb:%d): %s", show.title, show.tvdb_id, e)
            result.failed += 1

    result.success = result.failed == 0
    result.duration_seconds = time.monotonic() - start_time
    _log_summary(result, config.sync.dry_run)
    return result


def _log_summary(result: SyncResult, dry_run: bool) -> None:
    """Log a structured sync summary."""
    prefix = "[DRY RUN] " if dry_run else ""
    source_summary = ", ".join(f"{name}={count}" for name, count in result.per_source.items())
    missing = result.added + result.skipped + result.failed
    log.info(
        "%sSync complete in %.1fs: sources: %s | "
        "unique: %d | in library: %d | missing: %d | "
        "added: %d | skipped: %d | failed: %d",
        prefix,
        result.duration_seconds,
        source_summary,
        result.unique_shows,
        result.already_in_medusa,
        missing,
        result.added,
        result.skipped,
        result.failed,
    )


def _medusa_add_options_from_source(source) -> dict | None:
    if source is None:
        return None

    add_options: dict[str, str | list[str]] = {}

    quality = source.medusa.quality
    if quality is not None:
        add_options["quality"] = quality

    required_words = source.medusa.required_words
    if required_words:
        add_options["required_words"] = required_words

    return add_options or None
