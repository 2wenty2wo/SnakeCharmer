import logging
import threading
from dataclasses import dataclass, field

from app.health import SyncStatus
from app.sync import SyncResult, run_sync

log = logging.getLogger(__name__)


@dataclass
class SyncManager:
    """Thread-safe manager for triggering syncs from the web UI."""

    config_holder: object  # ConfigHolder — avoid circular import
    sync_status: SyncStatus
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _running: bool = False
    _last_result: SyncResult | None = None
    _error: str | None = None

    def start_sync(self) -> bool:
        """Start a sync in a background thread. Returns False if already running."""
        if not self._begin_sync():
            return False

        thread = threading.Thread(target=self._run_sync, daemon=True)
        thread.start()
        return True

    def run_sync_blocking(self) -> SyncResult | None:
        """Run a sync in the current thread if no other sync is active."""
        if not self._begin_sync():
            return None
        return self._run_sync()

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def get_state(self) -> dict:
        """Return current sync manager state for polling."""
        with self._lock:
            state: dict = {"running": self._running}
            if self._error:
                state["error"] = self._error
            if self._last_result is not None:
                r = self._last_result
                state["result"] = {
                    "success": r.success,
                    "added": r.added,
                    "skipped": r.skipped,
                    "failed": r.failed,
                    "unique_shows": r.unique_shows,
                    "duration_seconds": round(r.duration_seconds, 1),
                }
            return state

    def _begin_sync(self) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._error = None
            return True

    def _run_sync(self) -> SyncResult | None:
        """Execute sync."""
        result: SyncResult | None = None
        try:
            config = self.config_holder.get()
            log.info("Sync triggered (web UI/scheduler coordinator)")
            result = run_sync(config)
            with self._lock:
                self._last_result = result
            self.sync_status.update(result)

            # Send notification
            try:
                from app.notify import send_notification

                send_notification(config.notify, result, dry_run=config.sync.dry_run)
            except Exception as exc:
                log.warning("Notification error after manual sync: %s", exc)

            log.info("Sync completed: added=%d, failed=%d", result.added, result.failed)
        except Exception as exc:
            log.exception("Sync failed")
            with self._lock:
                self._error = str(exc)
            raise
        finally:
            with self._lock:
                self._running = False
        return result
