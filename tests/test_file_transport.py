"""Tests for cohort.file_transport -- JSONL storage backend and CLI commands."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cohort.chat import ChatManager
from cohort.file_transport import JsonlFileStorage, load_agents_from_file

# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def jsonl_path(tmp_path: Path) -> Path:
    return tmp_path / "conversation.jsonl"


@pytest.fixture
def storage(jsonl_path: Path) -> JsonlFileStorage:
    return JsonlFileStorage(jsonl_path)


@pytest.fixture
def chat(storage: JsonlFileStorage) -> ChatManager:
    return ChatManager(storage)


def _agents_file(tmp_path: Path) -> Path:
    """Write a test agents.json and return the path."""
    p = tmp_path / "agents.json"
    p.write_text(json.dumps({
        "architect": {"triggers": ["api", "design"], "capabilities": ["backend architecture"]},
        "tester": {"triggers": ["testing", "qa"], "capabilities": ["test strategy"]},
    }), encoding="utf-8")
    return p


def _seed_conversation(jsonl_path: Path, channel: str = "review") -> None:
    """Write a few messages to a JSONL file for testing."""
    messages = [
        {"id": "m1", "channel_id": channel, "sender": "architect",
         "content": "Let's review the API design for pagination",
         "timestamp": "2026-01-01T00:00:00", "message_type": "chat",
         "metadata": {}, "reactions": []},
        {"id": "m2", "channel_id": channel, "sender": "tester",
         "content": "We should also test the edge cases for empty results",
         "timestamp": "2026-01-01T00:01:00", "message_type": "chat",
         "metadata": {}, "reactions": []},
    ]
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")
    # Write companion channels file
    channels_path = jsonl_path.parent / f"{jsonl_path.stem}_channels.json"
    channels_path.write_text(json.dumps({
        channel: {"id": channel, "name": channel, "description": "Test",
                  "created_at": "2026-01-01T00:00:00"},
    }), encoding="utf-8")


# =====================================================================
# JsonlFileStorage -- messages
# =====================================================================

class TestJsonlSaveAndGetMessages:
    def test_save_message_returns_id(self, storage: JsonlFileStorage):
        msg_id = storage.save_message("general", {
            "sender": "agent_a", "content": "hello world",
        })
        assert isinstance(msg_id, str)
        assert len(msg_id) > 0

    def test_get_messages_returns_saved(self, storage: JsonlFileStorage):
        storage.save_message("general", {"sender": "a", "content": "msg1"})
        storage.save_message("general", {"sender": "b", "content": "msg2"})
        msgs = storage.get_messages("general")
        assert len(msgs) == 2
        assert msgs[0]["content"] == "msg1"
        assert msgs[1]["content"] == "msg2"

    def test_get_messages_filters_by_channel(self, storage: JsonlFileStorage):
        storage.save_message("general", {"sender": "a", "content": "in general"})
        storage.save_message("other", {"sender": "b", "content": "in other"})
        msgs = storage.get_messages("general")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "in general"

    def test_get_messages_respects_limit(self, storage: JsonlFileStorage):
        for i in range(10):
            storage.save_message("ch", {"sender": "a", "content": f"msg{i}"})
        msgs = storage.get_messages("ch", limit=3)
        assert len(msgs) == 3
        # Should return the LAST 3 messages
        assert msgs[0]["content"] == "msg7"

    def test_get_messages_before_cursor(self, storage: JsonlFileStorage):
        ids = []
        for i in range(5):
            mid = storage.save_message("ch", {
                "sender": "a", "content": f"msg{i}",
            })
            ids.append(mid)
        msgs = storage.get_messages("ch", before=ids[3])
        assert len(msgs) == 3
        assert msgs[-1]["content"] == "msg2"

    def test_get_messages_empty_file(self, storage: JsonlFileStorage):
        msgs = storage.get_messages("nonexistent")
        assert msgs == []

    def test_message_has_required_fields(self, storage: JsonlFileStorage):
        storage.save_message("ch", {"sender": "a", "content": "test"})
        msg = storage.get_messages("ch")[0]
        assert "id" in msg
        assert "channel_id" in msg
        assert "sender" in msg
        assert "content" in msg
        assert "timestamp" in msg

    def test_jsonl_format_one_json_per_line(self, jsonl_path: Path, storage: JsonlFileStorage):
        storage.save_message("ch", {"sender": "a", "content": "msg1"})
        storage.save_message("ch", {"sender": "b", "content": "msg2"})
        storage.save_message("ch", {"sender": "c", "content": "msg3"})
        lines = jsonl_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3
        for line in lines:
            parsed = json.loads(line)
            assert isinstance(parsed, dict)

    def test_append_does_not_rewrite(self, jsonl_path: Path, storage: JsonlFileStorage):
        storage.save_message("ch", {"sender": "a", "content": "first"})
        size_after_one = jsonl_path.stat().st_size
        storage.save_message("ch", {"sender": "b", "content": "second"})
        size_after_two = jsonl_path.stat().st_size
        assert size_after_two > size_after_one

    def test_skips_malformed_lines(self, jsonl_path: Path, storage: JsonlFileStorage):
        # Write a valid message, then a bad line, then another valid message
        storage.save_message("ch", {"sender": "a", "content": "good1"})
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write("this is not json\n")
        storage.save_message("ch", {"sender": "b", "content": "good2"})
        msgs = storage.get_messages("ch")
        assert len(msgs) == 2
        assert msgs[0]["content"] == "good1"
        assert msgs[1]["content"] == "good2"


# =====================================================================
# JsonlFileStorage -- channels
# =====================================================================

class TestJsonlChannels:
    def test_save_and_get_channel(self, storage: JsonlFileStorage):
        storage.save_channel("general", {
            "name": "General", "description": "Main channel",
        })
        ch = storage.get_channel("general")
        assert ch is not None
        assert ch["name"] == "General"
        assert ch["id"] == "general"

    def test_get_nonexistent_channel(self, storage: JsonlFileStorage):
        assert storage.get_channel("nope") is None

    def test_list_channels(self, storage: JsonlFileStorage):
        storage.save_channel("a", {"name": "Alpha"})
        storage.save_channel("b", {"name": "Bravo"})
        channels = storage.list_channels()
        assert len(channels) == 2
        names = {c["name"] for c in channels}
        assert names == {"Alpha", "Bravo"}

    def test_list_channels_empty(self, storage: JsonlFileStorage):
        assert storage.list_channels() == []

    def test_save_channel_overwrites(self, storage: JsonlFileStorage):
        storage.save_channel("ch", {"name": "v1"})
        storage.save_channel("ch", {"name": "v2"})
        ch = storage.get_channel("ch")
        assert ch["name"] == "v2"


# =====================================================================
# Persistence
# =====================================================================

class TestJsonlPersistence:
    def test_data_survives_new_instance(self, jsonl_path: Path):
        s1 = JsonlFileStorage(jsonl_path)
        s1.save_message("ch", {"sender": "a", "content": "persistent"})
        s1.save_channel("ch", {"name": "Test"})

        s2 = JsonlFileStorage(jsonl_path)
        msgs = s2.get_messages("ch")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "persistent"
        assert s2.get_channel("ch")["name"] == "Test"


# =====================================================================
# ChatManager integration
# =====================================================================

class TestJsonlWithChatManager:
    def test_create_channel_and_post(self, chat: ChatManager):
        chat.create_channel("dev", "Development")
        msg = chat.post_message("dev", "agent_a", "hello world")
        assert msg.sender == "agent_a"
        msgs = chat.get_channel_messages("dev")
        contents = [m.content for m in msgs]
        assert "hello world" in contents

    def test_post_message_extracts_mentions(self, chat: ChatManager):
        chat.create_channel("dev", "Dev")
        msg = chat.post_message("dev", "agent_a", "Hey @agent_b check this")
        assert msg.metadata.get("mentions") == ["agent_b"]

    def test_search_messages(self, chat: ChatManager):
        chat.create_channel("dev", "Dev")
        chat.post_message("dev", "agent_a", "the API is broken")
        chat.post_message("dev", "agent_b", "the tests pass")
        results = chat.search_messages("API")
        assert any("API" in m.content for m in results)


# =====================================================================
# load_agents_from_file
# =====================================================================

class TestLoadAgentsFromFile:
    def test_load_valid_file(self, tmp_path: Path):
        p = _agents_file(tmp_path)
        agents = load_agents_from_file(p)
        assert "architect" in agents
        assert "tester" in agents
        assert agents["architect"]["triggers"] == ["api", "design"]
        assert agents["tester"]["capabilities"] == ["test strategy"]

    def test_fills_missing_keys(self, tmp_path: Path):
        p = tmp_path / "agents.json"
        p.write_text(json.dumps({"minimal": {}}), encoding="utf-8")
        agents = load_agents_from_file(p)
        assert agents["minimal"]["triggers"] == []
        assert agents["minimal"]["capabilities"] == []
        assert agents["minimal"]["domain_expertise"] == []

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises((FileNotFoundError, OSError)):
            load_agents_from_file(tmp_path / "nonexistent.json")

    def test_loads_scoring_metadata(self, tmp_path: Path):
        p = tmp_path / "agents.json"
        p.write_text(json.dumps({
            "custom_agent": {
                "triggers": ["custom"],
                "capabilities": ["stuff"],
                "complementary_agents": ["partner_agent"],
                "data_sources": ["logs", "metrics"],
                "phase_roles": {"EXECUTE": "high"},
            }
        }), encoding="utf-8")
        agents = load_agents_from_file(p)
        assert agents["custom_agent"]["complementary_agents"] == ["partner_agent"]
        assert agents["custom_agent"]["data_sources"] == ["logs", "metrics"]
        assert agents["custom_agent"]["phase_roles"] == {"EXECUTE": "high"}

    def test_scoring_metadata_optional(self, tmp_path: Path):
        p = tmp_path / "agents.json"
        p.write_text(json.dumps({
            "minimal": {"triggers": ["test"]}
        }), encoding="utf-8")
        agents = load_agents_from_file(p)
        assert "complementary_agents" not in agents["minimal"]
        assert "data_sources" not in agents["minimal"]
        assert "phase_roles" not in agents["minimal"]


# =====================================================================
# CLI: gate
# =====================================================================

class TestCliGate:
    def test_gate_speak(self, tmp_path: Path):
        """Agent with matching expertise should be allowed to speak."""
        jsonl_path = tmp_path / "conv.jsonl"
        agents_path = _agents_file(tmp_path)
        _seed_conversation(jsonl_path)

        result = subprocess.run(
            [sys.executable, "-m", "cohort", "gate",
             "--agent", "architect", "--channel", "review",
             "--file", str(jsonl_path), "--agents", str(agents_path),
             "--format", "json"],
            capture_output=True, text=True,
        )
        data = json.loads(result.stdout)
        assert data["speak"] is True
        assert result.returncode == 0

    def test_gate_json_format(self, tmp_path: Path):
        jsonl_path = tmp_path / "conv.jsonl"
        agents_path = _agents_file(tmp_path)
        _seed_conversation(jsonl_path)

        result = subprocess.run(
            [sys.executable, "-m", "cohort", "gate",
             "--agent", "architect", "--channel", "review",
             "--file", str(jsonl_path), "--agents", str(agents_path),
             "--format", "json"],
            capture_output=True, text=True,
        )
        data = json.loads(result.stdout)
        assert "agent" in data
        assert "score" in data
        assert "threshold" in data
        assert "speak" in data
        assert "reason" in data

    def test_gate_text_format(self, tmp_path: Path):
        jsonl_path = tmp_path / "conv.jsonl"
        agents_path = _agents_file(tmp_path)
        _seed_conversation(jsonl_path)

        result = subprocess.run(
            [sys.executable, "-m", "cohort", "gate",
             "--agent", "architect", "--channel", "review",
             "--file", str(jsonl_path), "--agents", str(agents_path),
             "--format", "text"],
            capture_output=True, text=True,
        )
        assert "Agent:" in result.stdout
        assert "Score:" in result.stdout
        assert "Decision:" in result.stdout

    def test_gate_unknown_agent(self, tmp_path: Path):
        jsonl_path = tmp_path / "conv.jsonl"
        agents_path = _agents_file(tmp_path)
        _seed_conversation(jsonl_path)

        result = subprocess.run(
            [sys.executable, "-m", "cohort", "gate",
             "--agent", "unknown", "--channel", "review",
             "--file", str(jsonl_path), "--agents", str(agents_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 2

    def test_gate_empty_channel(self, tmp_path: Path):
        jsonl_path = tmp_path / "conv.jsonl"
        agents_path = _agents_file(tmp_path)
        # Don't seed any messages

        result = subprocess.run(
            [sys.executable, "-m", "cohort", "gate",
             "--agent", "architect", "--channel", "review",
             "--file", str(jsonl_path), "--agents", str(agents_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 1


# =====================================================================
# CLI: next-speaker
# =====================================================================

class TestCliNextSpeaker:
    def test_next_speaker_ranked(self, tmp_path: Path):
        jsonl_path = tmp_path / "conv.jsonl"
        agents_path = _agents_file(tmp_path)
        _seed_conversation(jsonl_path)

        result = subprocess.run(
            [sys.executable, "-m", "cohort", "next-speaker",
             "--channel", "review",
             "--file", str(jsonl_path), "--agents", str(agents_path),
             "--top", "2"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "architect" in result.stdout or "tester" in result.stdout

    def test_next_speaker_json_format(self, tmp_path: Path):
        jsonl_path = tmp_path / "conv.jsonl"
        agents_path = _agents_file(tmp_path)
        _seed_conversation(jsonl_path)

        result = subprocess.run(
            [sys.executable, "-m", "cohort", "next-speaker",
             "--channel", "review",
             "--file", str(jsonl_path), "--agents", str(agents_path),
             "--format", "json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) > 0
        assert "agent_id" in data[0]
        assert "score" in data[0]

    def test_next_speaker_empty_channel(self, tmp_path: Path):
        jsonl_path = tmp_path / "conv.jsonl"
        agents_path = _agents_file(tmp_path)

        result = subprocess.run(
            [sys.executable, "-m", "cohort", "next-speaker",
             "--channel", "review",
             "--file", str(jsonl_path), "--agents", str(agents_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 1


# =====================================================================
# CLI: say
# =====================================================================

class TestCliSay:
    def test_say_appends_message(self, tmp_path: Path):
        jsonl_path = tmp_path / "conv.jsonl"

        result = subprocess.run(
            [sys.executable, "-m", "cohort", "say",
             "--sender", "architect", "--channel", "review",
             "--file", str(jsonl_path),
             "--message", "Hello from the CLI"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "[OK]" in result.stdout

        # Verify the file has content
        lines = jsonl_path.read_text(encoding="utf-8").strip().split("\n")
        # Should have system channel-creation message + the user message
        assert len(lines) >= 2
        contents = [json.loads(line)["content"] for line in lines]
        assert "Hello from the CLI" in contents

    def test_say_auto_creates_channel(self, tmp_path: Path):
        jsonl_path = tmp_path / "conv.jsonl"
        channels_path = tmp_path / "conv_channels.json"

        subprocess.run(
            [sys.executable, "-m", "cohort", "say",
             "--sender", "architect", "--channel", "new-channel",
             "--file", str(jsonl_path),
             "--message", "First message"],
            capture_output=True, text=True,
        )
        assert channels_path.exists()
        channels = json.loads(channels_path.read_text(encoding="utf-8"))
        assert "new-channel" in channels

    def test_say_exit_code_zero(self, tmp_path: Path):
        jsonl_path = tmp_path / "conv.jsonl"

        result = subprocess.run(
            [sys.executable, "-m", "cohort", "say",
             "--sender", "tester", "--channel", "ch",
             "--file", str(jsonl_path),
             "--message", "test"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
