import sqlite3
import threading

import pytest

from app.sync import SyncResult
from app.sync_history import SyncHistoryDB


@pytest.fixture
def db(tmp_path):
    return SyncHistoryDB(str(tmp_path / "test_history.db"))


@pytest.fixture
def sample_result():
    return SyncResult(
        total_fetched=10,
        unique_shows=8,
        already_in_medusa=5,
        added=2,
        queued=1,
        skipped=0,
        failed=0,
        duration_seconds=3.14,
        per_source={"trending": 6, "popular": 4},
        success=True,
        added_shows=[
            {"title": "Show A", "tvdb_id": 100, "year": 2024, "imdb_id": "tt1000"},
            {"title": "Show B", "tvdb_id": 200, "year": 2023, "imdb_id": None},
        ],
        show_actions=[
            {
                "tvdb_id": 100,
                "title": "Show A",
                "year": 2024,
                "imdb_id": "tt1000",
                "action": "added",
                "source_label": "trending",
                "reason": None,
            },
            {
                "tvdb_id": 200,
                "title": "Show B",
                "year": 2023,
                "imdb_id": None,
                "action": "added",
                "source_label": "trending",
                "reason": None,
            },
            {
                "tvdb_id": 300,
                "title": "Show C",
                "year": 2022,
                "imdb_id": "tt3000",
                "action": "queued",
                "source_label": "popular",
                "reason": None,
            },
        ],
    )


class TestSyncHistoryDB:
    def test_creates_db_file(self, tmp_path):
        path = str(tmp_path / "history.db")
        SyncHistoryDB(path)
        assert (tmp_path / "history.db").exists()

    def test_in_memory_mode(self):
        db = SyncHistoryDB()
        assert db.get_total_runs() == 0
        db.close()

    def test_wal_mode(self, tmp_path):
        path = str(tmp_path / "history.db")
        db = SyncHistoryDB(path)
        conn = sqlite3.connect(path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"
        db.close()

    def test_schema_version(self, tmp_path):
        path = str(tmp_path / "history.db")
        db = SyncHistoryDB(path)
        conn = sqlite3.connect(path)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert version == 1
        db.close()

    def test_empty_db(self, db):
        assert db.get_history() == []
        assert db.get_total_runs() == 0

    def test_record_and_retrieve(self, db, sample_result):
        run_id = db.record(sample_result, sync_time=1700000000.0)
        assert run_id >= 1

        history = db.get_history()
        assert len(history) == 1
        entry = history[0]
        assert entry["added"] == 2
        assert entry["queued"] == 1
        assert entry["skipped"] == 0
        assert entry["failed"] == 0
        assert entry["unique_shows"] == 8
        assert entry["already_in_medusa"] == 5
        assert entry["duration_seconds"] == 3.1
        assert entry["success"] is True
        assert entry["per_source"] == {"trending": 6, "popular": 4}
        assert len(entry["added_shows"]) == 2
        assert entry["added_shows"][0]["title"] == "Show A"

    def test_show_actions_stored(self, db, sample_result):
        db.record(sample_result)
        history = db.get_history()
        actions = history[0]["show_actions"]
        assert len(actions) == 3
        added = [a for a in actions if a["action"] == "added"]
        queued = [a for a in actions if a["action"] == "queued"]
        assert len(added) == 2
        assert len(queued) == 1
        assert queued[0]["title"] == "Show C"

    def test_get_run_items(self, db, sample_result):
        run_id = db.record(sample_result)
        items = db.get_run_items(run_id)
        assert len(items) == 3
        assert items[0]["tvdb_id"] == 100

    def test_get_run_items_empty(self, db):
        assert db.get_run_items(999) == []

    def test_history_ordering_newest_first(self, db):
        db.record(SyncResult(added=1, success=True), sync_time=1000.0)
        db.record(SyncResult(added=2, success=True), sync_time=2000.0)
        db.record(SyncResult(added=3, success=True), sync_time=3000.0)

        history = db.get_history()
        assert len(history) == 3
        assert history[0]["added"] == 3  # newest first
        assert history[1]["added"] == 2
        assert history[2]["added"] == 1

    def test_pagination_limit(self, db):
        for i in range(10):
            db.record(SyncResult(added=i, success=True))
        history = db.get_history(limit=3)
        assert len(history) == 3
        assert history[0]["added"] == 9

    def test_pagination_offset(self, db):
        for i in range(10):
            db.record(SyncResult(added=i, success=True))
        history = db.get_history(limit=3, offset=3)
        assert len(history) == 3
        assert history[0]["added"] == 6

    def test_get_total_runs(self, db):
        assert db.get_total_runs() == 0
        db.record(SyncResult(added=1, success=True))
        db.record(SyncResult(added=2, success=True))
        assert db.get_total_runs() == 2

    def test_per_source_json_roundtrip(self, db):
        result = SyncResult(added=1, success=True, per_source={"trending": 5, "watchlist": 3})
        db.record(result)
        entry = db.get_history()[0]
        assert entry["per_source"] == {"trending": 5, "watchlist": 3}

    def test_success_boolean_roundtrip(self, db):
        db.record(SyncResult(success=True))
        db.record(SyncResult(success=False))
        history = db.get_history()
        assert history[0]["success"] is False
        assert history[1]["success"] is True

    def test_result_without_show_actions(self, db):
        """SyncResult without show_actions field still works."""
        result = SyncResult(added=1, success=True)
        db.record(result)
        entry = db.get_history()[0]
        assert entry["show_actions"] == []

    def test_concurrent_access(self, tmp_path):
        db = SyncHistoryDB(str(tmp_path / "concurrent.db"))
        errors = []

        def writer(start_val):
            try:
                for i in range(50):
                    db.record(SyncResult(added=start_val + i, success=True))
            except Exception as exc:
                errors.append(exc)

        def reader():
            try:
                for _ in range(50):
                    db.get_history(limit=10)
                    db.get_total_runs()
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer, args=(0,)),
            threading.Thread(target=writer, args=(100,)),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert db.get_total_runs() == 100
        db.close()

    def test_reopening_db_preserves_data(self, tmp_path):
        path = str(tmp_path / "reopen.db")
        db1 = SyncHistoryDB(path)
        db1.record(SyncResult(added=5, success=True))
        db1.close()

        db2 = SyncHistoryDB(path)
        assert db2.get_total_runs() == 1
        assert db2.get_history()[0]["added"] == 5
        db2.close()

    def test_no_limit_on_history_size(self, db):
        """Unlike the old in-memory store, SQLite has no 20-entry cap."""
        for i in range(30):
            db.record(SyncResult(added=i, success=True))
        assert db.get_total_runs() == 30
        all_entries = db.get_history(limit=100)
        assert len(all_entries) == 30

    def test_show_action_reason_stored(self, db):
        result = SyncResult(
            failed=1,
            success=False,
            show_actions=[
                {
                    "tvdb_id": 999,
                    "title": "Broken Show",
                    "year": None,
                    "imdb_id": None,
                    "action": "failed",
                    "source_label": "trending",
                    "reason": "Connection timeout",
                },
            ],
        )
        db.record(result)
        actions = db.get_history()[0]["show_actions"]
        assert actions[0]["reason"] == "Connection timeout"
