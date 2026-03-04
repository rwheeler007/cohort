"""JSONL file-based storage backend for cohort.

Provides :class:`JsonlFileStorage` -- an append-only storage backend
that uses a single ``.jsonl`` file for messages and a small JSON file
for channel metadata.  Zero external dependencies.

Usage::

    from cohort.file_transport import JsonlFileStorage
    from cohort.chat import ChatManager

    storage = JsonlFileStorage("conversation.jsonl")
    chat = ChatManager(storage)
    chat.create_channel("review", "API design review")
    chat.post_message("review", "architect", "Hello!")
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class JsonlFileStorage:
    """Append-only JSONL storage backend.

    Stores messages in a single ``.jsonl`` file (one JSON object per line)
    and channel metadata in a companion ``{stem}_channels.json`` file.

    Satisfies the :class:`~cohort.registry.StorageBackend` protocol.
    """

    def __init__(self, jsonl_path: Path | str) -> None:
        self._jsonl_path = Path(jsonl_path)
        self._channels_path = (
            self._jsonl_path.parent / f"{self._jsonl_path.stem}_channels.json"
        )

    # -- helpers --------------------------------------------------------

    def _read_channels(self) -> dict[str, dict]:
        if not self._channels_path.exists():
            return {}
        try:
            return json.loads(self._channels_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read %s: %s", self._channels_path, exc)
            return {}

    def _write_channels(self, data: dict[str, dict]) -> None:
        self._channels_path.parent.mkdir(parents=True, exist_ok=True)
        self._channels_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # -- messages -------------------------------------------------------

    def save_message(self, channel: str, message: dict) -> str:
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
        self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return msg_id

    def get_messages(
        self,
        channel: str,
        limit: int = 50,
        before: str | None = None,
    ) -> list[dict]:
        if not self._jsonl_path.exists():
            return []
        filtered: list[dict] = []
        with open(self._jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed JSONL line")
                    continue
                if record.get("channel_id") == channel:
                    filtered.append(record)

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
        if not self._jsonl_path.exists():
            return False
        lines: list[str] = []
        found = False
        with open(self._jsonl_path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    lines.append(line)
                    continue
                if record.get("id") == message_id:
                    found = True
                    continue  # skip this line (delete it)
                lines.append(line)
        if found:
            with open(self._jsonl_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
        return found

    # -- channels -------------------------------------------------------

    def save_channel(self, channel_id: str, metadata: dict) -> None:
        channels = self._read_channels()
        channels[channel_id] = {"id": channel_id, **metadata}
        self._write_channels(channels)

    def get_channel(self, channel_id: str) -> dict | None:
        return self._read_channels().get(channel_id)

    def list_channels(self) -> list[dict]:
        return list(self._read_channels().values())


# =====================================================================
# Agent config loader
# =====================================================================

def load_agents_from_file(path: Path | str) -> dict[str, dict[str, Any]]:
    """Load agent configurations from a JSON file.

    Expected format::

        {
            "architect": {"triggers": ["api"], "capabilities": ["backend"]},
            "tester":    {"triggers": ["testing"], "capabilities": ["qa"]}
        }

    Missing ``triggers`` or ``capabilities`` keys default to empty lists.
    """
    text = Path(path).read_text(encoding="utf-8")
    data = json.loads(text)
    agents: dict[str, dict[str, Any]] = {}
    for agent_id, config in data.items():
        entry: dict[str, Any] = {
            "triggers": config.get("triggers", []),
            "capabilities": config.get("capabilities", []),
            "domain_expertise": config.get("domain_expertise", []),
        }
        # Pass through optional scoring metadata if present
        for scoring_key in ("complementary_agents", "data_sources", "phase_roles"):
            if scoring_key in config:
                entry[scoring_key] = config[scoring_key]
        agents[agent_id] = entry
    return agents
