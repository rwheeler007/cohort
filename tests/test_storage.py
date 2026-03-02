"""Test JsonFileStorage CRUD operations."""

from pathlib import Path

import pytest

from cohort.registry import JsonFileStorage


@pytest.fixture
def storage(tmp_path: Path) -> JsonFileStorage:
    return JsonFileStorage(tmp_path)


class TestSaveAndGetMessages:
    def test_save_message_returns_id(self, storage: JsonFileStorage):
        msg_id = storage.save_message("general", {
            "sender": "agent_a",
            "content": "hello world",
        })
        assert isinstance(msg_id, str)
        assert len(msg_id) > 0

    def test_get_messages_returns_saved(self, storage: JsonFileStorage):
        storage.save_message("general", {"sender": "a", "content": "msg1"})
        storage.save_message("general", {"sender": "b", "content": "msg2"})
        msgs = storage.get_messages("general")
        assert len(msgs) == 2
        assert msgs[0]["content"] == "msg1"
        assert msgs[1]["content"] == "msg2"

    def test_get_messages_filters_by_channel(self, storage: JsonFileStorage):
        storage.save_message("general", {"sender": "a", "content": "in general"})
        storage.save_message("other", {"sender": "b", "content": "in other"})
        msgs = storage.get_messages("general")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "in general"

    def test_get_messages_respects_limit(self, storage: JsonFileStorage):
        for i in range(10):
            storage.save_message("ch", {"sender": "a", "content": f"msg{i}"})
        msgs = storage.get_messages("ch", limit=3)
        assert len(msgs) == 3
        # Should return the LAST 3 messages
        assert msgs[0]["content"] == "msg7"

    def test_get_messages_before_cursor(self, storage: JsonFileStorage):
        ids = []
        for i in range(5):
            mid = storage.save_message("ch", {
                "sender": "a", "content": f"msg{i}",
            })
            ids.append(mid)
        # Get messages before the 4th message (index 3)
        msgs = storage.get_messages("ch", before=ids[3])
        assert len(msgs) == 3
        assert msgs[-1]["content"] == "msg2"

    def test_get_messages_empty_channel(self, storage: JsonFileStorage):
        msgs = storage.get_messages("nonexistent")
        assert msgs == []

    def test_message_has_required_fields(self, storage: JsonFileStorage):
        storage.save_message("ch", {"sender": "a", "content": "test"})
        msg = storage.get_messages("ch")[0]
        assert "id" in msg
        assert "channel_id" in msg
        assert "sender" in msg
        assert "content" in msg
        assert "timestamp" in msg


class TestChannels:
    def test_save_and_get_channel(self, storage: JsonFileStorage):
        storage.save_channel("general", {
            "name": "General",
            "description": "Main channel",
        })
        ch = storage.get_channel("general")
        assert ch is not None
        assert ch["name"] == "General"
        assert ch["id"] == "general"

    def test_get_nonexistent_channel(self, storage: JsonFileStorage):
        assert storage.get_channel("nope") is None

    def test_list_channels(self, storage: JsonFileStorage):
        storage.save_channel("a", {"name": "Alpha"})
        storage.save_channel("b", {"name": "Bravo"})
        channels = storage.list_channels()
        assert len(channels) == 2
        names = {c["name"] for c in channels}
        assert names == {"Alpha", "Bravo"}

    def test_list_channels_empty(self, storage: JsonFileStorage):
        assert storage.list_channels() == []

    def test_save_channel_overwrites(self, storage: JsonFileStorage):
        storage.save_channel("ch", {"name": "v1"})
        storage.save_channel("ch", {"name": "v2"})
        ch = storage.get_channel("ch")
        assert ch["name"] == "v2"


class TestPersistence:
    def test_data_survives_new_instance(self, tmp_path: Path):
        s1 = JsonFileStorage(tmp_path)
        s1.save_message("ch", {"sender": "a", "content": "persistent"})
        s1.save_channel("ch", {"name": "Test"})

        s2 = JsonFileStorage(tmp_path)
        msgs = s2.get_messages("ch")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "persistent"
        assert s2.get_channel("ch")["name"] == "Test"
