import json
import re
import threading
from http.client import HTTPConnection

import pytest

from app.health import SyncStatus, start_health_server
from app.sync import SyncResult


@pytest.fixture
def sync_status():
    return SyncStatus()


class TestSyncStatus:
    def test_initial_snapshot_status_unknown(self, sync_status):
        snap = sync_status.snapshot()
        assert snap["status"] == "unknown"
        assert "last_sync" not in snap
        assert snap["uptime_seconds"] >= 0

    def test_update_with_successful_result(self, sync_status):
        result = SyncResult(
            added=3,
            skipped=1,
            failed=0,
            unique_shows=10,
            already_in_medusa=6,
            duration_seconds=5.2,
            success=True,
        )
        sync_status.update(result)
        snap = sync_status.snapshot()

        assert snap["status"] == "ok"
        assert snap["last_sync"]["added"] == 3
        assert snap["last_sync"]["skipped"] == 1
        assert snap["last_sync"]["failed"] == 0
        assert snap["last_sync"]["unique_shows"] == 10
        assert snap["last_sync"]["already_in_medusa"] == 6
        assert snap["last_sync"]["duration_seconds"] == 5.2

    def test_update_with_failed_result(self, sync_status):
        result = SyncResult(added=1, failed=2, success=False)
        sync_status.update(result)
        snap = sync_status.snapshot()

        assert snap["status"] == "degraded"

    def test_snapshot_timestamp_is_iso8601(self, sync_status):
        result = SyncResult(added=1, success=True, duration_seconds=1.0)
        sync_status.update(result)
        snap = sync_status.snapshot()
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", snap["last_sync"]["timestamp"])

    def test_concurrent_updates_do_not_raise(self, sync_status):
        """Concurrent reads and writes via the threading.Lock must not raise or corrupt state."""
        errors = []
        statuses = []

        def writer(success: bool) -> None:
            try:
                for _ in range(100):
                    sync_status.update(SyncResult(added=1, success=success, duration_seconds=0.01))
            except Exception as exc:
                errors.append(exc)

        def reader() -> None:
            try:
                for _ in range(100):
                    snap = sync_status.snapshot()
                    statuses.append(snap["status"])
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer, args=(True,)),
            threading.Thread(target=writer, args=(False,)),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert all(s in ("ok", "degraded", "unknown") for s in statuses)


class TestHealthServer:
    def test_health_endpoint_returns_json(self, sync_status):
        server = start_health_server(0, sync_status)  # port 0 = auto-assign
        port = server.server_address[1]
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/health")
            resp = conn.getresponse()

            assert resp.status == 200
            assert resp.getheader("Content-Type") == "application/json"
            body = json.loads(resp.read())
            assert body["status"] == "unknown"
            assert "uptime_seconds" in body
            conn.close()
        finally:
            server.shutdown()

    def test_health_endpoint_reflects_sync_result(self, sync_status):
        result = SyncResult(added=5, failed=0, success=True, duration_seconds=3.0)
        sync_status.update(result)

        server = start_health_server(0, sync_status)
        port = server.server_address[1]
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/health")
            resp = conn.getresponse()

            assert resp.status == 200
            body = json.loads(resp.read())
            assert body["status"] == "ok"
            assert body["last_sync"]["added"] == 5
            conn.close()
        finally:
            server.shutdown()

    def test_degraded_status_returns_503(self, sync_status):
        result = SyncResult(added=1, failed=3, success=False)
        sync_status.update(result)

        server = start_health_server(0, sync_status)
        port = server.server_address[1]
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/health")
            resp = conn.getresponse()

            assert resp.status == 503
            body = json.loads(resp.read())
            assert body["status"] == "degraded"
            conn.close()
        finally:
            server.shutdown()

    def test_404_for_unknown_path(self, sync_status):
        server = start_health_server(0, sync_status)
        port = server.server_address[1]
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/nonexistent")
            resp = conn.getresponse()

            assert resp.status == 404
            conn.close()
        finally:
            server.shutdown()

    def test_root_path_returns_health_json(self, sync_status):
        server = start_health_server(0, sync_status)
        port = server.server_address[1]
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/")
            resp = conn.getresponse()
            assert resp.status == 200
            body = json.loads(resp.read())
            assert "status" in body
            assert "uptime_seconds" in body
            conn.close()
        finally:
            server.shutdown()
