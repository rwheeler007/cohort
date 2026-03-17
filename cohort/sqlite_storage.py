"""SQLite storage backend for cohort.

Drop-in replacement for :class:`~cohort.registry.JsonFileStorage`.
Implements the :class:`~cohort.registry.StorageBackend` protocol with
crash-safe WAL journaling and proper index coverage.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS channels (
    id          TEXT PRIMARY KEY,
    metadata    TEXT NOT NULL DEFAULT '{}',
    deleted_at  TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_channels_active
    ON channels(id) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS messages (
    id           TEXT PRIMARY KEY,
    channel_id   TEXT NOT NULL,
    sender       TEXT NOT NULL,
    content      TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    message_type TEXT NOT NULL DEFAULT 'chat',
    thread_id    TEXT,
    metadata     TEXT NOT NULL DEFAULT '{}',
    reactions    TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_messages_channel_ts
    ON messages(channel_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_messages_thread
    ON messages(thread_id) WHERE thread_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS trash (
    channel_id  TEXT PRIMARY KEY,
    channel_data TEXT NOT NULL,
    messages     TEXT NOT NULL DEFAULT '[]',
    deleted_at   TEXT NOT NULL
);
"""


class SqliteStorage:
    """SQLite-backed storage implementing the StorageBackend protocol.

    Uses WAL journal mode for concurrent read access and crash safety.
    All queries use parameterised placeholders -- no string interpolation.
    """

    TRASH_RETENTION_DAYS = 30

    def __init__(self, data_dir: Path | str = "data") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._data_dir / "cohort.db"
        self._init_db()

    # -- connection helpers ------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # -- messages ----------------------------------------------------------

    def save_message(self, channel: str, message: dict) -> str:
        msg_id = message.get("id") or str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO messages
                   (id, channel_id, sender, content, timestamp,
                    message_type, thread_id, metadata, reactions)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    msg_id,
                    channel,
                    message.get("sender", "unknown"),
                    message.get("content", ""),
                    message.get("timestamp", now),
                    message.get("message_type", "chat"),
                    message.get("thread_id"),
                    json.dumps(message.get("metadata", {})),
                    json.dumps(message.get("reactions", [])),
                ),
            )
        return msg_id

    def get_messages(
        self,
        channel: str,
        limit: int = 50,
        before: str | None = None,
    ) -> list[dict]:
        with self._connect() as conn:
            if before:
                # Cursor-based: find the timestamp of the cursor message,
                # then fetch messages before it (matching JSON backend
                # behaviour which uses insertion order / list position).
                row = conn.execute(
                    "SELECT timestamp FROM messages WHERE id = ?",
                    (before,),
                ).fetchone()
                if row is None:
                    # Unknown cursor -- fall back to latest
                    return self._get_latest(conn, channel, limit)

                cursor_ts = row["timestamp"]
                rows = conn.execute(
                    """SELECT * FROM messages
                       WHERE channel_id = ?
                         AND (timestamp < ? OR (timestamp = ? AND id < ?))
                       ORDER BY timestamp ASC""",
                    (channel, cursor_ts, cursor_ts, before),
                ).fetchall()
                # Return last `limit` in chronological order
                rows = rows[-limit:] if len(rows) > limit else rows
                return [self._row_to_dict(r) for r in rows]

            return self._get_latest(conn, channel, limit)

    def _get_latest(
        self, conn: sqlite3.Connection, channel: str, limit: int,
    ) -> list[dict]:
        """Return the most recent *limit* messages in chronological order."""
        rows = conn.execute(
            """SELECT * FROM messages
               WHERE channel_id = ?
               ORDER BY timestamp DESC, rowid DESC
               LIMIT ?""",
            (channel, limit),
        ).fetchall()
        rows.reverse()  # chronological
        return [self._row_to_dict(r) for r in rows]

    def delete_message(
        self,
        message_id: str,
        channel_id: str | None = None,
    ) -> bool:
        with self._connect() as conn:
            if channel_id:
                cur = conn.execute(
                    "DELETE FROM messages WHERE id = ? AND channel_id = ?",
                    (message_id, channel_id),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM messages WHERE id = ?",
                    (message_id,),
                )
            return cur.rowcount > 0

    # -- channels ----------------------------------------------------------

    def save_channel(self, channel_id: str, metadata: dict) -> None:
        meta_copy = dict(metadata)
        meta_copy.pop("id", None)  # id stored in its own column
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO channels (id, metadata, created_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET metadata = excluded.metadata""",
                (channel_id, json.dumps(meta_copy), now),
            )

    def get_channel(self, channel_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM channels WHERE id = ? AND deleted_at IS NULL",
                (channel_id,),
            ).fetchone()
            if row is None:
                return None
            return self._channel_row_to_dict(row)

    def list_channels(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM channels WHERE deleted_at IS NULL"
            ).fetchall()
            return [self._channel_row_to_dict(r) for r in rows]

    def delete_channel(self, channel_id: str) -> bool:
        """Soft-delete: move channel + messages to trash."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM channels WHERE id = ? AND deleted_at IS NULL",
                (channel_id,),
            ).fetchone()
            if row is None:
                return False

            channel_data = self._channel_row_to_dict(row)

            # Collect messages
            msg_rows = conn.execute(
                "SELECT * FROM messages WHERE channel_id = ? ORDER BY timestamp",
                (channel_id,),
            ).fetchall()
            messages = [self._row_to_dict(r) for r in msg_rows]

            # Insert into trash
            conn.execute(
                """INSERT OR REPLACE INTO trash
                   (channel_id, channel_data, messages, deleted_at)
                   VALUES (?, ?, ?, ?)""",
                (channel_id, json.dumps(channel_data),
                 json.dumps(messages), now),
            )

            # Remove from active tables
            conn.execute(
                "DELETE FROM messages WHERE channel_id = ?", (channel_id,),
            )
            conn.execute(
                "DELETE FROM channels WHERE id = ?", (channel_id,),
            )

            # Purge expired trash
            self._purge_expired_trash_conn(conn)
        return True

    def list_deleted_channels(self) -> list[dict]:
        self._purge_expired_trash()
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM trash").fetchall()
            result = []
            for row in rows:
                ch = json.loads(row["channel_data"])
                msgs = json.loads(row["messages"])
                result.append({
                    "id": ch.get("id", row["channel_id"]),
                    "name": ch.get("name", ch.get("id", row["channel_id"])),
                    "deleted_at": row["deleted_at"],
                    "message_count": len(msgs),
                })
            return result

    def restore_channel(self, channel_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM trash WHERE channel_id = ?", (channel_id,),
            ).fetchone()
            if row is None:
                return False

            channel_data = json.loads(row["channel_data"])
            messages = json.loads(row["messages"])

            # Restore channel
            now = datetime.now(timezone.utc).isoformat()
            meta = dict(channel_data)
            meta.pop("id", None)
            conn.execute(
                """INSERT INTO channels (id, metadata, created_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       metadata = excluded.metadata,
                       deleted_at = NULL""",
                (channel_id, json.dumps(meta), now),
            )

            # Restore messages
            for msg in messages:
                conn.execute(
                    """INSERT OR IGNORE INTO messages
                       (id, channel_id, sender, content, timestamp,
                        message_type, thread_id, metadata, reactions)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        msg["id"],
                        msg["channel_id"],
                        msg["sender"],
                        msg["content"],
                        msg["timestamp"],
                        msg.get("message_type", "chat"),
                        msg.get("thread_id"),
                        json.dumps(msg.get("metadata", {})),
                        json.dumps(msg.get("reactions", [])),
                    ),
                )

            # Remove from trash
            conn.execute(
                "DELETE FROM trash WHERE channel_id = ?", (channel_id,),
            )
        return True

    def permanently_delete_channel(self, channel_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM trash WHERE channel_id = ?", (channel_id,),
            )
            return cur.rowcount > 0

    # -- trash maintenance -------------------------------------------------

    def _purge_expired_trash(self) -> None:
        with self._connect() as conn:
            self._purge_expired_trash_conn(conn)

    def _purge_expired_trash_conn(self, conn: sqlite3.Connection) -> None:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=self.TRASH_RETENTION_DAYS)
        ).isoformat()
        conn.execute("DELETE FROM trash WHERE deleted_at < ?", (cutoff,))

    # -- row helpers -------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for key in ("metadata", "reactions"):
            if key in d and isinstance(d[key], str):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

    @staticmethod
    def _channel_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        meta["id"] = row["id"]
        return meta

    # -- token usage queries -----------------------------------------------

    def get_token_usage(
        self,
        period: str = "today",
        pipeline: str | None = None,
    ) -> dict[str, int]:
        """Query accumulated token usage from message metadata.

        Args:
            period: "today", "month", or "all"
            pipeline: Filter by pipeline type (e.g., "smartest", "local"). None = all.

        Returns:
            {"messages": N, "tokens_in": N, "tokens_out": N, "tokens_total": N}
        """
        conditions = [
            "sender != 'user'",
            "sender != 'system'",
            "json_extract(metadata, '$.tokens_in') IS NOT NULL",
        ]
        params: list[Any] = []

        if period == "today":
            conditions.append("date(timestamp) = date('now')")
        elif period == "month":
            conditions.append("strftime('%Y-%m', timestamp) = strftime('%Y-%m', 'now')")

        if pipeline:
            conditions.append("json_extract(metadata, '$.pipeline') = ?")
            params.append(pipeline)

        where = " AND ".join(conditions)
        query = f"""
            SELECT
                COUNT(*) as msg_count,
                COALESCE(SUM(CAST(json_extract(metadata, '$.tokens_in') AS INTEGER)), 0) as total_in,
                COALESCE(SUM(CAST(json_extract(metadata, '$.tokens_out') AS INTEGER)), 0) as total_out
            FROM messages
            WHERE {where}
        """

        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()

        total_in = row["total_in"] if row else 0
        total_out = row["total_out"] if row else 0
        return {
            "messages": row["msg_count"] if row else 0,
            "tokens_in": total_in,
            "tokens_out": total_out,
            "tokens_total": total_in + total_out,
        }

    def check_token_budget(
        self,
        daily_limit: int = 500_000,
        monthly_limit: int = 10_000_000,
        pipeline: str | None = None,
    ) -> tuple[bool, int, str]:
        """Check if token budget allows another API call.

        Args:
            daily_limit: Max tokens (in+out) per day. 0 = unlimited.
            monthly_limit: Max tokens (in+out) per month. 0 = unlimited.
            pipeline: Filter by pipeline type. None = all pipelines.

        Returns:
            (allowed, remaining_today, reason)
        """
        if daily_limit <= 0 and monthly_limit <= 0:
            return True, 999_999, "Budget tracking disabled"

        today = self.get_token_usage("today", pipeline)
        today_total = today["tokens_total"]

        if daily_limit > 0 and today_total >= daily_limit:
            return False, 0, f"Daily token limit reached ({today_total:,}/{daily_limit:,})"

        if monthly_limit > 0:
            month = self.get_token_usage("month", pipeline)
            if month["tokens_total"] >= monthly_limit:
                return False, 0, f"Monthly token limit reached ({month['tokens_total']:,}/{monthly_limit:,})"

        remaining = daily_limit - today_total if daily_limit > 0 else 999_999
        return True, remaining, "OK"
