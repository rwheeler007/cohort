"""Agent profile protocols and storage backends for cohort."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
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


# =====================================================================
# Default JSON file storage
# =====================================================================

class JsonFileStorage:
    """Flat-file JSON storage backend.

    Stores messages in ``{data_dir}/messages.json`` and channels in
    ``{data_dir}/channels.json``.  Thread-safe for single-process use
    (no file locking -- adequate for CLI and small-team scenarios).
    """

    def __init__(self, data_dir: Path | str = "data") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._messages_path = self._data_dir / "messages.json"
        self._channels_path = self._data_dir / "channels.json"

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
