import logging

from app.config import AppConfig
from app.medusa import MedusaClient
from app.trakt import TraktClient

log = logging.getLogger(__name__)


def run_sync(config: AppConfig) -> None:
    """Run a single sync cycle: fetch Trakt lists, compare with Medusa, add missing shows."""
    trakt_client = TraktClient(config.trakt, config_dir=config.config_dir)
    medusa_client = MedusaClient(config.medusa)

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
            return

        list_counts[source_name] = len(list_shows)
        log.info("Source '%s' returned %d show(s)", source_name, len(list_shows))

        for show in list_shows:
            if show.tvdb_id not in trakt_shows_by_tvdb:
                trakt_shows_by_tvdb[show.tvdb_id] = show
            source_lists.setdefault(show.tvdb_id, []).append(source_name)
            source_objs.setdefault(show.tvdb_id, []).append(source)

    trakt_shows = list(trakt_shows_by_tvdb.values())

    if not trakt_shows:
        joined_sources = ", ".join(source.label for source in config.trakt.sources)
        log.info("No shows found across configured Trakt sources: %s", joined_sources)
        return

    # Fetch existing Medusa library
    try:
        existing_ids = medusa_client.get_existing_tvdb_ids()
    except Exception as e:
        log.error("Failed to fetch Medusa library: %s", e)
        return

    # Find missing shows
    missing = [s for s in trakt_shows if s.tvdb_id not in existing_ids]

    if not missing:
        log.info(
            "Everything in sync! All %d unique Trakt shows are already in Medusa. Sources: %s",
            len(trakt_shows),
            ", ".join(f"{name}={count}" for name, count in list_counts.items()),
        )
        return

    log.info(
        "%d unique show(s) to add to Medusa from sources: %s",
        len(missing),
        ", ".join(f"{name}={count}" for name, count in list_counts.items()),
    )

    # Add missing shows
    added = 0
    skipped = 0
    failed = 0

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
            added += 1
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
                added += 1
            else:
                skipped += 1
        except Exception as e:
            log.error("Failed to add '%s' (tvdb:%d): %s", show.title, show.tvdb_id, e)
            failed += 1

    # Summary
    prefix = "[DRY RUN] " if config.sync.dry_run else ""
    log.info(
        "%sSync complete: %d added, %d already existed, %d failed (out of %d missing)",
        prefix,
        added,
        skipped,
        failed,
        len(missing),
    )
    source_summary = ", ".join(f"{name}={count}" for name, count in list_counts.items())
    log.info("Trakt source summary: %s", source_summary)


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
