"""Tests for the pending queue functionality."""

import json
from datetime import datetime, timezone

from app.models import PendingShow
from app.pending_queue import PendingQueue


class TestPendingQueueBasics:
    """Basic pending queue operations."""

    def test_add_show_creates_pending_entry(self, tmp_path):
        """Adding a show creates a pending entry."""
        pq = PendingQueue(str(tmp_path))
        show = PendingShow(tvdb_id=12345, title="Test Show", source_type="trending")

        assert pq.add_show(show) is True
        assert pq.get_count() == 1

    def test_add_duplicate_returns_false(self, tmp_path):
        """Adding a duplicate show returns False."""
        pq = PendingQueue(str(tmp_path))
        show1 = PendingShow(tvdb_id=12345, title="Test Show", source_type="trending")
        show2 = PendingShow(tvdb_id=12345, title="Test Show 2", source_type="popular")

        assert pq.add_show(show1) is True
        assert pq.add_show(show2) is False
        assert pq.get_count() == 1

    def test_get_pending_returns_sorted_list(self, tmp_path):
        """get_pending returns shows sorted by discovery time."""
        pq = PendingQueue(str(tmp_path))
        show1 = PendingShow(tvdb_id=1, title="Show 1", discovered_at="2024-01-01T10:00:00+00:00")
        show2 = PendingShow(tvdb_id=2, title="Show 2", discovered_at="2024-01-01T09:00:00+00:00")

        pq.add_show(show1)
        pq.add_show(show2)

        pending = pq.get_pending()
        assert len(pending) == 2
        assert pending[0].tvdb_id == 2  # Earlier time first
        assert pending[1].tvdb_id == 1

    def test_is_pending_checks_existence(self, tmp_path):
        """is_pending returns True for pending shows."""
        pq = PendingQueue(str(tmp_path))
        show = PendingShow(tvdb_id=12345, title="Test Show")

        pq.add_show(show)
        assert pq.is_pending(12345) is True
        assert pq.is_pending(99999) is False

    def test_get_show_returns_show_or_none(self, tmp_path):
        """get_show returns the show or None."""
        pq = PendingQueue(str(tmp_path))
        show = PendingShow(tvdb_id=12345, title="Test Show", source_label="trending")

        pq.add_show(show)
        retrieved = pq.get_show(12345)
        assert retrieved is not None
        assert retrieved.title == "Test Show"
        assert retrieved.source_label == "trending"
        assert pq.get_show(99999) is None


class TestPendingQueueApproval:
    """Approve and reject operations."""

    def test_approve_show_removes_from_pending(self, tmp_path):
        """Approving a show removes it from pending."""
        pq = PendingQueue(str(tmp_path))
        show = PendingShow(tvdb_id=12345, title="Test Show")

        pq.add_show(show)
        approved = pq.approve_show(12345)

        assert approved is not None
        assert approved.tvdb_id == 12345
        assert pq.get_count() == 0
        assert pq.is_pending(12345) is False

    def test_approve_unknown_returns_none(self, tmp_path):
        """Approving unknown TVDB ID returns None."""
        pq = PendingQueue(str(tmp_path))
        assert pq.approve_show(99999) is None

    def test_reject_show_removes_from_pending(self, tmp_path):
        """Rejecting a show removes it from pending."""
        pq = PendingQueue(str(tmp_path))
        show = PendingShow(tvdb_id=12345, title="Test Show")

        pq.add_show(show)
        rejected = pq.reject_show(12345)

        assert rejected is not None
        assert rejected.tvdb_id == 12345
        assert pq.get_count() == 0

    def test_reject_unknown_returns_none(self, tmp_path):
        """Rejecting unknown TVDB ID returns None."""
        pq = PendingQueue(str(tmp_path))
        assert pq.reject_show(99999) is None


class TestPendingQueueBulk:
    """Bulk operations."""

    def test_bulk_approve_multiple_shows(self, tmp_path):
        """Bulk approve handles multiple shows."""
        pq = PendingQueue(str(tmp_path))
        pq.add_show(PendingShow(tvdb_id=1, title="Show 1"))
        pq.add_show(PendingShow(tvdb_id=2, title="Show 2"))
        pq.add_show(PendingShow(tvdb_id=3, title="Show 3"))

        approved = pq.bulk_approve([1, 2])

        assert len(approved) == 2
        assert pq.get_count() == 1
        assert pq.is_pending(3) is True

    def test_bulk_reject_multiple_shows(self, tmp_path):
        """Bulk reject handles multiple shows."""
        pq = PendingQueue(str(tmp_path))
        pq.add_show(PendingShow(tvdb_id=1, title="Show 1"))
        pq.add_show(PendingShow(tvdb_id=2, title="Show 2"))

        rejected = pq.bulk_reject([1, 2])

        assert len(rejected) == 2
        assert pq.get_count() == 0

    def test_bulk_approve_skips_unknown_ids(self, tmp_path):
        """Bulk approve skips unknown IDs gracefully."""
        pq = PendingQueue(str(tmp_path))
        pq.add_show(PendingShow(tvdb_id=1, title="Show 1"))

        approved = pq.bulk_approve([1, 99999])

        assert len(approved) == 1


class TestPendingQueuePersistence:
    """File persistence operations."""

    def test_saves_to_json_file(self, tmp_path):
        """Queue is saved to JSON file."""
        pq = PendingQueue(str(tmp_path))
        show = PendingShow(tvdb_id=12345, title="Test Show", source_type="trending")
        pq.add_show(show)

        # Create new instance to trigger reload
        pq2 = PendingQueue(str(tmp_path))
        assert pq2.is_pending(12345) is True
        assert pq2.get_show(12345).title == "Test Show"

    def test_file_format_structure(self, tmp_path):
        """JSON file has expected structure."""
        pq = PendingQueue(str(tmp_path))
        pq.add_show(PendingShow(tvdb_id=1, title="Show 1"))
        pq.approve_show(1)  # This adds to history

        with open(tmp_path / "pending_queue.json") as f:
            data = json.load(f)

        assert "pending" in data
        assert "history" in data
        assert isinstance(data["pending"], list)
        assert isinstance(data["history"], list)

    def test_handles_corrupted_file(self, tmp_path):
        """Corrupted file is handled gracefully."""
        pq_path = tmp_path / "pending_queue.json"
        pq_path.write_text("invalid json")

        pq = PendingQueue(str(tmp_path))
        assert pq.get_count() == 0  # Starts fresh

    def test_handles_missing_file(self, tmp_path):
        """Missing file is handled gracefully."""
        pq = PendingQueue(str(tmp_path))
        assert pq.get_count() == 0


class TestPendingQueueHistory:
    """History tracking."""

    def test_add_creates_history_entry(self, tmp_path):
        """Adding a show creates history entry."""
        pq = PendingQueue(str(tmp_path))
        pq.add_show(PendingShow(tvdb_id=1, title="Show 1"))

        history = pq.get_history()
        assert len(history) == 1
        assert history[0]["action"] == "added"
        assert history[0]["title"] == "Show 1"

    def test_approve_creates_history_entry(self, tmp_path):
        """Approving creates history entry."""
        pq = PendingQueue(str(tmp_path))
        pq.add_show(PendingShow(tvdb_id=1, title="Show 1"))
        pq.approve_show(1)

        history = pq.get_history()
        assert any(h["action"] == "approved" for h in history)

    def test_reject_creates_history_entry(self, tmp_path):
        """Rejecting creates history entry."""
        pq = PendingQueue(str(tmp_path))
        pq.add_show(PendingShow(tvdb_id=1, title="Show 1"))
        pq.reject_show(1)

        history = pq.get_history()
        assert any(h["action"] == "rejected" for h in history)

    def test_history_limited_to_100_entries(self, tmp_path):
        """History is limited to 100 entries."""
        pq = PendingQueue(str(tmp_path))
        for i in range(150):
            pq.add_show(PendingShow(tvdb_id=i, title=f"Show {i}"))

        assert len(pq.get_history()) == 100


class TestPendingQueueShowFields:
    """Show field preservation."""

    def test_all_fields_preserved(self, tmp_path):
        """All PendingShow fields are preserved."""
        pq = PendingQueue(str(tmp_path))
        show = PendingShow(
            tvdb_id=12345,
            title="Test Show",
            year=2024,
            imdb_id="tt12345",
            source_type="user_list",
            source_label="user_list:alice/my-list",
            discovered_at=datetime.now(timezone.utc).isoformat(),
            quality="hd1080p",
            required_words=["web-dl", "x265"],
        )

        pq.add_show(show)
        retrieved = pq.get_show(12345)

        assert retrieved.title == "Test Show"
        assert retrieved.year == 2024
        assert retrieved.imdb_id == "tt12345"
        assert retrieved.source_type == "user_list"
        assert retrieved.quality == "hd1080p"
        assert retrieved.required_words == ["web-dl", "x265"]


class TestPendingQueueClear:
    """Clear operation."""

    def test_clear_removes_all_pending(self, tmp_path):
        """Clear removes all pending shows."""
        pq = PendingQueue(str(tmp_path))
        pq.add_show(PendingShow(tvdb_id=1, title="Show 1"))
        pq.add_show(PendingShow(tvdb_id=2, title="Show 2"))

        count = pq.clear()

        assert count == 2
        assert pq.get_count() == 0


class TestPendingQueueRollback:
    """Rollback on save failure prevents silent data loss."""

    def test_add_show_rollback_on_save_failure(self, tmp_path):
        pq = PendingQueue(str(tmp_path))
        show = PendingShow(tvdb_id=1, title="Show 1")

        with pq._lock:
            pass  # ensure lock exists

        from unittest.mock import patch

        with patch.object(pq, "_save", side_effect=OSError("disk full")):
            with pq._lock:
                pass
            try:
                pq.add_show(show)
            except OSError:
                pass

        assert pq.is_pending(1) is False
        assert pq.get_count() == 0

    def test_approve_show_rollback_on_save_failure(self, tmp_path):
        pq = PendingQueue(str(tmp_path))
        show = PendingShow(tvdb_id=1, title="Show 1")
        pq.add_show(show)

        from unittest.mock import patch

        with patch.object(pq, "_save", side_effect=OSError("disk full")):
            try:
                pq.approve_show(1)
            except OSError:
                pass

        assert pq.is_pending(1) is True
        retrieved = pq.get_show(1)
        assert retrieved.status == "pending"

    def test_reject_show_rollback_on_save_failure(self, tmp_path):
        pq = PendingQueue(str(tmp_path))
        show = PendingShow(tvdb_id=1, title="Show 1")
        pq.add_show(show)

        from unittest.mock import patch

        with patch.object(pq, "_save", side_effect=OSError("disk full")):
            try:
                pq.reject_show(1)
            except OSError:
                pass

        assert pq.is_pending(1) is True
        retrieved = pq.get_show(1)
        assert retrieved.status == "pending"

    def test_bulk_approve_rollback_on_save_failure(self, tmp_path):
        pq = PendingQueue(str(tmp_path))
        pq.add_show(PendingShow(tvdb_id=1, title="Show 1"))
        pq.add_show(PendingShow(tvdb_id=2, title="Show 2"))

        from unittest.mock import patch

        with patch.object(pq, "_save", side_effect=OSError("disk full")):
            try:
                pq.bulk_approve([1, 2])
            except OSError:
                pass

        assert pq.get_count() == 2
        assert pq.get_show(1).status == "pending"
        assert pq.get_show(2).status == "pending"

    def test_bulk_reject_rollback_on_save_failure(self, tmp_path):
        pq = PendingQueue(str(tmp_path))
        pq.add_show(PendingShow(tvdb_id=1, title="Show 1"))
        pq.add_show(PendingShow(tvdb_id=2, title="Show 2"))

        from unittest.mock import patch

        with patch.object(pq, "_save", side_effect=OSError("disk full")):
            try:
                pq.bulk_reject([1, 2])
            except OSError:
                pass

        assert pq.get_count() == 2
        assert pq.get_show(1).status == "pending"
        assert pq.get_show(2).status == "pending"

    def test_clear_rollback_on_save_failure(self, tmp_path):
        pq = PendingQueue(str(tmp_path))
        pq.add_show(PendingShow(tvdb_id=1, title="Show 1"))

        from unittest.mock import patch

        with patch.object(pq, "_save", side_effect=OSError("disk full")):
            try:
                pq.clear()
            except OSError:
                pass

        assert pq.get_count() == 1
        assert pq.is_pending(1) is True
