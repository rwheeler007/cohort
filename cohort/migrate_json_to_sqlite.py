"""Migrate Cohort chat data from JSON files to SQLite.

Usage::

    python -m cohort.migrate_json_to_sqlite [data_dir]

Reads ``messages.json``, ``channels.json``, and ``deleted_channels.json``
from *data_dir* (default: ``data``), inserts them into ``cohort.db``,
verifies row counts, and renames the originals to ``.bak``.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from cohort.sqlite_storage import SqliteStorage

logger = logging.getLogger(__name__)


def migrate(data_dir: str | Path = "data") -> bool:
    """Run the full JSON -> SQLite migration.

    Returns True on success, False on verification failure.
    """
    data_path = Path(data_dir)
    messages_path = data_path / "messages.json"
    channels_path = data_path / "channels.json"
    trash_path = data_path / "deleted_channels.json"

    # Counts for verification
    expected_messages = 0
    expected_channels = 0
    expected_trash = 0

    storage = SqliteStorage(data_path)

    # -- Migrate channels --------------------------------------------------
    if channels_path.exists():
        try:
            channels: dict = json.loads(
                channels_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to read %s: %s", channels_path, exc)
            return False

        expected_channels = len(channels)
        for channel_id, metadata in channels.items():
            storage.save_channel(channel_id, metadata)
        logger.info(
            "Migrated %d channels from %s", expected_channels, channels_path,
        )

    # -- Migrate messages --------------------------------------------------
    if messages_path.exists():
        try:
            messages: list = json.loads(
                messages_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to read %s: %s", messages_path, exc)
            return False

        expected_messages = len(messages)

        # Batch insert via raw connection for speed
        conn = storage._connect()
        try:
            with conn:
                for msg in messages:
                    conn.execute(
                        """INSERT OR IGNORE INTO messages
                           (id, channel_id, sender, content, timestamp,
                            message_type, thread_id, metadata, reactions)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            msg["id"],
                            msg["channel_id"],
                            msg.get("sender", "unknown"),
                            msg.get("content", ""),
                            msg.get("timestamp", ""),
                            msg.get("message_type", "chat"),
                            msg.get("thread_id"),
                            json.dumps(msg.get("metadata", {})),
                            json.dumps(msg.get("reactions", [])),
                        ),
                    )
        finally:
            conn.close()
        logger.info(
            "Migrated %d messages from %s", expected_messages, messages_path,
        )

    # -- Migrate trash -----------------------------------------------------
    if trash_path.exists():
        try:
            trash: list = json.loads(
                trash_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to read %s: %s", trash_path, exc)
            return False

        expected_trash = len(trash)
        conn = storage._connect()
        try:
            with conn:
                for entry in trash:
                    ch = entry.get("channel", {})
                    channel_id = ch.get("id", "")
                    if not channel_id:
                        continue
                    conn.execute(
                        """INSERT OR IGNORE INTO trash
                           (channel_id, channel_data, messages, deleted_at)
                           VALUES (?, ?, ?, ?)""",
                        (
                            channel_id,
                            json.dumps(ch),
                            json.dumps(entry.get("messages", [])),
                            entry.get("deleted_at", ""),
                        ),
                    )
        finally:
            conn.close()
        logger.info("Migrated %d trash entries from %s", expected_trash, trash_path)

    # -- Verify counts -----------------------------------------------------
    conn = storage._connect()
    try:
        actual_messages = conn.execute(
            "SELECT COUNT(*) FROM messages"
        ).fetchone()[0]
        actual_channels = conn.execute(
            "SELECT COUNT(*) FROM channels"
        ).fetchone()[0]
        actual_trash = conn.execute(
            "SELECT COUNT(*) FROM trash"
        ).fetchone()[0]
    finally:
        conn.close()

    ok = True
    if actual_messages != expected_messages:
        logger.error(
            "Message count mismatch: expected %d, got %d",
            expected_messages, actual_messages,
        )
        ok = False
    if actual_channels != expected_channels:
        logger.error(
            "Channel count mismatch: expected %d, got %d",
            expected_channels, actual_channels,
        )
        ok = False
    if actual_trash != expected_trash:
        logger.error(
            "Trash count mismatch: expected %d, got %d",
            expected_trash, actual_trash,
        )
        ok = False

    if not ok:
        logger.error("Verification failed -- JSON files NOT renamed")
        return False

    # -- Rename originals to .bak ------------------------------------------
    for path in (messages_path, channels_path, trash_path):
        if path.exists():
            bak = path.with_suffix(".json.bak")
            path.rename(bak)
            logger.info("Renamed %s -> %s", path.name, bak.name)

    logger.info("[OK] Migration complete")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    data = sys.argv[1] if len(sys.argv) > 1 else "data"
    success = migrate(data)
    sys.exit(0 if success else 1)
