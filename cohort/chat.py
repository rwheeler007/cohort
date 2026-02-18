"""Channel and message primitives for cohort.

Extracted from the agent chat system.  All persistence goes through
:class:`~cohort.registry.StorageBackend` -- no hardcoded file paths.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from enum import Enum
from typing import Any


# =====================================================================
# Enums
# =====================================================================

class MessageType(Enum):
    """Types of messages in the system."""

    CHAT = "chat"
    TASK = "task"
    RESULT = "result"
    STATUS = "status"
    ERROR = "error"
    SYSTEM = "system"


# =====================================================================
# Dataclasses
# =====================================================================

@dataclass
class Message:
    """A single message in the chat system."""

    id: str
    channel_id: str
    sender: str
    content: str
    timestamp: str
    message_type: str = "chat"
    thread_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    reactions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        d = dict(data)
        # Normalize legacy key aliases
        if "channel" in d and "channel_id" not in d:
            d["channel_id"] = d.pop("channel")
        elif "channel" in d:
            d.pop("channel")
        if "type" in d and "message_type" not in d:
            d["message_type"] = d.pop("type")
        elif "type" in d:
            d.pop("type")
        # Drop unknown keys
        valid = {f.name for f in fields(cls)}
        d = {k: v for k, v in d.items() if k in valid}
        return cls(**d)


@dataclass
class Channel:
    """A channel for organising conversations.

    When ``mode="meeting"``, *meeting_context* tracks stakeholder
    participation to prevent conversational loops while preserving
    high-value contributions.
    """

    id: str
    name: str
    description: str
    created_at: str
    members: list[str] = field(default_factory=list)
    is_private: bool = False
    topic: str = ""
    pinned_messages: list[str] = field(default_factory=list)
    is_archived: bool = False
    archived_at: str | None = None
    archived_by: str | None = None
    is_locked: bool = False
    locked_by: str | None = None
    locked_at: str | None = None
    mode: str = "chat"  # "chat" | "meeting" | "execute"
    meeting_context: dict[str, Any] | None = field(default_factory=lambda: None)
    shared_plan: dict[str, Any] | None = field(default_factory=lambda: None)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Channel:
        data.setdefault("mode", "chat")
        data.setdefault("meeting_context", None)
        data.setdefault("shared_plan", None)
        data.setdefault("metadata", {})
        valid = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k in valid}
        return cls(**data)


# =====================================================================
# Mention parsing
# =====================================================================

_MENTION_RE = re.compile(r"@([\w.-]+)")


def parse_mentions(text: str) -> list[str]:
    """Extract ``@agent`` mentions from *text*.

    Returns a deduplicated list of agent names in order of appearance.
    """
    seen: set[str] = set()
    result: list[str] = []
    for match in _MENTION_RE.finditer(text):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


# =====================================================================
# Chat manager
# =====================================================================

class ChatManager:
    """High-level chat operations backed by a
    :class:`~cohort.registry.StorageBackend`.

    The manager keeps an in-memory cache of channels and delegates all
    persistence to *storage*.
    """

    def __init__(self, storage: Any) -> None:  # StorageBackend duck type
        self._storage = storage
        self._channels: dict[str, Channel] = {}
        self._load_channels()

    # -- bootstrap ------------------------------------------------------

    def _load_channels(self) -> None:
        for raw in self._storage.list_channels():
            ch = Channel.from_dict(raw)
            self._channels[ch.id] = ch

    # -- channels -------------------------------------------------------

    def create_channel(
        self,
        name: str,
        description: str,
        members: list[str] | None = None,
        is_private: bool = False,
        topic: str = "",
    ) -> Channel:
        channel = Channel(
            id=name,
            name=name,
            description=description,
            created_at=datetime.now().isoformat(),
            members=members or [],
            is_private=is_private,
            topic=topic,
        )
        self._channels[name] = channel
        self._storage.save_channel(name, channel.to_dict())

        self.post_message(
            channel_id=name,
            sender="system",
            content=f"Channel #{name} created: {description}",
            message_type=MessageType.SYSTEM.value,
        )
        return channel

    def get_channel(self, channel_id: str) -> Channel | None:
        if channel_id in self._channels:
            return self._channels[channel_id]
        raw = self._storage.get_channel(channel_id)
        if raw:
            ch = Channel.from_dict(raw)
            self._channels[channel_id] = ch
            return ch
        return None

    def list_channels(self, include_archived: bool = False) -> list[Channel]:
        channels = list(self._channels.values())
        if not include_archived:
            channels = [c for c in channels if not c.is_archived]
        return channels

    # -- messages -------------------------------------------------------

    def post_message(
        self,
        channel_id: str,
        sender: str,
        content: str,
        message_type: str = "chat",
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        msg_id = str(uuid.uuid4())
        msg = Message(
            id=msg_id,
            channel_id=channel_id,
            sender=sender,
            content=content,
            timestamp=datetime.now().isoformat(),
            message_type=message_type,
            thread_id=thread_id,
            metadata=metadata or {},
        )
        self._storage.save_message(channel_id, msg.to_dict())
        return msg

    def get_channel_messages(
        self,
        channel_id: str,
        limit: int = 50,
        before: str | None = None,
    ) -> list[Message]:
        raw_messages = self._storage.get_messages(
            channel_id, limit=limit, before=before
        )
        return [Message.from_dict(m) for m in raw_messages]

    def search_messages(self, query: str, channel_id: str | None = None) -> list[Message]:
        """Simple substring search across messages."""
        query_lower = query.lower()
        results: list[Message] = []

        if channel_id:
            channels_to_search = [channel_id]
        else:
            channels_to_search = [ch.id for ch in self._channels.values()]

        for ch_id in channels_to_search:
            for msg in self.get_channel_messages(ch_id, limit=500):
                if query_lower in msg.content.lower():
                    results.append(msg)
        return results
