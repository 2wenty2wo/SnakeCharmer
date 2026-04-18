import logging
import threading
from dataclasses import dataclass, field

from app.config import get_config_errors
from app.health import SyncStatus
from app.sync import SyncResult, run_sync
from app.sync_events import (
    EVT_ERROR,
    EVT_FINISHED,
    EVT_STARTED,
    SyncEventBroker,
    make_emitter,
)

log = logging.getLogger(__name__)


@dataclass
class SyncManager:
    """Thread-safe manager for triggering syncs from the web UI."""

    config_holder: object  # ConfigHolder — avoid circular import
    sync_status: SyncStatus
    pending_queue: object | None = None  # PendingQueue
    broker: SyncEventBroker = field(default_factory=SyncEventBroker, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _running: bool = False
    _last_result: SyncResult | None = None
    _error: str | None = None
    _trigger: str | None = None

    def start_sync(self, trigger: str = "web") -> bool:
        """Start a sync in a background thread.

        Returns False if already running or config is incomplete.
        """
        config = self.config_holder.get()
        errors = get_config_errors(config)
        if errors:
            with self._lock:
                self._error = "Config incomplete: " + "; ".join(errors)
            return False
        if not self._begin_sync(trigger):
            return False

        thread = threading.Thread(target=self._run_sync, daemon=True)
        thread.start()
        return True

    def run_sync_blocking(self, trigger: str = "scheduler") -> SyncResult | None:
        """Run a sync in the current thread if no other sync is active."""
        if not self._begin_sync(trigger):
            return None
        return self._run_sync()

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def get_state(self) -> dict:
        """Return current sync manager state for polling."""
        with self._lock:
            state: dict = {
                "running": self._running,
                "run_id": self.broker.current_run_id,
            }
            if self._trigger:
                state["trigger"] = self._trigger
            if self._error:
                state["error"] = self._error
            if self._last_result is not None:
                r = self._last_result
                state["result"] = {
                    "success": r.success,
                    "added": r.added,
                    "queued": r.queued,
                    "skipped": r.skipped,
                    "failed": r.failed,
                    "unique_shows": r.unique_shows,
                    "duration_seconds": round(r.duration_seconds, 1),
                }
            return state

    def _begin_sync(self, trigger: str) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._error = None
            self._trigger = trigger
            return True

    def _run_sync(self) -> SyncResult | None:
        """Execute sync and stream progress events via the broker."""
        result: SyncResult | None = None
        broker = self.broker
        run_id = broker.new_run()
        config = self.config_holder.get()
        trigger = self._trigger or "web"

        started_at = self._utc_now_iso()
        broker.emit(
            EVT_STARTED,
            {
                "run_id": run_id,
                "started_at": started_at,
                "trigger": trigger,
                "dry_run": config.sync.dry_run,
                "sources": [
                    {"name": s.label, "type": s.type} for s in config.trakt.sources
                ],
            },
        )
        emitter = make_emitter(broker)
        try:
            log.info("Sync triggered (web UI/scheduler coordinator)")
            result = run_sync(config, pending_queue=self.pending_queue, emit=emitter)
            with self._lock:
                self._last_result = result
            self.sync_status.update(result)

            try:
                from app.notify import send_notification

                send_notification(config.notify, result, dry_run=config.sync.dry_run)
            except Exception as exc:
                log.warning("Notification error after manual sync: %s", exc)

            broker.emit(
                EVT_FINISHED,
                {
                    "run_id": run_id,
                    "success": result.success,
                    "added": result.added,
                    "queued": result.queued,
                    "skipped": result.skipped,
                    "failed": result.failed,
                    "unique_shows": result.unique_shows,
                    "total_fetched": result.total_fetched,
                    "already_in_medusa": result.already_in_medusa,
                    "duration_seconds": round(result.duration_seconds, 2),
                    "dry_run": config.sync.dry_run,
                },
            )
            log.info("Sync completed: added=%d, failed=%d", result.added, result.failed)
        except Exception as exc:
            log.exception("Sync failed")
            with self._lock:
                self._error = str(exc)
            broker.emit(
                EVT_ERROR,
                {"run_id": run_id, "message": str(exc)},
            )
            raise
        finally:
            with self._lock:
                self._running = False
        return result

    @staticmethod
    def _utc_now_iso() -> str:
        import datetime as _dt

        return _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")
