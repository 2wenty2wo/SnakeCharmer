import ipaddress
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

log = logging.getLogger(__name__)
BIND_ALL_INTERFACES = str(ipaddress.IPv4Address(0))


@dataclass
class SyncStatus:
    """Thread-safe container for the latest sync result."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_sync_time: float = 0.0
    _last_result: object = None
    _start_time: float = field(default_factory=time.monotonic)
    _history: list = field(default_factory=list, repr=False)
    _max_history: int = 20
    _db: object = field(default=None, repr=False)

    def update(self, result) -> None:
        with self._lock:
            self._last_sync_time = time.time()
            self._last_result = result
            if result is not None:
                if self._db is not None:
                    try:
                        self._db.record(result, self._last_sync_time)
                    except Exception:
                        log.exception("Failed to persist sync result to database")
                entry = self._result_to_entry(result, self._last_sync_time)
                self._history.insert(0, entry)
                self._history = self._history[: self._max_history]

    def snapshot(self) -> dict:
        with self._lock:
            uptime = time.monotonic() - self._start_time
            data: dict = {
                "status": "unknown",
                "uptime_seconds": round(uptime, 1),
            }
            if self._last_result is None:
                return data

            result = self._last_result
            if result.success:
                data["status"] = "ok"
            else:
                data["status"] = "degraded"

            data["last_sync"] = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._last_sync_time)),
                "duration_seconds": round(result.duration_seconds, 1),
                "added": result.added,
                "queued": result.queued,
                "skipped": result.skipped,
                "failed": result.failed,
                "unique_shows": result.unique_shows,
                "already_in_medusa": result.already_in_medusa,
            }
            return data

    def get_history(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Return sync history (newest first).

        When a database is configured, reads from SQLite with pagination.
        Otherwise falls back to the in-memory list.
        """
        with self._lock:
            if self._db is not None:
                try:
                    return self._db.get_history(limit=limit, offset=offset)
                except Exception:
                    log.exception("Failed to read sync history from database")
                    return list(self._history)
            return list(self._history[offset : offset + limit])

    def get_total_runs(self) -> int:
        """Return total number of recorded sync runs."""
        with self._lock:
            if self._db is not None:
                try:
                    return self._db.get_total_runs()
                except Exception:
                    log.exception("Failed to read run count from database")
                    return len(self._history)
            return len(self._history)

    def get_totals(self) -> dict:
        """Return aggregate stats across all sync runs.

        Delegates to the DB when available, otherwise computes from in-memory history.
        """
        with self._lock:
            if self._db is not None:
                try:
                    return self._db.get_totals()
                except Exception:
                    log.exception("Failed to read totals from database")

            total_runs = len(self._history)
            if total_runs == 0:
                return {
                    "total_runs": 0,
                    "total_added": 0,
                    "total_queued": 0,
                    "total_failed": 0,
                    "success_rate": 0,
                }
            total_added = sum(e.get("added", 0) for e in self._history)
            total_queued = sum(e.get("queued", 0) for e in self._history)
            total_failed = sum(e.get("failed", 0) for e in self._history)
            successful = sum(
                1 for e in self._history if e.get("failed", 0) == 0 and e.get("success", True)
            )
            return {
                "total_runs": total_runs,
                "total_added": total_added,
                "total_queued": total_queued,
                "total_failed": total_failed,
                "success_rate": int((successful / total_runs) * 100),
            }

    @staticmethod
    def _result_to_entry(result, sync_time: float) -> dict:
        return {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(sync_time)),
            "duration_seconds": round(result.duration_seconds, 1),
            "added": result.added,
            "queued": result.queued,
            "skipped": result.skipped,
            "failed": result.failed,
            "unique_shows": result.unique_shows,
            "already_in_medusa": result.already_in_medusa,
            "success": result.success,
            "per_source": dict(result.per_source),
            "added_shows": list(result.added_shows) if result.added_shows else [],
            "show_actions": (
                list(result.show_actions)
                if hasattr(result, "show_actions") and result.show_actions
                else []
            ),
        }


class _HealthHandler(BaseHTTPRequestHandler):
    sync_status: SyncStatus

    def do_GET(self) -> None:
        if urlparse(self.path).path not in ("/", "/health"):
            self.send_response(404)
            self.end_headers()
            return

        data = self.sync_status.snapshot()
        body = json.dumps(data, indent=2).encode()
        status_code = 200 if data["status"] != "degraded" else 503
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args) -> None:  # noqa: A002
        # Suppress default stderr logging from BaseHTTPRequestHandler
        pass


def start_health_server(port: int, sync_status: SyncStatus) -> HTTPServer:
    """Start the health check HTTP server in a daemon thread."""
    handler = type("Handler", (_HealthHandler,), {"sync_status": sync_status})
    server = HTTPServer((BIND_ALL_INTERFACES, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Health server listening on port %d", port)
    return server
