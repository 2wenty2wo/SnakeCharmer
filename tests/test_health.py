import json
import re
import threading
from http.client import HTTPConnection

import pytest

from app.health import SyncStatus, start_health_server
from app.sync import SyncResult
from app.sync_history import SyncHistoryDB


@pytest.fixture
def sync_status():
    return SyncStatus()


@pytest.fixture
def db_sync_status(tmp_path):
    db = SyncHistoryDB(str(tmp_path / "test_history.db"))
    return SyncStatus(_db=db)


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

    def test_update_with_db_record_failure_keeps_in_memory_history(self):
        class BrokenDB:
            def record(self, result, timestamp):
                raise RuntimeError("db write failed")

        sync_status = SyncStatus(_db=BrokenDB())
        sync_status.update(SyncResult(added=4, success=True))

        history = sync_status.get_history()
        assert len(history) == 1
        assert history[0]["added"] == 4


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


class TestHealthSnapshotSchema:
    """Validate the full JSON schema of health endpoint responses."""

    def test_unknown_snapshot_schema(self, sync_status):
        snap = sync_status.snapshot()
        assert set(snap.keys()) == {"status", "uptime_seconds"}
        assert snap["status"] == "unknown"
        assert isinstance(snap["uptime_seconds"], float)
        assert snap["uptime_seconds"] >= 0

    def test_ok_snapshot_has_all_last_sync_keys(self, sync_status):
        result = SyncResult(
            added=2,
            skipped=1,
            failed=0,
            unique_shows=8,
            already_in_medusa=5,
            duration_seconds=3.14,
            success=True,
        )
        sync_status.update(result)
        snap = sync_status.snapshot()

        assert set(snap.keys()) == {"status", "uptime_seconds", "last_sync"}
        assert snap["status"] == "ok"
        last_sync = snap["last_sync"]
        expected_keys = {
            "timestamp",
            "duration_seconds",
            "added",
            "queued",
            "skipped",
            "failed",
            "unique_shows",
            "already_in_medusa",
        }
        assert set(last_sync.keys()) == expected_keys

        # Validate types
        assert isinstance(last_sync["timestamp"], str)
        assert isinstance(last_sync["duration_seconds"], float)
        for int_key in (
            "added",
            "queued",
            "skipped",
            "failed",
            "unique_shows",
            "already_in_medusa",
        ):
            assert isinstance(last_sync[int_key], int)

        # Validate timestamp format (ISO 8601)
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", last_sync["timestamp"])

    def test_degraded_snapshot_schema(self, sync_status):
        result = SyncResult(added=0, failed=3, success=False, duration_seconds=1.0)
        sync_status.update(result)
        snap = sync_status.snapshot()

        assert snap["status"] == "degraded"
        assert "last_sync" in snap
        assert snap["last_sync"]["failed"] == 3

    def test_uptime_is_positive_number(self, sync_status):
        snap = sync_status.snapshot()
        assert snap["uptime_seconds"] >= 0
        assert isinstance(snap["uptime_seconds"], float)

    def test_concurrent_reads_produce_consistent_snapshots(self, sync_status):
        """Verify that concurrent get/update never produces torn reads
        (e.g., status=ok but last_sync from a failed run)."""
        errors = []

        def writer(success: bool) -> None:
            try:
                for _ in range(200):
                    sync_status.update(
                        SyncResult(
                            added=1 if success else 0,
                            failed=0 if success else 5,
                            success=success,
                            duration_seconds=0.01,
                        )
                    )
            except Exception as exc:
                errors.append(exc)

        def reader() -> None:
            try:
                for _ in range(200):
                    snap = sync_status.snapshot()
                    if "last_sync" in snap:
                        if snap["status"] == "ok":
                            assert snap["last_sync"]["failed"] == 0
                        elif snap["status"] == "degraded":
                            assert snap["last_sync"]["failed"] == 5
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer, args=(True,)),
            threading.Thread(target=writer, args=(False,)),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors


class TestSyncStatusGetTotals:
    def test_get_totals_empty(self, sync_status):
        totals = sync_status.get_totals()
        assert totals["total_runs"] == 0
        assert totals["total_added"] == 0
        assert totals["success_rate"] == 0

    def test_get_totals_in_memory(self, sync_status):
        sync_status.update(SyncResult(added=3, failed=0, success=True))
        sync_status.update(SyncResult(added=2, failed=1, success=False))
        totals = sync_status.get_totals()
        assert totals["total_runs"] == 2
        assert totals["total_added"] == 5
        assert totals["total_failed"] == 1
        assert totals["success_rate"] == 50

    def test_get_totals_with_db(self, db_sync_status):
        db_sync_status.update(SyncResult(added=4, queued=1, failed=0, success=True))
        db_sync_status.update(SyncResult(added=1, queued=0, failed=2, success=False))
        totals = db_sync_status.get_totals()
        assert totals["total_runs"] == 2
        assert totals["total_added"] == 5
        assert totals["total_queued"] == 1
        assert totals["total_failed"] == 2
        assert totals["success_rate"] == 50

    def test_get_totals_falls_back_on_db_error(self):
        class BrokenTotalsDB:
            def record(self, result, timestamp):
                return None

            def get_totals(self):
                raise RuntimeError("db totals failed")

        sync_status = SyncStatus(_db=BrokenTotalsDB())
        sync_status.update(SyncResult(added=7, failed=0, success=True))
        totals = sync_status.get_totals()
        assert totals["total_runs"] == 1
        assert totals["total_added"] == 7
        assert totals["success_rate"] == 100


class TestSyncStatusWithDB:
    """Tests for SyncStatus backed by SyncHistoryDB."""

    def test_history_persisted(self, db_sync_status):
        db_sync_status.update(SyncResult(added=3, success=True))
        history = db_sync_status.get_history()
        assert len(history) == 1
        assert history[0]["added"] == 3

    def test_history_no_cap(self, db_sync_status):
        """DB-backed history is not limited to 20 entries."""
        for i in range(25):
            db_sync_status.update(SyncResult(added=i, success=True))
        assert db_sync_status.get_total_runs() == 25
        history = db_sync_status.get_history(limit=30)
        assert len(history) == 25

    def test_pagination(self, db_sync_status):
        for i in range(10):
            db_sync_status.update(SyncResult(added=i, success=True))
        page1 = db_sync_status.get_history(limit=3, offset=0)
        page2 = db_sync_status.get_history(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3
        assert page1[0]["added"] == 9
        assert page2[0]["added"] == 6

    def test_get_total_runs(self, db_sync_status):
        assert db_sync_status.get_total_runs() == 0
        db_sync_status.update(SyncResult(added=1, success=True))
        assert db_sync_status.get_total_runs() == 1

    def test_snapshot_still_works(self, db_sync_status):
        result = SyncResult(added=5, failed=0, success=True, duration_seconds=2.5)
        db_sync_status.update(result)
        snap = db_sync_status.snapshot()
        assert snap["status"] == "ok"
        assert snap["last_sync"]["added"] == 5

    def test_show_actions_persisted(self, db_sync_status):
        result = SyncResult(
            added=1,
            success=True,
            show_actions=[
                {
                    "tvdb_id": 100,
                    "title": "Test Show",
                    "year": 2024,
                    "imdb_id": "tt100",
                    "action": "added",
                    "source_label": "trending",
                    "reason": None,
                },
            ],
        )
        db_sync_status.update(result)
        history = db_sync_status.get_history()
        assert len(history[0]["show_actions"]) == 1
        assert history[0]["show_actions"][0]["title"] == "Test Show"

    def test_get_history_falls_back_to_memory_if_db_read_fails(self):
        class BrokenReadDB:
            def get_history(self, limit, offset):
                raise RuntimeError("db read failed")

            def get_total_runs(self):
                return 99

        sync_status = SyncStatus(_db=BrokenReadDB())
        sync_status.update(SyncResult(added=7, success=True))

        history = sync_status.get_history(limit=10, offset=0)
        assert len(history) == 1
        assert history[0]["added"] == 7

    def test_get_total_runs_falls_back_to_memory_if_db_count_fails(self):
        class BrokenCountDB:
            def record(self, result, timestamp):
                return None

            def get_history(self, limit, offset):
                return []

            def get_total_runs(self):
                raise RuntimeError("db count failed")

        sync_status = SyncStatus(_db=BrokenCountDB())
        sync_status.update(SyncResult(added=1, success=True))
        sync_status.update(SyncResult(added=2, success=True))

        assert sync_status.get_total_runs() == 2
