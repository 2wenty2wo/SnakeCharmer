"""Pending queue storage and management for manual show approval."""

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.models import PendingShow

log = logging.getLogger(__name__)

PENDING_QUEUE_FILE = "pending_queue.json"
MAX_HISTORY = 100


class PendingQueue:
    """Thread-safe pending queue with JSON file persistence."""

    def __init__(self, config_dir: str = "."):
        self._path = Path(config_dir) / PENDING_QUEUE_FILE
        self._lock = threading.Lock()
        self._pending: dict[int, PendingShow] = {}
        self._history: list[dict] = []
        self._load()

    def _load(self) -> None:
        """Load pending queue from JSON file."""
        if not self._path.exists():
            return
        try:
            with open(self._path) as f:
                data = json.load(f)
            for item in data.get("pending", []):
                show = self._dict_to_show(item)
                self._pending[show.tvdb_id] = show
            self._history = data.get("history", [])[:MAX_HISTORY]
            log.debug("Loaded %d pending shows from %s", len(self._pending), self._path)
        except (json.JSONDecodeError, OSError, KeyError, TypeError) as e:
            log.warning("Failed to load pending queue: %s", e)

    def _save(self) -> None:
        """Save pending queue to JSON file atomically."""
        data = {
            "pending": [self._show_to_dict(s) for s in self._pending.values()],
            "history": self._history[:MAX_HISTORY],
        }
        try:
            tmp_path = self._path.with_suffix(".tmp")
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, self._path)
        except OSError as e:
            log.error("Failed to save pending queue: %s", e)
            raise

    def _show_to_dict(self, show: PendingShow) -> dict:
        """Convert PendingShow to dict for JSON serialization."""
        return {
            "tvdb_id": show.tvdb_id,
            "title": show.title,
            "year": show.year,
            "imdb_id": show.imdb_id,
            "source_type": show.source_type,
            "source_label": show.source_label,
            "discovered_at": show.discovered_at,
            "status": show.status,
            "quality": show.quality,
            "required_words": show.required_words,
            "poster_url": show.poster_url,
            "network": show.network,
            "genres": show.genres,
        }

    def _dict_to_show(self, data: dict) -> PendingShow:
        """Convert dict to PendingShow."""
        return PendingShow(
            tvdb_id=int(data["tvdb_id"]),
            title=str(data.get("title", "Unknown")),
            year=data.get("year"),
            imdb_id=data.get("imdb_id"),
            source_type=str(data.get("source_type", "")),
            source_label=str(data.get("source_label", "")),
            discovered_at=str(data.get("discovered_at", "")),
            status=str(data.get("status", "pending")),
            quality=data.get("quality"),
            required_words=list(data.get("required_words", [])),
            poster_url=data.get("poster_url"),
            network=data.get("network"),
            genres=list(data.get("genres", [])),
        )

    def _now_iso(self) -> str:
        """Return current timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()

    def _add_to_history(self, show: PendingShow, action: str) -> None:
        """Add an action to the history."""
        entry = {
            "tvdb_id": show.tvdb_id,
            "title": show.title,
            "action": action,  # "added", "approved", "rejected"
            "timestamp": self._now_iso(),
            "source_label": show.source_label,
        }
        self._history.insert(0, entry)
        self._history = self._history[:MAX_HISTORY]

    def add_show(self, show: PendingShow) -> bool:
        """Add a show to the pending queue.

        Returns True if added, False if already exists (by TVDB ID).
        """
        with self._lock:
            if show.tvdb_id in self._pending:
                log.debug("Show already in pending queue: %s (tvdb:%d)", show.title, show.tvdb_id)
                return False
            if not show.discovered_at:
                show.discovered_at = self._now_iso()
            self._pending[show.tvdb_id] = show
            self._add_to_history(show, "added")
            self._save()
            log.info("Added to pending queue: %s (tvdb:%d)", show.title, show.tvdb_id)
            return True

    def get_pending(self) -> list[PendingShow]:
        """Return all pending shows sorted by discovery time (oldest first)."""
        with self._lock:
            shows = [s for s in self._pending.values() if s.status == "pending"]
            return sorted(shows, key=lambda s: s.discovered_at or "")

    def get_show(self, tvdb_id: int) -> PendingShow | None:
        """Get a specific show by TVDB ID."""
        with self._lock:
            return self._pending.get(tvdb_id)

    def approve_show(self, tvdb_id: int) -> PendingShow | None:
        """Mark a show as approved and return it.

        Returns None if show not found.
        """
        with self._lock:
            show = self._pending.get(tvdb_id)
            if show is None:
                return None
            show.status = "approved"
            self._add_to_history(show, "approved")
            del self._pending[tvdb_id]
            self._save()
            log.info("Approved from pending queue: %s (tvdb:%d)", show.title, show.tvdb_id)
            return show

    def reject_show(self, tvdb_id: int) -> PendingShow | None:
        """Mark a show as rejected and remove it.

        Returns None if show not found.
        """
        with self._lock:
            show = self._pending.get(tvdb_id)
            if show is None:
                return None
            show.status = "rejected"
            self._add_to_history(show, "rejected")
            del self._pending[tvdb_id]
            self._save()
            log.info("Rejected from pending queue: %s (tvdb:%d)", show.title, show.tvdb_id)
            return show

    def bulk_approve(self, tvdb_ids: list[int]) -> list[PendingShow]:
        """Approve multiple shows at once."""
        approved = []
        with self._lock:
            for tvdb_id in tvdb_ids:
                show = self._pending.get(tvdb_id)
                if show and show.status == "pending":
                    show.status = "approved"
                    self._add_to_history(show, "approved")
                    del self._pending[tvdb_id]
                    approved.append(show)
            if approved:
                self._save()
                log.info("Bulk approved %d shows from pending queue", len(approved))
        return approved

    def bulk_reject(self, tvdb_ids: list[int]) -> list[PendingShow]:
        """Reject multiple shows at once."""
        rejected = []
        with self._lock:
            for tvdb_id in tvdb_ids:
                show = self._pending.get(tvdb_id)
                if show and show.status == "pending":
                    show.status = "rejected"
                    self._add_to_history(show, "rejected")
                    del self._pending[tvdb_id]
                    rejected.append(show)
            if rejected:
                self._save()
                log.info("Bulk rejected %d shows from pending queue", len(rejected))
        return rejected

    def is_pending(self, tvdb_id: int) -> bool:
        """Check if a show is in the pending queue."""
        with self._lock:
            return tvdb_id in self._pending

    def get_count(self) -> int:
        """Return the number of pending shows."""
        with self._lock:
            return len([s for s in self._pending.values() if s.status == "pending"])

    def get_history(self) -> list[dict]:
        """Return action history (newest first)."""
        with self._lock:
            return list(self._history)

    def clear(self) -> int:
        """Clear all pending shows (for testing). Returns count cleared."""
        with self._lock:
            count = len(self._pending)
            self._pending.clear()
            self._save()
            return count
