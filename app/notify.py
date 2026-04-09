import logging

import apprise

from app.config import NotifyConfig
from app.sync import SyncResult

log = logging.getLogger(__name__)


def send_notification(config: NotifyConfig, result: SyncResult, *, dry_run: bool = False) -> None:
    """Send an Apprise notification based on sync result."""
    if not config.enabled:
        return
    if not config.urls:
        log.warning("notify.enabled is true but notify.urls is empty — no notification sent")
        return

    if result.success:
        if not config.on_success:
            return
        if config.only_if_added and result.added == 0:
            log.debug("Skipping notification: only_if_added=true and no shows were added")
            return
        title, body = _success_message(result, dry_run=dry_run)
    else:
        if not config.on_failure:
            return
        title, body = _failure_message(result)

    try:
        ap = _build_apprise(config.urls)
        ap.notify(title=title, body=body)
        log.info("Notification sent: %s", title)
    except Exception as exc:
        log.warning("Failed to send notification: %s", exc)


def _build_apprise(urls: list[str]) -> apprise.Apprise:
    ap = apprise.Apprise()
    for url in urls:
        ap.add(url)
    return ap


def _success_message(result: SyncResult, *, dry_run: bool = False) -> tuple[str, str]:
    title = "SnakeCharmer: Sync Complete"
    added_text = "Would add" if dry_run else "Added"
    queued_text = "would queue" if dry_run else "queued"
    body_parts = [
        f"{added_text} {result.added} show(s)",
    ]
    if result.queued > 0:
        body_parts.append(f"{queued_text} {result.queued} for approval")
    body_parts.append(f"in {result.duration_seconds:.1f}s")
    body_parts.append(
        f"(unique: {result.unique_shows}, already in library: {result.already_in_medusa}, "
        f"skipped: {result.skipped}, failed: {result.failed})"
    )
    body = " ".join(body_parts)
    return title, body


def _failure_message(result: SyncResult) -> tuple[str, str]:
    title = "SnakeCharmer: Sync Failed"
    body = (
        f"Sync cycle failed after {result.duration_seconds:.1f}s "
        f"(added: {result.added}, failed: {result.failed})"
    )
    return title, body
