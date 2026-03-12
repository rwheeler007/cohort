"""Tests for the lite MCP backend (file-backed, no server).

Validates that LiteBackend provides the same interface as CohortClient
and operates correctly with local file storage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from cohort.mcp.lite_backend import LiteBackend


# =====================================================================
# Fixtures
# =====================================================================

@pytest_asyncio.fixture
async def backend(tmp_path: Path) -> LiteBackend:
    """LiteBackend with isolated temporary storage."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    checklist_path = tmp_path / "checklist.json"

    # Seed a test agent
    agent_dir = agents_dir / "test_agent"
    agent_dir.mkdir()
    (agent_dir / "agent_config.json").write_text(json.dumps({
        "agent_id": "test_agent",
        "name": "Test Agent",
        "role": "Test role",
        "status": "active",
        "personality": "A test agent.",
        "domain_expertise": ["testing"],
        "capabilities": ["test"],
    }), encoding="utf-8")
    (agent_dir / "memory.json").write_text(json.dumps({
        "agent_id": "test_agent",
        "working_memory": [],
        "learned_facts": [],
        "collaborators": {},
    }), encoding="utf-8")

    return LiteBackend(
        data_dir=data_dir,
        agents_dir=agents_dir,
        checklist_path=checklist_path,
    )


# =====================================================================
# Channel tests
# =====================================================================

@pytest.mark.asyncio
async def test_create_and_list_channels(backend: LiteBackend):
    result = await backend.create_channel("test-ch", description="A test channel")
    assert result is not None
    assert result["success"] is True

    channels = await backend.get_channels()
    assert channels is not None
    assert any(c["id"] == "test-ch" for c in channels)


@pytest.mark.asyncio
async def test_post_and_get_messages(backend: LiteBackend):
    await backend.create_channel("chat", description="Chat channel")

    result = await backend.post_message("chat", "alice", "Hello world")
    assert result is not None
    assert result["success"] is True
    assert "message_id" in result

    messages = await backend.get_messages("chat", limit=10)
    assert messages is not None
    assert len(messages) >= 1
    # Find our message (skip system message from channel creation)
    user_msgs = [m for m in messages if m["sender"] == "alice"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["content"] == "Hello world"


@pytest.mark.asyncio
async def test_post_message_auto_creates_channel(backend: LiteBackend):
    result = await backend.post_message("new-ch", "bob", "First message")
    assert result is not None
    assert result["success"] is True

    channels = await backend.get_channels()
    assert any(c["id"] == "new-ch" for c in channels)


@pytest.mark.asyncio
async def test_condense_returns_error_in_lite_mode(backend: LiteBackend):
    result = await backend.condense_channel("test", keep_last=5)
    assert result is not None
    assert result["success"] is False
    assert "requires" in result["error"].lower()


# =====================================================================
# Agent tests
# =====================================================================

@pytest.mark.asyncio
async def test_list_agents(backend: LiteBackend):
    agents = await backend.list_agents()
    assert agents is not None
    assert len(agents) >= 1
    assert agents[0]["agent_id"] == "test_agent"


@pytest.mark.asyncio
async def test_get_agent(backend: LiteBackend):
    data = await backend.get_agent("test_agent")
    assert data is not None
    assert data.get("agent_id") == "test_agent"
    assert data.get("name") == "Test Agent"


@pytest.mark.asyncio
async def test_get_agent_not_found(backend: LiteBackend):
    data = await backend.get_agent("nonexistent")
    assert data is not None
    assert "error" in data


@pytest.mark.asyncio
async def test_get_agent_memory(backend: LiteBackend):
    data = await backend.get_agent_memory("test_agent")
    assert data is not None
    assert "working_memory" in data
    assert "learned_facts" in data


@pytest.mark.asyncio
async def test_add_fact(backend: LiteBackend):
    result = await backend.add_agent_fact("test_agent", {
        "fact": "Python uses indentation for blocks",
        "learned_from": "test",
        "confidence": "high",
    })
    assert result is not None
    assert result["success"] is True

    memory = await backend.get_agent_memory("test_agent")
    assert len(memory["learned_facts"]) == 1
    assert memory["learned_facts"][0]["fact"] == "Python uses indentation for blocks"


@pytest.mark.asyncio
async def test_clean_memory(backend: LiteBackend):
    # Add some working memory entries first
    memory_path = backend._agents_dir / "test_agent" / "memory.json"
    memory_data = json.loads(memory_path.read_text(encoding="utf-8"))
    memory_data["working_memory"] = [
        {"timestamp": f"2026-01-0{i}", "channel": "test", "input": f"msg {i}", "response": f"resp {i}"}
        for i in range(1, 6)
    ]
    memory_path.write_text(json.dumps(memory_data), encoding="utf-8")

    # Reload store
    backend._agent_store._loaded_all = False
    backend._agent_store._cache.clear()

    # Dry run
    result = await backend.clean_agent_memory("test_agent", keep_last=2, dry_run=True)
    assert result["success"] is True
    assert result["working_memory_removed"] == 3

    # Real clean
    result = await backend.clean_agent_memory("test_agent", keep_last=2, dry_run=False)
    assert result["success"] is True
    assert result["working_memory_kept"] == 2


@pytest.mark.asyncio
async def test_create_agent(backend: LiteBackend):
    result = await backend.create_agent({
        "name": "New Agent",
        "role": "New role",
        "capabilities": ["cap1"],
    })
    assert result is not None
    assert result["success"] is True
    assert result["agent_id"] == "new_agent"

    # Verify on disk
    assert (backend._agents_dir / "new_agent" / "agent_config.json").exists()
    assert (backend._agents_dir / "new_agent" / "memory.json").exists()


@pytest.mark.asyncio
async def test_create_agent_duplicate(backend: LiteBackend):
    result = await backend.create_agent({
        "name": "Test Agent",  # Already exists
        "role": "Duplicate",
    })
    assert result["success"] is False
    assert "already exists" in result["error"]


# =====================================================================
# Search tests
# =====================================================================

@pytest.mark.asyncio
async def test_search_messages(backend: LiteBackend):
    await backend.create_channel("search-ch", description="Search test")
    await backend.post_message("search-ch", "alice", "The quick brown fox")
    await backend.post_message("search-ch", "bob", "Jumped over the lazy dog")

    results = await backend.search_messages("brown fox")
    assert results is not None
    assert len(results) >= 1
    assert any("brown fox" in m.get("content", "") for m in results)


@pytest.mark.asyncio
async def test_get_mentions(backend: LiteBackend):
    await backend.create_channel("mention-ch", description="Mention test")
    await backend.post_message("mention-ch", "alice", "Hey @test_agent, check this")
    await backend.post_message("mention-ch", "bob", "No mentions here")

    results = await backend.get_mentions("test_agent")
    assert results is not None
    assert len(results) >= 1
    assert all("@test_agent" in m.get("content", "") for m in results)


# =====================================================================
# Session tests (lite mode stubs)
# =====================================================================

@pytest.mark.asyncio
async def test_start_session_lite_mode(backend: LiteBackend):
    await backend.create_channel("session-ch", description="Session test")
    result = await backend.start_session("session-ch", ["test_agent"], "Discuss X")
    assert result is not None
    assert result["success"] is True
    assert result["status"] == "lite_mode"


@pytest.mark.asyncio
async def test_get_session_status_lite_mode(backend: LiteBackend):
    result = await backend.get_session_status("fake-id")
    assert result is not None
    assert result["status"] == "lite_mode"


# =====================================================================
# Work queue / task stubs
# =====================================================================

@pytest.mark.asyncio
async def test_work_queue_stubs(backend: LiteBackend):
    assert await backend.get_work_queue() == []
    assert await backend.get_task_queue() == []
    assert await backend.get_outputs_for_review() == []

    result = await backend.enqueue_work_item("Test task")
    assert result["success"] is False

    result = await backend.create_task("test_agent", "Do something")
    assert result["success"] is False


# =====================================================================
# Checklist tests
# =====================================================================

@pytest.mark.asyncio
async def test_checklist_read_write(backend: LiteBackend):
    data = await backend.read_checklist()
    assert data == {"items": []}

    items = {"items": [{"id": "1", "content": "Test task", "checked": False}]}
    assert await backend.write_checklist(items) is True

    data = await backend.read_checklist()
    assert len(data["items"]) == 1
    assert data["items"][0]["content"] == "Test task"


@pytest.mark.asyncio
async def test_checklist_no_path():
    """Backend without checklist_path returns empty list."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        b = LiteBackend(data_dir=td, checklist_path=None)
        data = await b.read_checklist()
        assert data == {"items": []}
        assert await b.write_checklist({"items": []}) is False


# =====================================================================
# Briefing stubs
# =====================================================================

@pytest.mark.asyncio
async def test_briefing_stubs(backend: LiteBackend):
    result = await backend.generate_briefing()
    assert result["success"] is False

    result = await backend.get_latest_briefing()
    assert result is None
