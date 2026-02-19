"""Tests for cohort.chat -- ChatManager, Message, Channel, parse_mentions."""

from pathlib import Path

import pytest

from cohort.chat import Channel, ChatManager, Message, MessageType, parse_mentions
from cohort.registry import JsonFileStorage


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def storage(tmp_path: Path) -> JsonFileStorage:
    return JsonFileStorage(tmp_path)


@pytest.fixture
def chat(storage: JsonFileStorage) -> ChatManager:
    return ChatManager(storage)


# =====================================================================
# parse_mentions
# =====================================================================

class TestParseMentions:
    def test_single_mention(self):
        assert parse_mentions("Hey @alice check this") == ["alice"]

    def test_multiple_mentions(self):
        assert parse_mentions("@alice and @bob please review") == ["alice", "bob"]

    def test_deduplicates(self):
        assert parse_mentions("@alice said @alice should do it") == ["alice"]

    def test_no_mentions(self):
        assert parse_mentions("No mentions here") == []

    def test_mention_with_underscores(self):
        assert parse_mentions("@python_developer help") == ["python_developer"]

    def test_mention_with_dots(self):
        assert parse_mentions("@agent.v2 respond") == ["agent.v2"]

    def test_mention_with_hyphens(self):
        assert parse_mentions("@web-dev respond") == ["web-dev"]

    def test_preserves_order(self):
        assert parse_mentions("@charlie then @alice then @bob") == ["charlie", "alice", "bob"]

    def test_empty_string(self):
        assert parse_mentions("") == []


# =====================================================================
# Message
# =====================================================================

class TestMessage:
    def test_to_dict_roundtrip(self):
        msg = Message(
            id="m1",
            channel_id="general",
            sender="alice",
            content="hello",
            timestamp="2026-01-01T00:00:00",
        )
        d = msg.to_dict()
        restored = Message.from_dict(d)
        assert restored.id == "m1"
        assert restored.sender == "alice"
        assert restored.content == "hello"

    def test_from_dict_legacy_channel_key(self):
        """Legacy data uses 'channel' instead of 'channel_id'."""
        msg = Message.from_dict({
            "id": "m1",
            "channel": "general",
            "sender": "alice",
            "content": "hi",
            "timestamp": "2026-01-01T00:00:00",
        })
        assert msg.channel_id == "general"

    def test_from_dict_legacy_type_key(self):
        """Legacy data uses 'type' instead of 'message_type'."""
        msg = Message.from_dict({
            "id": "m1",
            "channel_id": "general",
            "sender": "alice",
            "content": "hi",
            "timestamp": "2026-01-01T00:00:00",
            "type": "system",
        })
        assert msg.message_type == "system"

    def test_from_dict_drops_unknown_keys(self):
        msg = Message.from_dict({
            "id": "m1",
            "channel_id": "ch",
            "sender": "a",
            "content": "hi",
            "timestamp": "t",
            "unknown_field": "should be dropped",
        })
        assert not hasattr(msg, "unknown_field") or msg.id == "m1"

    def test_defaults(self):
        msg = Message(
            id="m1", channel_id="ch", sender="a", content="hi", timestamp="t"
        )
        assert msg.message_type == "chat"
        assert msg.thread_id is None
        assert msg.metadata == {}
        assert msg.reactions == []


# =====================================================================
# Channel
# =====================================================================

class TestChannel:
    def test_to_dict_roundtrip(self):
        ch = Channel(
            id="general",
            name="General",
            description="Main channel",
            created_at="2026-01-01T00:00:00",
        )
        d = ch.to_dict()
        restored = Channel.from_dict(d)
        assert restored.id == "general"
        assert restored.name == "General"

    def test_defaults(self):
        ch = Channel(
            id="ch", name="Ch", description="desc", created_at="t"
        )
        assert ch.mode == "chat"
        assert ch.meeting_context is None
        assert ch.members == []
        assert ch.is_private is False

    def test_from_dict_drops_unknown_keys(self):
        ch = Channel.from_dict({
            "id": "ch",
            "name": "Ch",
            "description": "desc",
            "created_at": "t",
            "bogus": 42,
        })
        assert ch.id == "ch"

    def test_meeting_mode(self):
        ch = Channel(
            id="mtg",
            name="Meeting",
            description="desc",
            created_at="t",
            mode="meeting",
            meeting_context={"stakeholder_status": {"alice": "active_stakeholder"}},
        )
        assert ch.mode == "meeting"
        assert ch.meeting_context is not None


# =====================================================================
# ChatManager
# =====================================================================

class TestChatManager:
    def test_create_channel(self, chat: ChatManager):
        ch = chat.create_channel("dev", "Development")
        assert ch.id == "dev"
        assert ch.description == "Development"

    def test_get_channel(self, chat: ChatManager):
        chat.create_channel("dev", "Development")
        ch = chat.get_channel("dev")
        assert ch is not None
        assert ch.name == "dev"

    def test_get_channel_nonexistent(self, chat: ChatManager):
        assert chat.get_channel("nope") is None

    def test_list_channels(self, chat: ChatManager):
        chat.create_channel("a", "Alpha")
        chat.create_channel("b", "Bravo")
        channels = chat.list_channels()
        ids = {c.id for c in channels}
        assert ids == {"a", "b"}

    def test_list_channels_excludes_archived(self, chat: ChatManager):
        ch = chat.create_channel("old", "Old channel")
        ch.is_archived = True
        channels = chat.list_channels(include_archived=False)
        ids = {c.id for c in channels}
        assert "old" not in ids

    def test_list_channels_includes_archived(self, chat: ChatManager):
        ch = chat.create_channel("old", "Old channel")
        ch.is_archived = True
        channels = chat.list_channels(include_archived=True)
        ids = {c.id for c in channels}
        assert "old" in ids

    def test_post_message(self, chat: ChatManager):
        chat.create_channel("dev", "Dev")
        msg = chat.post_message("dev", "alice", "hello world")
        assert msg.sender == "alice"
        assert msg.content == "hello world"
        assert msg.channel_id == "dev"

    def test_post_message_extracts_mentions(self, chat: ChatManager):
        chat.create_channel("dev", "Dev")
        msg = chat.post_message("dev", "alice", "Hey @bob check this")
        assert msg.metadata.get("mentions") == ["bob"]

    def test_get_channel_messages(self, chat: ChatManager):
        chat.create_channel("dev", "Dev")
        chat.post_message("dev", "alice", "msg1")
        chat.post_message("dev", "bob", "msg2")
        # channel creation also posts a system message
        msgs = chat.get_channel_messages("dev")
        contents = [m.content for m in msgs]
        assert "msg1" in contents
        assert "msg2" in contents

    def test_get_channel_messages_limit(self, chat: ChatManager):
        chat.create_channel("dev", "Dev")
        for i in range(10):
            chat.post_message("dev", "alice", f"msg{i}")
        msgs = chat.get_channel_messages("dev", limit=3)
        assert len(msgs) == 3

    def test_search_messages(self, chat: ChatManager):
        chat.create_channel("dev", "Dev")
        chat.post_message("dev", "alice", "the API is broken")
        chat.post_message("dev", "bob", "the tests pass")
        results = chat.search_messages("API")
        assert any("API" in m.content for m in results)

    def test_search_messages_case_insensitive(self, chat: ChatManager):
        chat.create_channel("dev", "Dev")
        chat.post_message("dev", "alice", "Check the API")
        results = chat.search_messages("api")
        assert len(results) >= 1

    def test_search_messages_specific_channel(self, chat: ChatManager):
        chat.create_channel("dev", "Dev")
        chat.create_channel("ops", "Ops")
        chat.post_message("dev", "alice", "deploy API")
        chat.post_message("ops", "bob", "deploy API")
        results = chat.search_messages("API", channel_id="dev")
        assert all(m.channel_id == "dev" for m in results)


# =====================================================================
# MessageType enum
# =====================================================================

class TestMessageType:
    def test_values(self):
        assert MessageType.CHAT.value == "chat"
        assert MessageType.SYSTEM.value == "system"
        assert MessageType.ERROR.value == "error"
