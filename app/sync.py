import logging

from app.config import AppConfig
from app.trakt import TraktClient
from app.medusa import MedusaClient

log = logging.getLogger(__name__)


def run_sync(config: AppConfig) -> None:
    """Run a single sync cycle: fetch Trakt list, compare with Medusa, add missing shows."""
    trakt_client = TraktClient(config.trakt, config_dir=config.config_dir)
    medusa_client = MedusaClient(config.medusa)

    # Fetch shows from Trakt
    try:
        trakt_shows = trakt_client.get_shows()
    except Exception as e:
        log.error("Failed to fetch Trakt list: %s", e)
        return

    if not trakt_shows:
        log.info("No shows found in Trakt list '%s'", config.trakt.list)
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
        log.info("Everything in sync! All %d Trakt shows are already in Medusa.", len(trakt_shows))
        return

    log.info("%d show(s) to add to Medusa", len(missing))

    # Add missing shows
    added = 0
    skipped = 0
    failed = 0

    for show in missing:
        if config.sync.dry_run:
            log.info("[DRY RUN] Would add: %s (tvdb:%d)", show.title, show.tvdb_id)
            added += 1
            continue

        try:
            if medusa_client.add_show(show.tvdb_id, show.title):
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
        prefix, added, skipped, failed, len(missing),
    )
