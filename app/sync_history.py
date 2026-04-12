"""SQLite-backed persistent storage for sync history."""

import json
import logging
import sqlite3
import threading
import time

log = logging.getLogger(__name__)

DB_FILENAME = "sync_history.db"

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS sync_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT    NOT NULL,
    duration_seconds  REAL    NOT NULL,
    total_fetched     INTEGER NOT NULL DEFAULT 0,
    unique_shows      INTEGER NOT NULL DEFAULT 0,
    already_in_medusa INTEGER NOT NULL DEFAULT 0,
    added             INTEGER NOT NULL DEFAULT 0,
    queued            INTEGER NOT NULL DEFAULT 0,
    skipped           INTEGER NOT NULL DEFAULT 0,
    failed            INTEGER NOT NULL DEFAULT 0,
    success           INTEGER NOT NULL DEFAULT 1,
    per_source        TEXT    NOT NULL DEFAULT '{}',
    added_shows       TEXT    NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS sync_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES sync_runs(id) ON DELETE CASCADE,
    tvdb_id      INTEGER NOT NULL,
    title        TEXT    NOT NULL,
    year         INTEGER,
    imdb_id      TEXT,
    action       TEXT    NOT NULL,
    source_label TEXT,
    reason       TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_items_run_id ON sync_items(run_id);
CREATE INDEX IF NOT EXISTS idx_sync_runs_timestamp ON sync_runs(timestamp DESC);
"""


class SyncHistoryDB:
    """SQLite-backed persistent storage for sync history."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or ":memory:"
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            self._conn.executescript(_SCHEMA_V1)
            self._conn.execute("PRAGMA user_version=1")
            self._conn.commit()

    def record(self, result, sync_time: float | None = None) -> int:
        """Persist a SyncResult and its per-item actions. Returns the run ID."""
        if sync_time is None:
            sync_time = time.time()
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(sync_time))

        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO sync_runs
                   (timestamp, duration_seconds, total_fetched, unique_shows,
                    already_in_medusa, added, queued, skipped, failed,
                    success, per_source, added_shows)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    timestamp,
                    round(result.duration_seconds, 1),
                    result.total_fetched,
                    result.unique_shows,
                    result.already_in_medusa,
                    result.added,
                    result.queued,
                    result.skipped,
                    result.failed,
                    1 if result.success else 0,
                    json.dumps(dict(result.per_source)),
                    json.dumps(list(result.added_shows) if result.added_shows else []),
                ),
            )
            run_id = cur.lastrowid

            show_actions = getattr(result, "show_actions", None) or []
            if show_actions:
                self._conn.executemany(
                    """INSERT INTO sync_items
                       (run_id, tvdb_id, title, year, imdb_id, action, source_label, reason)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            run_id,
                            a["tvdb_id"],
                            a["title"],
                            a.get("year"),
                            a.get("imdb_id"),
                            a["action"],
                            a.get("source_label", ""),
                            a.get("reason"),
                        )
                        for a in show_actions
                    ],
                )

            self._conn.commit()
            return run_id

    def get_history(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Return sync run entries (newest first) with their per-item actions."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sync_runs ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()

            entries = []
            for row in rows:
                entry = self._row_to_dict(row)
                items = self._conn.execute(
                    "SELECT * FROM sync_items WHERE run_id = ? ORDER BY id",
                    (row["id"],),
                ).fetchall()
                entry["show_actions"] = [self._item_to_dict(i) for i in items]
                entries.append(entry)
            return entries

    def get_run_items(self, run_id: int) -> list[dict]:
        """Return per-show action items for a specific sync run."""
        with self._lock:
            items = self._conn.execute(
                "SELECT * FROM sync_items WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
            return [self._item_to_dict(i) for i in items]

    def get_total_runs(self) -> int:
        """Return total count of sync runs."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM sync_runs").fetchone()
            return row[0]

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "duration_seconds": row["duration_seconds"],
            "total_fetched": row["total_fetched"],
            "unique_shows": row["unique_shows"],
            "already_in_medusa": row["already_in_medusa"],
            "added": row["added"],
            "queued": row["queued"],
            "skipped": row["skipped"],
            "failed": row["failed"],
            "success": bool(row["success"]),
            "per_source": json.loads(row["per_source"]),
            "added_shows": json.loads(row["added_shows"]),
        }

    @staticmethod
    def _item_to_dict(item) -> dict:
        return {
            "tvdb_id": item["tvdb_id"],
            "title": item["title"],
            "year": item["year"],
            "imdb_id": item["imdb_id"],
            "action": item["action"],
            "source_label": item["source_label"],
            "reason": item["reason"],
        }
