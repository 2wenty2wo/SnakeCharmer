import json
import logging
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer

log = logging.getLogger(__name__)


@dataclass
class SyncStatus:
    """Thread-safe container for the latest sync result."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_sync_time: float = 0.0
    _last_result: object = None
    _start_time: float = field(default_factory=time.monotonic)

    def update(self, result) -> None:
        with self._lock:
            self._last_sync_time = time.time()
            self._last_result = result

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
                "skipped": result.skipped,
                "failed": result.failed,
                "unique_shows": result.unique_shows,
                "already_in_medusa": result.already_in_medusa,
            }
            return data


class _HealthHandler(BaseHTTPRequestHandler):
    sync_status: SyncStatus

    def do_GET(self) -> None:
        if self.path not in ("/", "/health"):
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
    server = HTTPServer(("0.0.0.0", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Health server listening on port %d", port)
    return server
