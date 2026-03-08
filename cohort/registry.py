"""Agent profile protocols and storage backends for cohort."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# =====================================================================
# Protocols
# =====================================================================

@runtime_checkable
class AgentProfile(Protocol):
    """Duck-typed interface for agent profiles.

    Any object with these attributes and methods satisfies the protocol.
    No subclassing required.
    """

    name: str
    role: str
    capabilities: list[str]

    def relevance_score(self, topic: str) -> float: ...
    def can_contribute(self, context: dict) -> bool: ...


class StorageBackend(Protocol):
    """Duck-typed interface for message and channel persistence."""

    def save_message(self, channel: str, message: dict) -> str: ...
    def get_messages(
        self, channel: str, limit: int = 50, before: str | None = None
    ) -> list[dict]: ...
    def save_channel(self, channel_id: str, metadata: dict) -> None: ...
    def get_channel(self, channel_id: str) -> dict | None: ...
    def list_channels(self) -> list[dict]: ...
    def delete_channel(self, channel_id: str) -> bool: ...
    def list_deleted_channels(self) -> list[dict]: ...
    def restore_channel(self, channel_id: str) -> bool: ...
    def permanently_delete_channel(self, channel_id: str) -> bool: ...


# =====================================================================
# Default JSON file storage
# =====================================================================

class JsonFileStorage:
    """Flat-file JSON storage backend.

    Stores messages in ``{data_dir}/messages.json`` and channels in
    ``{data_dir}/channels.json``.  Thread-safe for single-process use
    (no file locking -- adequate for CLI and small-team scenarios).
    """

    TRASH_RETENTION_DAYS = 30

    def __init__(self, data_dir: Path | str = "data") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._messages_path = self._data_dir / "messages.json"
        self._channels_path = self._data_dir / "channels.json"
        self._trash_path = self._data_dir / "deleted_channels.json"

    # -- helpers --------------------------------------------------------

    def _read_json(self, path: Path, default: Any = None) -> Any:
        if not path.exists():
            return default if default is not None else []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read %s: %s", path, exc)
            return default if default is not None else []

    def _write_json(self, path: Path, data: Any) -> None:
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # -- messages -------------------------------------------------------

    def save_message(self, channel: str, message: dict) -> str:
        messages: list[dict] = self._read_json(self._messages_path, [])
        msg_id = message.get("id") or str(uuid.uuid4())
        record = {
            "id": msg_id,
            "channel_id": channel,
            "sender": message.get("sender", "unknown"),
            "content": message.get("content", ""),
            "timestamp": message.get(
                "timestamp", datetime.now(timezone.utc).isoformat()
            ),
            "message_type": message.get("message_type", "chat"),
            "thread_id": message.get("thread_id"),
            "metadata": message.get("metadata", {}),
            "reactions": message.get("reactions", []),
        }
        messages.append(record)
        self._write_json(self._messages_path, messages)
        return msg_id

    def get_messages(
        self,
        channel: str,
        limit: int = 50,
        before: str | None = None,
    ) -> list[dict]:
        messages: list[dict] = self._read_json(self._messages_path, [])
        filtered = [m for m in messages if m.get("channel_id") == channel]

        if before:
            cursor_idx: int | None = None
            for idx, m in enumerate(filtered):
                if m.get("id") == before:
                    cursor_idx = idx
                    break
            if cursor_idx is not None:
                filtered = filtered[:cursor_idx]

        return filtered[-limit:]

    def delete_message(
        self,
        message_id: str,
        channel_id: str | None = None,
    ) -> bool:
        messages: list[dict] = self._read_json(self._messages_path, [])
        original_len = len(messages)
        messages = [m for m in messages if m.get("id") != message_id]
        if len(messages) < original_len:
            self._write_json(self._messages_path, messages)
            return True
        return False

    # -- channels -------------------------------------------------------

    def save_channel(self, channel_id: str, metadata: dict) -> None:
        channels: dict[str, dict] = self._read_json(self._channels_path, {})
        channels[channel_id] = {
            "id": channel_id,
            **metadata,
        }
        self._write_json(self._channels_path, channels)

    def get_channel(self, channel_id: str) -> dict | None:
        channels: dict[str, dict] = self._read_json(self._channels_path, {})
        return channels.get(channel_id)

    def list_channels(self) -> list[dict]:
        channels: dict[str, dict] = self._read_json(self._channels_path, {})
        return list(channels.values())

    def delete_channel(self, channel_id: str) -> bool:
        """Soft-delete a channel: move to trash with 30-day retention."""
        channels: dict[str, dict] = self._read_json(self._channels_path, {})
        if channel_id not in channels:
            return False

        # Collect channel + its messages
        channel_data = channels.pop(channel_id)
        messages: list[dict] = self._read_json(self._messages_path, [])
        channel_msgs = [m for m in messages if m.get("channel_id") == channel_id]
        remaining_msgs = [m for m in messages if m.get("channel_id") != channel_id]

        # Write to trash
        trash: list[dict] = self._read_json(self._trash_path, [])
        trash.append({
            "channel": channel_data,
            "messages": channel_msgs,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
        })
        self._write_json(self._trash_path, trash)

        # Remove from active storage
        self._write_json(self._channels_path, channels)
        self._write_json(self._messages_path, remaining_msgs)

        # Purge expired entries
        self._purge_expired_trash()
        return True

    def list_deleted_channels(self) -> list[dict]:
        """Return trash entries still within retention period."""
        self._purge_expired_trash()
        trash: list[dict] = self._read_json(self._trash_path, [])
        result = []
        for entry in trash:
            ch = entry.get("channel", {})
            result.append({
                "id": ch.get("id", ""),
                "name": ch.get("name", ch.get("id", "")),
                "deleted_at": entry.get("deleted_at", ""),
                "message_count": len(entry.get("messages", [])),
            })
        return result

    def restore_channel(self, channel_id: str) -> bool:
        """Restore a soft-deleted channel and its messages."""
        trash: list[dict] = self._read_json(self._trash_path, [])
        entry = None
        for i, e in enumerate(trash):
            if e.get("channel", {}).get("id") == channel_id:
                entry = trash.pop(i)
                break
        if entry is None:
            return False

        # Restore channel
        channels: dict[str, dict] = self._read_json(self._channels_path, {})
        channels[channel_id] = entry["channel"]
        self._write_json(self._channels_path, channels)

        # Restore messages
        messages: list[dict] = self._read_json(self._messages_path, [])
        messages.extend(entry.get("messages", []))
        self._write_json(self._messages_path, messages)

        # Update trash
        self._write_json(self._trash_path, trash)
        return True

    def permanently_delete_channel(self, channel_id: str) -> bool:
        """Remove a channel from trash permanently."""
        trash: list[dict] = self._read_json(self._trash_path, [])
        original_len = len(trash)
        trash = [e for e in trash if e.get("channel", {}).get("id") != channel_id]
        if len(trash) == original_len:
            return False
        self._write_json(self._trash_path, trash)
        return True

    def _purge_expired_trash(self) -> None:
        """Remove trash entries older than retention period."""
        trash: list[dict] = self._read_json(self._trash_path, [])
        if not trash:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.TRASH_RETENTION_DAYS)
        kept = []
        for entry in trash:
            try:
                deleted_at = datetime.fromisoformat(entry["deleted_at"])
                if deleted_at.tzinfo is None:
                    deleted_at = deleted_at.replace(tzinfo=timezone.utc)
                if deleted_at > cutoff:
                    kept.append(entry)
            except (KeyError, ValueError):
                kept.append(entry)  # keep unparseable entries
        if len(kept) != len(trash):
            self._write_json(self._trash_path, kept)
