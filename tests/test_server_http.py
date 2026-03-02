"""Integration tests for Cohort Starlette server HTTP endpoints.

Tests all REST API routes: channels CRUD, messages, agents, settings,
permissions, tools, roundtable, and task management. Uses httpx.AsyncClient
with ASGITransport for full request/response testing without external deps.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio


# =====================================================================
# Fixture to fix conftest data_dir bug (messages.json should be [], not {})
# =====================================================================

@pytest_asyncio.fixture
async def server_client(data_dir: Path, agents_dir: Path):
    """httpx.AsyncClient wired to the Starlette server via ASGITransport.

    Fixes conftest.py data_dir bug where messages.json is initialized as {}
    instead of [] (required by JsonFileStorage).
    """
    import os
    from unittest.mock import patch
    import httpx

    # Fix messages.json format (should be array, not dict)
    (data_dir / "messages.json").write_text("[]", encoding="utf-8")
    # channels.json should remain as {} (dict)

    # Create server app
    env = {
        "COHORT_DATA_DIR": str(data_dir),
        "COHORT_AGENTS_DIR": str(agents_dir),
        "COHORT_AGENTS_ROOT": str(agents_dir.parent),
    }
    with patch.dict(os.environ, env, clear=False):
        from cohort.server import create_app
        app = create_app(data_dir=str(data_dir))

    # Create client
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client


# =====================================================================
# Health & Index
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.server
class TestHealthAndIndex:
    """Test basic server endpoints."""

    async def test_health_endpoint(self, server_client):
        """GET /health returns 200 with status ok."""
        resp = await server_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    async def test_index_endpoint(self, server_client):
        """GET / returns HTML dashboard."""
        resp = await server_client.get("/")
        assert resp.status_code == 200
        # Template may not exist in test environment, but endpoint exists
        assert resp.headers["content-type"].startswith("text/html")


# =====================================================================
# Channel CRUD
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.server
class TestChannelCRUD:
    """Test channel create, list, get, update operations."""

    async def test_list_channels_empty(self, server_client):
        """GET /api/channels returns empty array initially."""
        resp = await server_client.get("/api/channels")
        assert resp.status_code == 200
        channels = resp.json()
        assert isinstance(channels, list)
        assert len(channels) == 0

    async def test_create_channel_success(self, server_client):
        """POST /api/channels creates a new channel."""
        payload = {
            "name": "test-channel",
            "description": "A test channel",
            "members": ["agent_a", "agent_b"],
            "is_private": False,
            "topic": "Testing",
        }
        resp = await server_client.post("/api/channels", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "channel" in data
        channel = data["channel"]
        assert channel["name"] == "test-channel"
        assert channel["description"] == "A test channel"
        assert "agent_a" in channel["members"]

    async def test_create_channel_missing_name(self, server_client):
        """POST /api/channels fails without name field."""
        payload = {"description": "No name provided"}
        resp = await server_client.post("/api/channels", json=payload)
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert "name" in data["error"].lower()

    async def test_create_channel_invalid_json(self, server_client):
        """POST /api/channels rejects malformed JSON."""
        resp = await server_client.post(
            "/api/channels",
            content=b"not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert "json" in data["error"].lower()

    async def test_create_channel_duplicate_name(self, server_client):
        """POST /api/channels rejects duplicate channel names."""
        payload = {"name": "duplicate-test", "description": "First"}
        resp1 = await server_client.post("/api/channels", json=payload)
        assert resp1.status_code == 200

        # Attempt to create again with same name
        resp2 = await server_client.post("/api/channels", json=payload)
        assert resp2.status_code == 409
        data = resp2.json()
        assert "error" in data
        assert "exists" in data["error"].lower()

    async def test_list_channels_after_create(self, server_client):
        """GET /api/channels includes newly created channels."""
        # Create two channels
        await server_client.post("/api/channels", json={"name": "ch1", "description": "First"})
        await server_client.post("/api/channels", json={"name": "ch2", "description": "Second"})

        # List channels
        resp = await server_client.get("/api/channels")
        assert resp.status_code == 200
        channels = resp.json()
        assert len(channels) >= 2
        names = [ch["name"] for ch in channels]
        assert "ch1" in names
        assert "ch2" in names


# =====================================================================
# Messages
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.server
class TestMessages:
    """Test message send and retrieval operations."""

    async def test_send_message_success(self, server_client):
        """POST /api/send posts a message to a channel."""
        payload = {
            "channel": "test-messages",
            "sender": "test_agent",
            "message": "Hello, this is a test message.",
        }
        resp = await server_client.post("/api/send", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "message_id" in data
        assert isinstance(data["message_id"], str)

    async def test_send_message_missing_fields(self, server_client):
        """POST /api/send fails with missing required fields."""
        # Missing 'message' field
        payload = {"channel": "test", "sender": "agent"}
        resp = await server_client.post("/api/send", json=payload)
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert "missing" in data["error"].lower()

    async def test_send_message_invalid_json(self, server_client):
        """POST /api/send rejects malformed JSON."""
        resp = await server_client.post(
            "/api/send",
            content=b"{invalid json}",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data

    async def test_get_messages_missing_channel(self, server_client):
        """GET /api/messages requires channel parameter."""
        resp = await server_client.get("/api/messages")
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert "channel" in data["error"].lower()

    async def test_get_messages_success(self, server_client):
        """GET /api/messages returns messages for a channel."""
        # Send a message first
        await server_client.post(
            "/api/send",
            json={"channel": "msg-test", "sender": "agent1", "message": "Test message 1"},
        )
        await server_client.post(
            "/api/send",
            json={"channel": "msg-test", "sender": "agent2", "message": "Test message 2"},
        )

        # Retrieve messages
        resp = await server_client.get("/api/messages", params={"channel": "msg-test"})
        assert resp.status_code == 200
        data = resp.json()
        assert "messages" in data
        messages = data["messages"]
        # 3 messages: 1 system (auto-created channel) + 2 user
        assert len(messages) == 3
        assert messages[1]["content"] == "Test message 1"

    async def test_get_messages_with_limit(self, server_client):
        """GET /api/messages respects limit parameter."""
        # Send 5 messages
        for i in range(5):
            await server_client.post(
                "/api/send",
                json={"channel": "limit-test", "sender": f"agent{i}", "message": f"Message {i}"},
            )

        # Retrieve with limit=3
        resp = await server_client.get("/api/messages", params={"channel": "limit-test", "limit": "3"})
        assert resp.status_code == 200
        data = resp.json()
        messages = data["messages"]
        assert len(messages) == 3

    async def test_get_messages_invalid_limit(self, server_client):
        """GET /api/messages falls back to default limit for invalid limit param."""
        await server_client.post(
            "/api/send",
            json={"channel": "invalid-limit", "sender": "agent", "message": "Test"},
        )
        resp = await server_client.get("/api/messages", params={"channel": "invalid-limit", "limit": "not-a-number"})
        assert resp.status_code == 200
        # Should not crash, uses default limit


# =====================================================================
# Agents
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.server
class TestAgents:
    """Test agent listing and detail retrieval."""

    async def test_list_agents(self, server_client):
        """GET /api/agents returns agent list."""
        resp = await server_client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        # Should return team snapshot (dict)
        assert isinstance(data, dict)

    async def test_get_agent_detail_success(self, server_client):
        """GET /api/agents/{agent_id} returns agent config."""
        # Use one of the mock agents from conftest
        resp = await server_client.get("/api/agents/python_developer")
        assert resp.status_code == 200
        config = resp.json()
        assert "agent_id" in config
        assert config["agent_id"] == "python_developer"
        assert "role" in config

    async def test_get_agent_detail_not_found(self, server_client):
        """GET /api/agents/{agent_id} returns 404 for unknown agent."""
        resp = await server_client.get("/api/agents/nonexistent_agent_xyz")
        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data
        assert "not found" in data["error"].lower()

    async def test_get_agent_registry(self, server_client):
        """GET /api/agent-registry returns visual profiles."""
        resp = await server_client.get("/api/agent-registry")
        assert resp.status_code == 200
        registry = resp.json()
        assert isinstance(registry, dict)


# =====================================================================
# Settings
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.server
class TestSettings:
    """Test settings get and update operations."""

    async def test_get_settings(self, server_client):
        """GET /api/settings returns current settings."""
        resp = await server_client.get("/api/settings")
        assert resp.status_code == 200
        settings = resp.json()
        assert "api_key_masked" in settings
        assert "claude_cmd" in settings
        assert "agents_root" in settings
        assert "response_timeout" in settings

    async def test_post_settings_success(self, server_client):
        """POST /api/settings updates settings."""
        payload = {
            "response_timeout": 180,
            "execution_backend": "cli",
        }
        resp = await server_client.post("/api/settings", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

        # Verify settings were updated
        resp2 = await server_client.get("/api/settings")
        settings = resp2.json()
        assert settings["response_timeout"] == 180

    async def test_post_settings_invalid_json(self, server_client):
        """POST /api/settings rejects malformed JSON."""
        resp = await server_client.post(
            "/api/settings",
            content=b"malformed",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data


# =====================================================================
# Permissions
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.server
class TestPermissions:
    """Test permissions endpoints."""

    async def test_get_permissions(self, server_client):
        """GET /api/permissions returns services and permissions."""
        resp = await server_client.get("/api/permissions")
        assert resp.status_code == 200
        data = resp.json()
        assert "services" in data
        assert "permissions" in data
        assert isinstance(data["services"], list)
        assert isinstance(data["permissions"], dict)

    async def test_post_permissions_success(self, server_client):
        """POST /api/permissions updates service keys and permissions."""
        payload = {
            "services": [
                {"id": "test_svc", "type": "custom", "name": "Test Service", "key": "test123"}
            ],
            "permissions": {"agent_a": ["read", "write"]},
        }
        resp = await server_client.post("/api/permissions", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    async def test_post_permissions_invalid_json(self, server_client):
        """POST /api/permissions rejects malformed JSON."""
        resp = await server_client.post(
            "/api/permissions",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data


# =====================================================================
# Tools
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.server
class TestTools:
    """Test tools API endpoint."""

    async def test_list_tools(self, server_client):
        """GET /api/tools returns tools list."""
        resp = await server_client.get("/api/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert "tools" in data
        assert isinstance(data["tools"], list)
        # May be empty if boss_config.yaml not found, but should not error


# =====================================================================
# Task Management
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.server
class TestTaskManagement:
    """Test task queue and outputs endpoints."""

    async def test_get_task_queue(self, server_client):
        """GET /api/tasks returns task queue."""
        resp = await server_client.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert isinstance(data["tasks"], list)

    async def test_get_task_queue_with_status_filter(self, server_client):
        """GET /api/tasks?status=pending filters by status."""
        resp = await server_client.get("/api/tasks", params={"status": "pending"})
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data

    async def test_get_outputs(self, server_client):
        """GET /api/outputs returns outputs for review."""
        resp = await server_client.get("/api/outputs")
        assert resp.status_code == 200
        data = resp.json()
        assert "outputs" in data
        assert isinstance(data["outputs"], list)


# =====================================================================
# Roundtable Endpoints
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.server
class TestRoundtable:
    """Test roundtable orchestration endpoints."""

    async def test_start_roundtable_missing_channel(self, server_client):
        """POST /api/roundtable/start requires channel_id."""
        payload = {"topic": "Test topic"}
        resp = await server_client.post("/api/roundtable/start", json=payload)
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False
        assert "channel_id" in data["error"]

    async def test_start_roundtable_missing_topic(self, server_client):
        """POST /api/roundtable/start requires topic."""
        payload = {"channel_id": "test-rt"}
        resp = await server_client.post("/api/roundtable/start", json=payload)
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False
        assert "topic" in data["error"]

    async def test_start_roundtable_success(self, server_client):
        """POST /api/roundtable/start creates a new roundtable session."""
        payload = {
            "channel_id": "roundtable-test",
            "topic": "Integration testing",
            "max_turns": 10,
        }
        resp = await server_client.post("/api/roundtable/start", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "session" in data
        session = data["session"]
        assert session["channel_id"] == "roundtable-test"
        assert session["topic"] == "Integration testing"

    async def test_start_roundtable_duplicate_session(self, server_client):
        """POST /api/roundtable/start rejects duplicate session for same channel."""
        payload = {"channel_id": "duplicate-rt", "topic": "Test"}
        # First session succeeds
        resp1 = await server_client.post("/api/roundtable/start", json=payload)
        assert resp1.status_code == 200

        # Second session fails
        resp2 = await server_client.post("/api/roundtable/start", json=payload)
        assert resp2.status_code == 409
        data = resp2.json()
        assert data["success"] is False
        assert "active roundtable" in data["error"].lower()

    async def test_get_roundtable_status_not_found(self, server_client):
        """GET /api/roundtable/{session_id}/status returns 404 for unknown session."""
        resp = await server_client.get("/api/roundtable/nonexistent-session-id/status")
        assert resp.status_code == 404
        data = resp.json()
        assert data["success"] is False

    async def test_get_roundtable_status_success(self, server_client):
        """GET /api/roundtable/{session_id}/status returns session status."""
        # Create a session
        start_resp = await server_client.post(
            "/api/roundtable/start",
            json={"channel_id": "status-test", "topic": "Testing"},
        )
        session_id = start_resp.json()["session"]["session_id"]

        # Get status
        resp = await server_client.get(f"/api/roundtable/{session_id}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "status" in data

    async def test_get_channel_roundtable_no_session(self, server_client):
        """GET /api/roundtable/channel/{channel_id} returns no session if none active."""
        resp = await server_client.get("/api/roundtable/channel/no-session-channel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["has_session"] is False

    async def test_get_channel_roundtable_with_session(self, server_client):
        """GET /api/roundtable/channel/{channel_id} returns active session."""
        # Start a roundtable
        await server_client.post(
            "/api/roundtable/start",
            json={"channel_id": "channel-rt", "topic": "Test"},
        )

        # Get channel roundtable
        resp = await server_client.get("/api/roundtable/channel/channel-rt")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["has_session"] is True
        assert "session" in data

    async def test_end_roundtable_not_found(self, server_client):
        """POST /api/roundtable/{session_id}/end returns 404 for unknown session."""
        resp = await server_client.post("/api/roundtable/fake-session-id/end")
        assert resp.status_code == 404
        data = resp.json()
        assert data["success"] is False

    async def test_record_turn_missing_fields(self, server_client):
        """POST /api/roundtable/{session_id}/record-turn requires speaker and message_id."""
        resp = await server_client.post(
            "/api/roundtable/some-session/record-turn",
            json={"speaker": "agent_a"},  # Missing message_id
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False


# =====================================================================
# Channel Condense
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.server
class TestChannelCondense:
    """Test channel message condensing."""

    async def test_condense_channel_not_found(self, server_client):
        """POST /api/channels/{channel_id}/condense returns 404 for missing channel."""
        resp = await server_client.post(
            "/api/channels/nonexistent-channel/condense",
            json={"keep_last": 5},
        )
        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data
        assert "not found" in data["error"].lower()

    async def test_condense_channel_nothing_to_condense(self, server_client):
        """POST /api/channels/{channel_id}/condense handles channels with few messages."""
        # Create channel and send 2 messages
        await server_client.post("/api/send", json={"channel": "condense-test", "sender": "a", "message": "msg1"})
        await server_client.post("/api/send", json={"channel": "condense-test", "sender": "b", "message": "msg2"})

        # Try to condense keeping last 5 (more than exist)
        resp = await server_client.post(
            "/api/channels/condense-test/condense",
            json={"keep_last": 5},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["archived_count"] == 0

    async def test_condense_channel_success(self, server_client):
        """POST /api/channels/{channel_id}/condense archives old messages."""
        # Send 10 messages
        for i in range(10):
            await server_client.post(
                "/api/send",
                json={"channel": "condense-10", "sender": f"agent{i}", "message": f"Message {i}"},
            )

        # Condense to keep last 3
        resp = await server_client.post(
            "/api/channels/condense-10/condense",
            json={"keep_last": 3},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        # 11 total (1 system from auto-create + 10 user), keep 3 = 8 archived
        assert data["archived_count"] == 8

        # Verify only 3 messages remain
        msg_resp = await server_client.get("/api/messages", params={"channel": "condense-10"})
        messages = msg_resp.json()["messages"]
        assert len(messages) == 3
