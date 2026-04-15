"""Shared parsing for Trakt OAuth device-code flow (CLI + web UI)."""

import logging

log = logging.getLogger(__name__)


def parse_oauth_device_timing(interval: object, expires_in: object) -> tuple[int, int] | None:
    """Parse Trakt ``interval`` and ``expires_in`` from a device authorization response.

    Returns ``(interval_seconds, expires_in_seconds)`` with a minimum interval of 1
    and a minimum expiry window of 600 seconds.

    Returns ``None`` if either value is not a finite number (caller should use defaults).
    """
    try:
        interval_f = float(interval)
        expires_f = float(expires_in)
    except (TypeError, ValueError, OverflowError):
        return None
    if interval_f != interval_f or expires_f != expires_f:  # NaN
        return None
    try:
        interval_i = int(interval_f)
        expires_i = int(expires_f)
    except (ValueError, OverflowError):
        return None
    if interval_i < 1:
        log.warning("Invalid OAuth poll interval %r; using 1s", interval)
        interval_i = 1
    if expires_i < 600:
        log.warning("OAuth device expires_in %r below minimum 600s; using 600s", expires_in)
        expires_i = 600
    return interval_i, expires_i
