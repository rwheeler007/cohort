"""Integration tests for Cohort Starlette server HTTP endpoints.

Tests all REST API routes: channels CRUD, messages, agents, settings,
tools, health, and error paths. Uses httpx.AsyncClient with ASGITransport
for full request/response testing without external dependencies.

Deliverables covered:
  D2 - Channel CRUD tests
  D3 - Message tests
  D4 - Agent endpoint tests
  D5 - Settings and tools
  D6 - Error paths
  D7 - All tests use conftest.py fixtures, zero external deps
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio

pytestmark = [pytest.mark.integration, pytest.mark.server, pytest.mark.asyncio]


# =====================================================================
# Override conftest server_client to fix messages.json format
# =====================================================================

@pytest_asyncio.fixture
async def server_client(data_dir: Path, agents_dir: Path):
    """httpx.AsyncClient wired to the Starlette server via ASGITransport.

    Overrides conftest.py to ensure messages.json is initialized as []
    (required by JsonFileStorage) rather than {}.
    """
    # Ensure correct JSON format for messages
    (data_dir / "messages.json").write_text("[]", encoding="utf-8")

    env = {
        "COHORT_DATA_DIR": str(data_dir),
        "COHORT_AGENTS_DIR": str(agents_dir),
        "COHORT_AGENTS_ROOT": str(agents_dir.parent),
    }
    with patch.dict(os.environ, env, clear=False):
        from cohort.server import create_app
        app = create_app(data_dir=str(data_dir))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client


# =====================================================================
# D5: Health endpoint
# =====================================================================


class TestHealth:
    """GET /api/health and GET /health liveness probe."""

    async def test_health_returns_200_with_status_ok(self, server_client):
        """GET /health returns 200 with {status: ok}."""
        resp = await server_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    async def test_index_returns_html(self, server_client):
        """GET / returns an HTML response (dashboard or 404 placeholder)."""
        resp = await server_client.get("/")
        assert resp.status_code in (200, 404)
        assert resp.headers["content-type"].startswith("text/html")


# =====================================================================
# D2: Channel CRUD tests
# =====================================================================


class TestChannelCRUD:
    """POST /api/channels, GET /api/channels, GET /api/messages."""

    async def test_create_channel_success(self, server_client):
        """POST /api/channels with name and description returns 200 with success and channel."""
        payload = {"name": "test-channel", "description": "A test channel"}
        resp = await server_client.post("/api/channels", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "channel" in data
        channel = data["channel"]
        assert channel["name"] == "test-channel"
        assert channel["description"] == "A test channel"
        assert channel["id"] == "test-channel"

    async def test_create_channel_with_members_and_topic(self, server_client):
        """POST /api/channels accepts optional members, is_private, topic fields."""
        payload = {
            "name": "full-channel",
            "description": "Full spec channel",
            "members": ["alice", "bob"],
            "is_private": True,
            "topic": "Integration testing",
        }
        resp = await server_client.post("/api/channels", json=payload)
        assert resp.status_code == 200
        channel = resp.json()["channel"]
        assert channel["members"] == ["alice", "bob"]
        assert channel["is_private"] is True
        assert channel["topic"] == "Integration testing"

    async def test_list_channels_initially_empty(self, server_client):
        """GET /api/channels returns empty list when no channels exist."""
        resp = await server_client.get("/api/channels")
        assert resp.status_code == 200
        channels = resp.json()
        assert isinstance(channels, list)
        assert len(channels) == 0

    async def test_list_channels_after_create(self, server_client):
        """GET /api/channels includes newly created channels."""
        await server_client.post(
            "/api/channels", json={"name": "ch-alpha", "description": "Alpha"},
        )
        await server_client.post(
            "/api/channels", json={"name": "ch-beta", "description": "Beta"},
        )

        resp = await server_client.get("/api/channels")
        assert resp.status_code == 200
        channels = resp.json()
        names = [ch["name"] for ch in channels]
        assert "ch-alpha" in names
        assert "ch-beta" in names

    async def test_get_messages_for_channel(self, server_client):
        """GET /api/messages?channel=<id> returns messages list."""
        # Create channel and send a message
        await server_client.post(
            "/api/channels", json={"name": "msg-chan", "description": "Messages"},
        )
        await server_client.post(
            "/api/send",
            json={"channel": "msg-chan", "sender": "tester", "message": "Hello"},
        )

        resp = await server_client.get("/api/messages", params={"channel": "msg-chan"})
        assert resp.status_code == 200
        data = resp.json()
        assert "messages" in data
        assert isinstance(data["messages"], list)
        # At least 1 system message (channel create) + 1 user message
        assert len(data["messages"]) >= 2

    async def test_get_messages_nonexistent_channel(self, server_client):
        """GET /api/messages?channel=nonexistent returns 200 with empty messages."""
        resp = await server_client.get(
            "/api/messages", params={"channel": "nonexistent-channel-xyz"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "messages" in data
        assert data["messages"] == []

    async def test_get_messages_missing_channel_param(self, server_client):
        """GET /api/messages without channel param returns 400 error."""
        resp = await server_client.get("/api/messages")
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert "channel" in data["error"].lower()


# =====================================================================
# D3: Message tests
# =====================================================================


class TestMessages:
    """POST /api/send and message retrieval with ordering and limits."""

    async def test_send_message_success(self, server_client):
        """POST /api/send with channel, sender, message returns 200 with success and message_id."""
        payload = {
            "channel": "send-test",
            "sender": "test_agent",
            "message": "Hello, this is a test message.",
        }
        resp = await server_client.post("/api/send", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "message_id" in data
        assert isinstance(data["message_id"], str)
        assert len(data["message_id"]) > 0

    async def test_send_message_auto_creates_channel(self, server_client):
        """POST /api/send auto-creates channel if it does not exist."""
        resp = await server_client.post(
            "/api/send",
            json={
                "channel": "auto-created-chan",
                "sender": "bot",
                "message": "I trigger channel creation",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Verify channel now exists
        channels_resp = await server_client.get("/api/channels")
        names = [ch["id"] for ch in channels_resp.json()]
        assert "auto-created-chan" in names

    async def test_get_messages_with_limit(self, server_client):
        """GET /api/messages?channel=<id>&limit=1 returns limited messages."""
        # Create channel and send multiple messages
        await server_client.post(
            "/api/channels", json={"name": "limit-chan", "description": "Limit test"},
        )
        for i in range(5):
            await server_client.post(
                "/api/send",
                json={
                    "channel": "limit-chan",
                    "sender": "bot",
                    "message": f"Message {i}",
                },
            )

        resp = await server_client.get(
            "/api/messages", params={"channel": "limit-chan", "limit": "2"},
        )
        assert resp.status_code == 200
        messages = resp.json()["messages"]
        assert len(messages) == 2

    async def test_send_multiple_messages_ordering(self, server_client):
        """Send multiple messages to a channel and verify they come back in order."""
        channel_name = "ordering-test"
        await server_client.post(
            "/api/channels",
            json={"name": channel_name, "description": "Ordering test"},
        )

        messages_sent = ["First", "Second", "Third", "Fourth"]
        for msg_text in messages_sent:
            await server_client.post(
                "/api/send",
                json={
                    "channel": channel_name,
                    "sender": "orderer",
                    "message": msg_text,
                },
            )

        resp = await server_client.get(
            "/api/messages", params={"channel": channel_name},
        )
        assert resp.status_code == 200
        messages = resp.json()["messages"]

        # Filter to only user messages (skip system channel-creation message)
        user_msgs = [m for m in messages if m["sender"] == "orderer"]
        assert len(user_msgs) == 4
        assert user_msgs[0]["content"] == "First"
        assert user_msgs[1]["content"] == "Second"
        assert user_msgs[2]["content"] == "Third"
        assert user_msgs[3]["content"] == "Fourth"

    async def test_send_message_with_mentions(self, server_client):
        """POST /api/send extracts @mentions into message metadata."""
        await server_client.post(
            "/api/channels",
            json={"name": "mention-chan", "description": "Mention test"},
        )
        resp = await server_client.post(
            "/api/send",
            json={
                "channel": "mention-chan",
                "sender": "user",
                "message": "Hey @python_developer please review this",
            },
        )
        assert resp.status_code == 200

        get_resp = await server_client.get(
            "/api/messages", params={"channel": "mention-chan"},
        )
        user_msgs = [
            m for m in get_resp.json()["messages"] if m["sender"] == "user"
        ]
        assert len(user_msgs) == 1
        assert "python_developer" in user_msgs[0]["metadata"].get("mentions", [])


# =====================================================================
# D4: Agent endpoint tests
# =====================================================================


class TestAgents:
    """GET /api/agents, GET /api/agents/<id>, GET /api/agents/<id>/prompt, GET /api/agent-registry."""

    async def test_list_agents_returns_200(self, server_client):
        """GET /api/agents returns 200 with agent data."""
        resp = await server_client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        # Returns team snapshot -- dict with agents and metadata
        assert isinstance(data, dict)

    async def test_get_agent_detail_success(self, server_client):
        """GET /api/agents/python_developer returns 200 with agent config."""
        resp = await server_client.get("/api/agents/python_developer")
        assert resp.status_code == 200
        config = resp.json()
        assert "agent_id" in config
        assert config["agent_id"] == "python_developer"
        assert "role" in config

    async def test_get_agent_detail_not_found(self, server_client):
        """GET /api/agents/nonexistent_agent returns 404."""
        resp = await server_client.get("/api/agents/nonexistent_agent_xyz")
        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data
        assert "not found" in data["error"].lower()

    async def test_get_agent_prompt_success(self, server_client):
        """GET /api/agents/python_developer/prompt returns prompt text."""
        resp = await server_client.get("/api/agents/python_developer/prompt")
        assert resp.status_code == 200
        data = resp.json()
        assert "agent_id" in data
        assert data["agent_id"] == "python_developer"
        assert "prompt" in data
        assert isinstance(data["prompt"], str)
        assert len(data["prompt"]) > 0

    async def test_get_agent_prompt_not_found(self, server_client):
        """GET /api/agents/nonexistent_agent/prompt returns 404."""
        resp = await server_client.get("/api/agents/nonexistent_agent_xyz/prompt")
        assert resp.status_code == 404

    async def test_get_agent_registry_returns_profiles_dict(self, server_client):
        """GET /api/agent-registry returns a dict of agent profiles."""
        resp = await server_client.get("/api/agent-registry")
        assert resp.status_code == 200
        registry = resp.json()
        assert isinstance(registry, dict)

    async def test_all_mock_agents_accessible(self, server_client):
        """All 5 mock agents from conftest are accessible via detail endpoint."""
        mock_ids = [
            "python_developer",
            "web_developer",
            "coding_orchestrator",
            "cohort_orchestrator",
            "ceo_agent",
        ]
        for agent_id in mock_ids:
            resp = await server_client.get(f"/api/agents/{agent_id}")
            assert resp.status_code == 200, f"Agent {agent_id} returned {resp.status_code}"
            assert resp.json()["agent_id"] == agent_id


# =====================================================================
# D5: Settings and tools
# =====================================================================


class TestSettings:
    """GET /api/settings, POST /api/settings."""

    async def test_get_settings_returns_expected_keys(self, server_client):
        """GET /api/settings returns 200 with standard settings keys."""
        resp = await server_client.get("/api/settings")
        assert resp.status_code == 200
        settings = resp.json()
        assert "api_key_masked" in settings
        assert "claude_cmd" in settings
        assert "agents_root" in settings
        assert "response_timeout" in settings
        assert "execution_backend" in settings
        assert "claude_code_connected" in settings

    async def test_post_settings_updates_timeout(self, server_client):
        """POST /api/settings with response_timeout=60 persists correctly."""
        resp = await server_client.post(
            "/api/settings",
            json={"response_timeout": 60},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Verify it persisted
        get_resp = await server_client.get("/api/settings")
        assert get_resp.json()["response_timeout"] == 60

    async def test_post_settings_updates_backend(self, server_client):
        """POST /api/settings with valid execution_backend persists correctly."""
        resp = await server_client.post(
            "/api/settings",
            json={"execution_backend": "api"},
        )
        assert resp.status_code == 200

        get_resp = await server_client.get("/api/settings")
        assert get_resp.json()["execution_backend"] == "api"


class TestTools:
    """GET /api/tools."""

    async def test_list_tools_returns_200_with_tools_key(self, server_client):
        """GET /api/tools returns 200 with a tools key (may be empty list)."""
        resp = await server_client.get("/api/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert "tools" in data
        assert isinstance(data["tools"], list)


# =====================================================================
# D6: Error paths
# =====================================================================


class TestErrorPaths:
    """Error handling for malformed requests, missing fields, bad IDs."""

    async def test_create_channel_empty_body(self, server_client):
        """POST /api/channels with empty JSON object returns error for missing name."""
        resp = await server_client.post("/api/channels", json={})
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert "name" in data["error"].lower()

    async def test_create_channel_bad_json(self, server_client):
        """POST /api/channels with malformed JSON returns 400."""
        resp = await server_client.post(
            "/api/channels",
            content=b"this is not json at all",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data

    async def test_send_message_missing_sender(self, server_client):
        """POST /api/send with missing sender field returns 400."""
        resp = await server_client.post(
            "/api/send",
            json={"channel": "test", "message": "no sender"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert "sender" in data["error"].lower()

    async def test_send_message_missing_message(self, server_client):
        """POST /api/send with missing message field returns 400."""
        resp = await server_client.post(
            "/api/send",
            json={"channel": "test", "sender": "agent"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert "message" in data["error"].lower()

    async def test_send_message_missing_channel(self, server_client):
        """POST /api/send with missing channel field returns 400."""
        resp = await server_client.post(
            "/api/send",
            json={"sender": "agent", "message": "no channel"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data
        assert "channel" in data["error"].lower()

    async def test_send_message_bad_json(self, server_client):
        """POST /api/send with malformed JSON returns 400."""
        resp = await server_client.post(
            "/api/send",
            content=b"{broken json!!!",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data

    async def test_get_agent_bad_id_returns_404(self, server_client):
        """GET /api/agents/<bad_id> returns 404 for unknown agent."""
        resp = await server_client.get("/api/agents/totally_fake_agent_999")
        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data

    async def test_post_settings_invalid_timeout_ignored(self, server_client):
        """POST /api/settings with timeout outside 30-600 range is silently ignored."""
        # Set a valid timeout first
        await server_client.post(
            "/api/settings", json={"response_timeout": 120},
        )

        # Try invalid timeout (too low)
        resp = await server_client.post(
            "/api/settings", json={"response_timeout": 5},
        )
        assert resp.status_code == 200

        # Timeout should still be 120, not 5
        get_resp = await server_client.get("/api/settings")
        assert get_resp.json()["response_timeout"] == 120

    async def test_post_settings_invalid_timeout_too_high(self, server_client):
        """POST /api/settings with timeout > 600 is silently ignored."""
        await server_client.post(
            "/api/settings", json={"response_timeout": 300},
        )

        resp = await server_client.post(
            "/api/settings", json={"response_timeout": 9999},
        )
        assert resp.status_code == 200

        get_resp = await server_client.get("/api/settings")
        assert get_resp.json()["response_timeout"] == 300

    async def test_post_settings_bad_json(self, server_client):
        """POST /api/settings with malformed JSON returns 400."""
        resp = await server_client.post(
            "/api/settings",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data

    async def test_create_channel_duplicate_returns_409(self, server_client):
        """POST /api/channels with an existing name returns 409 conflict."""
        await server_client.post(
            "/api/channels", json={"name": "dup-test", "description": "First"},
        )
        resp = await server_client.post(
            "/api/channels", json={"name": "dup-test", "description": "Second"},
        )
        assert resp.status_code == 409
        data = resp.json()
        assert "error" in data
        assert "exists" in data["error"].lower()
