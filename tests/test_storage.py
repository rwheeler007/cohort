"""Test StorageBackend implementations (JsonFileStorage + SqliteStorage)."""

from pathlib import Path

import pytest

from cohort.registry import JsonFileStorage
from cohort.sqlite_storage import SqliteStorage


@pytest.fixture(params=["json", "sqlite"], ids=["json", "sqlite"])
def storage(request, tmp_path: Path):
    if request.param == "json":
        return JsonFileStorage(tmp_path)
    return SqliteStorage(tmp_path)


class TestSaveAndGetMessages:
    def test_save_message_returns_id(self, storage):
        msg_id = storage.save_message("general", {
            "sender": "agent_a",
            "content": "hello world",
        })
        assert isinstance(msg_id, str)
        assert len(msg_id) > 0

    def test_get_messages_returns_saved(self, storage):
        storage.save_message("general", {"sender": "a", "content": "msg1"})
        storage.save_message("general", {"sender": "b", "content": "msg2"})
        msgs = storage.get_messages("general")
        assert len(msgs) == 2
        assert msgs[0]["content"] == "msg1"
        assert msgs[1]["content"] == "msg2"

    def test_get_messages_filters_by_channel(self, storage):
        storage.save_message("general", {"sender": "a", "content": "in general"})
        storage.save_message("other", {"sender": "b", "content": "in other"})
        msgs = storage.get_messages("general")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "in general"

    def test_get_messages_respects_limit(self, storage):
        for i in range(10):
            storage.save_message("ch", {"sender": "a", "content": f"msg{i}"})
        msgs = storage.get_messages("ch", limit=3)
        assert len(msgs) == 3
        # Should return the LAST 3 messages
        assert msgs[0]["content"] == "msg7"

    def test_get_messages_before_cursor(self, storage):
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

    def test_get_messages_empty_channel(self, storage):
        msgs = storage.get_messages("nonexistent")
        assert msgs == []

    def test_message_has_required_fields(self, storage):
        storage.save_message("ch", {"sender": "a", "content": "test"})
        msg = storage.get_messages("ch")[0]
        assert "id" in msg
        assert "channel_id" in msg
        assert "sender" in msg
        assert "content" in msg
        assert "timestamp" in msg


class TestDeleteMessage:
    def test_delete_existing_message(self, storage):
        msg_id = storage.save_message("ch", {"sender": "a", "content": "bye"})
        assert storage.delete_message(msg_id) is True
        assert storage.get_messages("ch") == []

    def test_delete_nonexistent_message(self, storage):
        assert storage.delete_message("no-such-id") is False


class TestChannels:
    def test_save_and_get_channel(self, storage):
        storage.save_channel("general", {
            "name": "General",
            "description": "Main channel",
        })
        ch = storage.get_channel("general")
        assert ch is not None
        assert ch["name"] == "General"
        assert ch["id"] == "general"

    def test_get_nonexistent_channel(self, storage):
        assert storage.get_channel("nope") is None

    def test_list_channels(self, storage):
        storage.save_channel("a", {"name": "Alpha"})
        storage.save_channel("b", {"name": "Bravo"})
        channels = storage.list_channels()
        assert len(channels) == 2
        names = {c["name"] for c in channels}
        assert names == {"Alpha", "Bravo"}

    def test_list_channels_empty(self, storage):
        assert storage.list_channels() == []

    def test_save_channel_overwrites(self, storage):
        storage.save_channel("ch", {"name": "v1"})
        storage.save_channel("ch", {"name": "v2"})
        ch = storage.get_channel("ch")
        assert ch["name"] == "v2"


class TestSoftDelete:
    def test_delete_and_list_deleted(self, storage):
        storage.save_channel("ch", {"name": "Doomed"})
        storage.save_message("ch", {"sender": "a", "content": "hello"})
        assert storage.delete_channel("ch") is True
        assert storage.get_channel("ch") is None
        assert storage.get_messages("ch") == []
        deleted = storage.list_deleted_channels()
        assert len(deleted) == 1
        assert deleted[0]["id"] == "ch"
        assert deleted[0]["message_count"] == 1

    def test_restore_channel(self, storage):
        storage.save_channel("ch", {"name": "Revived"})
        storage.save_message("ch", {"sender": "a", "content": "data"})
        storage.delete_channel("ch")
        assert storage.restore_channel("ch") is True
        assert storage.get_channel("ch") is not None
        msgs = storage.get_messages("ch")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "data"

    def test_permanently_delete(self, storage):
        storage.save_channel("ch", {"name": "Gone"})
        storage.delete_channel("ch")
        assert storage.permanently_delete_channel("ch") is True
        assert storage.list_deleted_channels() == []

    def test_delete_nonexistent_channel(self, storage):
        assert storage.delete_channel("nope") is False

    def test_restore_nonexistent(self, storage):
        assert storage.restore_channel("nope") is False

    def test_permanently_delete_nonexistent(self, storage):
        assert storage.permanently_delete_channel("nope") is False


class TestPersistence:
    def test_json_data_survives_new_instance(self, tmp_path: Path):
        s1 = JsonFileStorage(tmp_path)
        s1.save_message("ch", {"sender": "a", "content": "persistent"})
        s1.save_channel("ch", {"name": "Test"})

        s2 = JsonFileStorage(tmp_path)
        msgs = s2.get_messages("ch")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "persistent"
        assert s2.get_channel("ch")["name"] == "Test"

    def test_sqlite_data_survives_new_instance(self, tmp_path: Path):
        s1 = SqliteStorage(tmp_path)
        s1.save_message("ch", {"sender": "a", "content": "persistent"})
        s1.save_channel("ch", {"name": "Test"})

        s2 = SqliteStorage(tmp_path)
        msgs = s2.get_messages("ch")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "persistent"
        assert s2.get_channel("ch")["name"] == "Test"


class TestMigration:
    def test_migrate_json_to_sqlite(self, tmp_path: Path):
        """End-to-end: write JSON, migrate, verify in SQLite."""
        import json

        from cohort.migrate_json_to_sqlite import migrate

        # Seed JSON files
        channels = {
            "general": {"id": "general", "name": "General"},
            "random": {"id": "random", "name": "Random"},
        }
        messages = [
            {"id": "m1", "channel_id": "general", "sender": "a",
             "content": "hello", "timestamp": "2026-01-01T00:00:00"},
            {"id": "m2", "channel_id": "general", "sender": "b",
             "content": "world", "timestamp": "2026-01-01T00:01:00"},
            {"id": "m3", "channel_id": "random", "sender": "c",
             "content": "stuff", "timestamp": "2026-01-01T00:02:00"},
        ]
        (tmp_path / "channels.json").write_text(json.dumps(channels))
        (tmp_path / "messages.json").write_text(json.dumps(messages))

        assert migrate(tmp_path) is True

        # JSON files should be renamed
        assert not (tmp_path / "messages.json").exists()
        assert (tmp_path / "messages.json.bak").exists()
        assert not (tmp_path / "channels.json").exists()
        assert (tmp_path / "channels.json.bak").exists()

        # Verify via SQLite storage
        s = SqliteStorage(tmp_path)
        assert len(s.list_channels()) == 2
        assert len(s.get_messages("general")) == 2
        assert len(s.get_messages("random")) == 1
