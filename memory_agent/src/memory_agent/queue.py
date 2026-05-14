"""SQLite-backed strict FIFO queue for high-level memory work."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import contextlib
import json
import sqlite3
import time
from typing import Any, Literal

QueueKind = Literal["recall", "remember", "rem_sleep"]
QueueStatus = Literal["pending", "processing", "succeeded", "failed"]


@dataclass(frozen=True)
class QueueItem:
    id: int
    kind: QueueKind
    input: str
    status: QueueStatus
    attempts: int
    created_at: float
    updated_at: float
    completed_at: float | None = None
    last_error: str | None = None
    result: str | None = None


class MemoryQueue:
    """Small SQLite queue that preserves arrival order by monotonically increasing id."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connect().close()
        self.init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_db(self) -> None:
        with contextlib.closing(self._connect()) as conn:
            self._ensure_queue_items_table(conn)
            self._ensure_state_table(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_queue_claim ON queue_items(status, id)"
            )

    def _ensure_queue_items_table(self, conn: sqlite3.Connection) -> None:
        desired_sql = """
            CREATE TABLE queue_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK (kind IN ('recall', 'remember', 'rem_sleep')),
                input TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'succeeded', 'failed')),
                attempts INTEGER NOT NULL DEFAULT 0,
                result TEXT,
                last_error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                claimed_at REAL,
                completed_at REAL
            )
        """
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'queue_items'").fetchone()
        if row is None:
            conn.execute(desired_sql)
            return
        existing_sql = str(row["sql"] or "")
        if "'rem_sleep'" in existing_sql and "completed_at" in existing_sql:
            return
        conn.execute("ALTER TABLE queue_items RENAME TO queue_items_old")
        conn.execute(desired_sql)
        old_columns = {column[1] for column in conn.execute("PRAGMA table_info(queue_items_old)").fetchall()}
        claimed_expr = "claimed_at" if "claimed_at" in old_columns else "NULL"
        completed_expr = "completed_at" if "completed_at" in old_columns else "NULL"
        conn.execute(
            f"""
            INSERT INTO queue_items(id, kind, input, status, attempts, result, last_error, created_at, updated_at, claimed_at, completed_at)
            SELECT id,
                   kind,
                   input, status, attempts, result, last_error, created_at, updated_at, {claimed_expr}, {completed_expr}
            FROM queue_items_old
            WHERE kind IN ('recall', 'remember', 'rem_sleep')
            """
        )
        conn.execute("DROP TABLE queue_items_old")

    def _ensure_state_table(self, conn: sqlite3.Connection) -> None:
        now = time.time()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS queue_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO queue_state(key, value, updated_at) VALUES ('successful_remembers_since_rem_sleep', '0', ?)",
            (now,),
        )

    def enqueue(self, kind: QueueKind, input_text: str) -> int:
        now = time.time()
        with contextlib.closing(self._connect()) as conn:
            cur = conn.execute(
                """
                INSERT INTO queue_items(kind, input, status, attempts, created_at, updated_at)
                VALUES (?, ?, 'pending', 0, ?, ?)
                """,
                (kind, input_text, now, now),
            )
            return int(cur.lastrowid)

    def enqueue_rem_sleep(self, input_text: str = "Manual REM sleep request") -> int:
        now = time.time()
        with contextlib.closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                INSERT INTO queue_items(kind, input, status, attempts, created_at, updated_at)
                VALUES ('rem_sleep', ?, 'pending', 0, ?, ?)
                """,
                (input_text, now, now),
            )
            self._set_remember_count(conn, 0, now)
            conn.execute("COMMIT")
            return int(cur.lastrowid)

    def record_successful_remember_and_maybe_enqueue_rem_sleep(self, enabled: bool, threshold: int) -> int | None:
        if not enabled or threshold <= 0:
            return None
        now = time.time()
        with contextlib.closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            count = self._get_remember_count(conn) + 1
            if count < threshold:
                self._set_remember_count(conn, count, now)
                conn.execute("COMMIT")
                return None
            cur = conn.execute(
                """
                INSERT INTO queue_items(kind, input, status, attempts, created_at, updated_at)
                VALUES ('rem_sleep', ?, 'pending', 0, ?, ?)
                """,
                (f"Automatic REM sleep after {threshold} successful remember jobs", now, now),
            )
            self._set_remember_count(conn, 0, now)
            conn.execute("COMMIT")
            return int(cur.lastrowid)

    def remember_count_since_rem_sleep(self) -> int:
        with contextlib.closing(self._connect()) as conn:
            return self._get_remember_count(conn)

    def delete_pending_rem_sleep(self, item_id: int) -> bool:
        with contextlib.closing(self._connect()) as conn:
            cur = conn.execute("DELETE FROM queue_items WHERE id = ? AND kind = 'rem_sleep' AND status = 'pending' AND attempts = 0", (item_id,))
            return int(cur.rowcount) == 1

    def list_items(self, limit: int = 200) -> list[QueueItem]:
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM queue_items ORDER BY id ASC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_item(row) for row in rows]

    def status_counts(self) -> dict[str, int]:
        counts = {"total": 0, "pending": 0, "processing": 0, "succeeded": 0, "failed": 0}
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM queue_items GROUP BY status"
            ).fetchall()
        for row in rows:
            count = int(row["count"])
            counts[str(row["status"])] = count
            counts["total"] += count
        return counts

    @staticmethod
    def _get_remember_count(conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT value FROM queue_state WHERE key = 'successful_remembers_since_rem_sleep'").fetchone()
        if row is None:
            return 0
        return int(row["value"])

    @staticmethod
    def _set_remember_count(conn: sqlite3.Connection, count: int, now: float) -> None:
        conn.execute(
            """
            INSERT INTO queue_state(key, value, updated_at)
            VALUES ('successful_remembers_since_rem_sleep', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (str(count), now),
        )

    def claim_next(self, processing_lease_seconds: float = 600.0, max_retries: int | None = None) -> QueueItem | None:
        """Atomically claim the oldest available item without bypassing FIFO.

        The oldest non-terminal row controls progress. If it is still processing and
        its lease has not expired, newer pending rows are not claimed. If the lease
        has expired, the row is reclaimed for retry before any newer row.
        """
        now = time.time()
        with contextlib.closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            while True:
                row = conn.execute(
                    """
                    SELECT * FROM queue_items
                    WHERE status IN ('pending', 'processing')
                    ORDER BY id ASC LIMIT 1
                    """
                ).fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    return None
                if row["status"] == "processing":
                    claimed_at = float(row["claimed_at"] or row["updated_at"] or row["created_at"])
                    if claimed_at + processing_lease_seconds > now:
                        conn.execute("COMMIT")
                        return None
                    if max_retries is not None and int(row["attempts"]) >= max_retries + 1:
                        conn.execute(
                            """
                            UPDATE queue_items
                            SET status = 'failed', last_error = ?, updated_at = ?, claimed_at = NULL, completed_at = ?
                            WHERE id = ?
                            """,
                            ("processing lease expired after maximum retries", now, now, row["id"]),
                        )
                        continue
                break
            conn.execute(
                """
                UPDATE queue_items
                SET status = 'processing', attempts = attempts + 1, updated_at = ?, claimed_at = ?
                WHERE id = ?
                """,
                (now, now, row["id"]),
            )
            claimed = conn.execute("SELECT * FROM queue_items WHERE id = ?", (row["id"],)).fetchone()
            conn.execute("COMMIT")
            return self._row_to_item(claimed)

    def reset_stale_processing(self, processing_lease_seconds: float = 600.0) -> int:
        """Return expired processing rows to pending so they can be retried in FIFO order."""
        now = time.time()
        cutoff = now - processing_lease_seconds
        with contextlib.closing(self._connect()) as conn:
            cur = conn.execute(
                """
                UPDATE queue_items
                SET status = 'pending', updated_at = ?, claimed_at = NULL
                WHERE status = 'processing'
                  AND COALESCE(claimed_at, updated_at, created_at) <= ?
                """,
                (now, cutoff),
            )
            return int(cur.rowcount)

    def mark_succeeded(self, item_id: int, result: Any | None = None) -> None:
        now = time.time()
        encoded = None if result is None else (result if isinstance(result, str) else json.dumps(result, ensure_ascii=False))
        with contextlib.closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE queue_items
                SET status = 'succeeded', result = ?, last_error = NULL, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (encoded, now, now, item_id),
            )

    def mark_failed_or_retry(self, item_id: int, error: str, max_retries: int) -> QueueStatus:
        """Retry by returning item to pending until max retries are exhausted."""
        now = time.time()
        with contextlib.closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT attempts FROM queue_items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return "failed"
            max_attempts = max_retries + 1
            status: QueueStatus = "pending" if int(row["attempts"]) < max_attempts else "failed"
            completed_at = now if status == "failed" else None
            conn.execute(
                """
                UPDATE queue_items
                SET status = ?, last_error = ?, updated_at = ?, claimed_at = NULL, completed_at = ?
                WHERE id = ?
                """,
                (status, error, now, completed_at, item_id),
            )
            conn.execute("COMMIT")
            return status

    def get(self, item_id: int) -> QueueItem | None:
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM queue_items WHERE id = ?", (item_id,)).fetchone()
        return self._row_to_item(row) if row else None

    def wait_for_terminal(self, item_id: int, timeout_seconds: float, poll_interval_seconds: float) -> QueueItem | None:
        deadline = time.monotonic() + timeout_seconds
        interval = poll_interval_seconds if poll_interval_seconds > 0 else 0.001
        while True:
            item = self.get(item_id)
            if item is None or item.status in {"succeeded", "failed"}:
                return item
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self.get(item_id)
            time.sleep(min(interval, remaining))

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> QueueItem:
        return QueueItem(
            id=int(row["id"]),
            kind=row["kind"],
            input=row["input"],
            status=row["status"],
            attempts=int(row["attempts"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            completed_at=(float(row["completed_at"]) if row["completed_at"] is not None else None),
            last_error=row["last_error"],
            result=row["result"],
        )
