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
    list_counts: dict[str, int] = {}

    # Fetch shows from Trakt lists and dedupe by TVDB ID
    for list_name in config.trakt.lists:
        try:
            list_shows = trakt_client.get_shows(list_name)
        except Exception as e:
            log.error("Failed to fetch Trakt list '%s': %s", list_name, e)
            return

        list_counts[list_name] = len(list_shows)
        log.info("List '%s' returned %d show(s)", list_name, len(list_shows))

        for show in list_shows:
            if show.tvdb_id not in trakt_shows_by_tvdb:
                trakt_shows_by_tvdb[show.tvdb_id] = show
            source_lists.setdefault(show.tvdb_id, []).append(list_name)

    trakt_shows = list(trakt_shows_by_tvdb.values())

    if not trakt_shows:
        joined_lists = ", ".join(config.trakt.lists)
        log.info("No shows found across configured Trakt lists: %s", joined_lists)
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
        "%d unique show(s) to add to Medusa from lists: %s",
        len(missing),
        ", ".join(f"{name}={count}" for name, count in list_counts.items()),
    )

    # Add missing shows
    added = 0
    skipped = 0
    failed = 0

    for show in missing:
        if config.sync.dry_run:
            log.info(
                "[DRY RUN] Would add: %s (tvdb:%d, source:%s)",
                show.title,
                show.tvdb_id,
                ",".join(source_lists.get(show.tvdb_id, [])),
            )
            added += 1
            continue

        try:
            if medusa_client.add_show(show.tvdb_id, show.title):
                log.info(
                    "Added: %s (tvdb:%d, source:%s)",
                    show.title,
                    show.tvdb_id,
                    ",".join(source_lists.get(show.tvdb_id, [])),
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
