import json
import logging
import time
from dataclasses import dataclass, field

from app.config import AppConfig
from app.filters import apply_filters
from app.medusa import MedusaClient
from app.models import PendingShow
from app.trakt import TraktClient

log = logging.getLogger(__name__)


@dataclass
class SyncResult:
    total_fetched: int = 0
    unique_shows: int = 0
    already_in_medusa: int = 0
    added: int = 0
    queued: int = 0
    skipped: int = 0
    failed: int = 0
    duration_seconds: float = 0.0
    per_source: dict[str, int] = field(default_factory=dict)
    success: bool = True
    added_shows: list[dict] = field(default_factory=list)
    show_actions: list[dict] = field(default_factory=list)


def run_sync(config: AppConfig, pending_queue=None) -> SyncResult:
    """Run a single sync cycle: fetch Trakt lists, compare with Medusa, add missing shows."""
    start_time_ns = time.perf_counter_ns()
    result = SyncResult()

    with (
        TraktClient(
            config.trakt,
            config_dir=config.config_dir,
            max_retries=config.sync.max_retries,
            retry_backoff=config.sync.retry_backoff,
        ) as trakt_client,
        MedusaClient(
            config.medusa,
            max_retries=config.sync.max_retries,
            retry_backoff=config.sync.retry_backoff,
        ) as medusa_client,
    ):
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
                result.duration_seconds = _elapsed_seconds(start_time_ns)
                return result

            accepted_shows: list = []
            for show in list_shows:
                should_include, reason = apply_filters(show, source.filters)
                if should_include:
                    accepted_shows.append(show)
                else:
                    log.info(
                        "Filtered '%s' (tvdb:%d) from source '%s': %s",
                        show.title,
                        show.tvdb_id,
                        source_name,
                        reason,
                    )
                    result.skipped += 1
                    _track_action(result, show, source_name, "skipped", reason)

            list_counts[source_name] = len(accepted_shows)
            result.total_fetched += len(accepted_shows)
            result.per_source[source_name] = len(accepted_shows)
            log.info(
                "Source '%s' returned %d show(s), %d accepted after filters",
                source_name,
                len(list_shows),
                len(accepted_shows),
            )

            for show in accepted_shows:
                if show.tvdb_id not in trakt_shows_by_tvdb:
                    trakt_shows_by_tvdb[show.tvdb_id] = show
                source_lists.setdefault(show.tvdb_id, []).append(source_name)
                source_objs.setdefault(show.tvdb_id, []).append(source)

        trakt_shows = list(trakt_shows_by_tvdb.values())
        result.unique_shows = len(trakt_shows)

        if not trakt_shows:
            joined_sources = ", ".join(source.label for source in config.trakt.sources)
            log.info("No shows found across configured Trakt sources: %s", joined_sources)
            result.duration_seconds = _elapsed_seconds(start_time_ns)
            return result

        # Fetch existing Medusa library
        try:
            existing_ids = medusa_client.get_existing_tvdb_ids()
        except Exception as e:
            log.error("Failed to fetch Medusa library: %s", e)
            result.success = False
            result.duration_seconds = _elapsed_seconds(start_time_ns)
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
            result.duration_seconds = _elapsed_seconds(start_time_ns)
            _log_summary(result, config.sync.dry_run)
            return result

        log.info(
            "%d unique show(s) to add to Medusa from sources: %s",
            len(missing),
            ", ".join(f"{name}={count}" for name, count in list_counts.items()),
        )

        # Add missing shows (or queue for approval)
        for show in missing:
            show_sources = source_objs.get(show.tvdb_id, [])
            selected_source = show_sources[0] if show_sources else None
            selected_options = _medusa_add_options_from_source(selected_source)
            selected_source_label = selected_source.label if selected_source else "unknown"
            option_keys = sorted(selected_options.keys()) if selected_options else []

            requires_manual_approval = (
                selected_source is not None and not selected_source.auto_approve
            )

            # Respect manual approval even when no pending queue is available
            if requires_manual_approval and pending_queue is None:
                log.warning(
                    "Skipping '%s' (tvdb:%d): source '%s' requires manual approval but no pending "
                    "queue is configured",
                    show.title,
                    show.tvdb_id,
                    selected_source_label,
                )
                result.skipped += 1
                _track_action(result, show, selected_source_label, "skipped", "no_pending_queue")
                continue

            # Check if this show should go to pending queue
            if requires_manual_approval:
                try:
                    already_pending = pending_queue.is_pending(show.tvdb_id)
                except (OSError, json.JSONDecodeError) as e:
                    log.error(
                        "Pending queue check failed for '%s' (tvdb:%d): %s",
                        show.title,
                        show.tvdb_id,
                        e,
                    )
                    _track_action(
                        result, show, selected_source_label, "failed", "pending_queue_error"
                    )
                    result.failed += 1
                    result.success = False
                    result.duration_seconds = _elapsed_seconds(start_time_ns)
                    return result

                if already_pending:
                    log.debug("Already in pending queue: %s (tvdb:%d)", show.title, show.tvdb_id)
                    result.skipped += 1
                    _track_action(result, show, selected_source_label, "skipped", "already_pending")
                    continue

                # Add to pending queue
                pending_show = PendingShow(
                    tvdb_id=show.tvdb_id,
                    title=show.title,
                    year=show.year,
                    imdb_id=show.imdb_id,
                    source_type=selected_source.type,
                    source_label=selected_source_label,
                    quality=selected_source.medusa.quality,
                    required_words=selected_source.medusa.required_words,
                    poster_url=show.poster_url,
                    network=show.network,
                    genres=show.genres,
                )

                if config.sync.dry_run:
                    log.info(
                        "[DRY RUN] Would queue: %s (tvdb:%d, source:%s)",
                        show.title,
                        show.tvdb_id,
                        selected_source_label,
                    )
                    result.queued += 1
                    _track_action(result, show, selected_source_label, "queued")
                    continue

                try:
                    added = pending_queue.add_show(pending_show)
                except (OSError, json.JSONDecodeError) as e:
                    log.error(
                        "Pending queue add failed for '%s' (tvdb:%d): %s",
                        show.title,
                        show.tvdb_id,
                        e,
                    )
                    _track_action(
                        result, show, selected_source_label, "failed", "pending_queue_error"
                    )
                    result.failed += 1
                    result.success = False
                    result.duration_seconds = _elapsed_seconds(start_time_ns)
                    return result

                if added:
                    log.info(
                        "Queued for approval: %s (tvdb:%d, source:%s)",
                        show.title,
                        show.tvdb_id,
                        selected_source_label,
                    )
                    result.queued += 1
                    _track_action(result, show, selected_source_label, "queued")
                else:
                    result.skipped += 1
                    _track_action(result, show, selected_source_label, "skipped", "already_pending")
                continue

            # Auto-approve path: add directly to Medusa
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
                result.added_shows.append(
                    {
                        "title": show.title,
                        "tvdb_id": show.tvdb_id,
                        "year": show.year,
                        "imdb_id": show.imdb_id,
                    }
                )
                _track_action(result, show, selected_source_label, "added")
                continue

            try:
                if medusa_client.add_show(show.tvdb_id, show.title, add_options=selected_options):
                    log.info(
                        "Added: %s "
                        "(tvdb:%d, source:%s, options_policy:%s, selected_source:%s, "
                        "option_keys:%s)",
                        show.title,
                        show.tvdb_id,
                        ",".join(source_lists.get(show.tvdb_id, [])),
                        options_policy,
                        selected_source_label,
                        option_keys,
                    )
                    result.added += 1
                    result.added_shows.append(
                        {
                            "title": show.title,
                            "tvdb_id": show.tvdb_id,
                            "year": show.year,
                            "imdb_id": show.imdb_id,
                        }
                    )
                    _track_action(result, show, selected_source_label, "added")
                else:
                    result.skipped += 1
                    _track_action(
                        result, show, selected_source_label, "skipped", "medusa_returned_false"
                    )
            except Exception as e:
                log.error("Failed to add '%s' (tvdb:%d): %s", show.title, show.tvdb_id, e)
                result.failed += 1
                _track_action(result, show, selected_source_label, "failed", str(e))

        result.success = result.failed == 0
        result.duration_seconds = _elapsed_seconds(start_time_ns)
        _log_summary(result, config.sync.dry_run)
        return result


def _track_action(result, show, source_label, action, reason=None) -> None:
    """Append a per-show action entry to the result."""
    result.show_actions.append(
        {
            "tvdb_id": show.tvdb_id,
            "title": show.title,
            "year": show.year,
            "imdb_id": show.imdb_id,
            "action": action,
            "source_label": source_label,
            "reason": reason,
        }
    )


def _elapsed_seconds(start_time_ns: int) -> float:
    elapsed = (time.perf_counter_ns() - start_time_ns) / 1_000_000_000
    return elapsed if elapsed > 0 else 1e-9


def _log_summary(result: SyncResult, dry_run: bool) -> None:
    """Log a structured sync summary."""
    prefix = "[DRY RUN] " if dry_run else ""
    source_summary = ", ".join(f"{name}={count}" for name, count in result.per_source.items())
    missing = result.added + result.queued + result.skipped + result.failed
    log.info(
        "%sSync complete in %.1fs: sources: %s | "
        "unique: %d | in library: %d | missing: %d | "
        "added: %d | queued: %d | skipped: %d | failed: %d",
        prefix,
        result.duration_seconds,
        source_summary,
        result.unique_shows,
        result.already_in_medusa,
        missing,
        result.added,
        result.queued,
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
