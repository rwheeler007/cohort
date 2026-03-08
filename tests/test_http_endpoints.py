"""Integration tests for Cohort server HTTP endpoints.

Tests all REST API endpoints exposed by the Starlette server:
channels CRUD, messages, agents, settings, permissions, tools,
roundtable, and task management.

Uses httpx.AsyncClient with ASGITransport from conftest.py fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.server, pytest.mark.asyncio]


# =====================================================================
# Health
# =====================================================================


class TestHealth:
    async def test_health_ok(self, server_client):
        resp = await server_client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# =====================================================================
# Channels CRUD
# =====================================================================


class TestChannels:
    async def test_list_channels_empty(self, server_client):
        resp = await server_client.get("/api/channels")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_create_channel(self, server_client):
        resp = await server_client.post(
            "/api/channels",
            json={
                "name": "test-chan",
                "description": "A test channel",
                "members": ["alice", "bob"],
                "is_private": False,
                "topic": "Testing",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["channel"]["id"] == "test-chan"
        assert body["channel"]["description"] == "A test channel"
        assert body["channel"]["members"] == ["alice", "bob"]
        assert body["channel"]["topic"] == "Testing"

    async def test_create_channel_appears_in_list(self, server_client):
        await server_client.post(
            "/api/channels",
            json={"name": "listed-chan", "description": "visible"},
        )
        resp = await server_client.get("/api/channels")
        assert resp.status_code == 200
        names = [ch["id"] for ch in resp.json()]
        assert "listed-chan" in names

    async def test_create_channel_missing_name(self, server_client):
        resp = await server_client.post(
            "/api/channels",
            json={"description": "no name"},
        )
        assert resp.status_code == 400
        assert "name" in resp.json()["error"].lower()

    async def test_create_channel_invalid_json(self, server_client):
        resp = await server_client.post(
            "/api/channels",
            content=b"not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

    async def test_create_channel_duplicate(self, server_client):
        await server_client.post(
            "/api/channels",
            json={"name": "dup-chan", "description": "first"},
        )
        resp = await server_client.post(
            "/api/channels",
            json={"name": "dup-chan", "description": "second"},
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["error"]


# =====================================================================
# Channel condense
# =====================================================================


class TestCondense:
    async def test_condense_nonexistent_channel(self, server_client):
        resp = await server_client.post(
            "/api/channels/nonexistent/condense",
            json={"keep_last": 3},
        )
        assert resp.status_code == 404

    async def test_condense_keeps_last_messages(self, server_client):
        # Create a channel with several messages
        await server_client.post(
            "/api/channels",
            json={"name": "condense-chan", "description": "will condense"},
        )
        for i in range(6):
            await server_client.post(
                "/api/send",
                json={
                    "channel": "condense-chan",
                    "sender": "tester",
                    "message": f"msg-{i}",
                },
            )

        resp = await server_client.post(
            "/api/channels/condense-chan/condense",
            json={"keep_last": 2},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["archived_count"] > 0

        # Verify only the kept messages remain
        msgs_resp = await server_client.get(
            "/api/messages", params={"channel": "condense-chan"}
        )
        messages = msgs_resp.json()["messages"]
        assert len(messages) == 2

    async def test_condense_nothing_to_condense(self, server_client):
        await server_client.post(
            "/api/channels",
            json={"name": "tiny-chan", "description": "few msgs"},
        )
        # Channel is created with 1 system message -- condense with keep_last=5
        resp = await server_client.post(
            "/api/channels/tiny-chan/condense",
            json={"keep_last": 5},
        )
        assert resp.status_code == 200
        assert resp.json()["archived_count"] == 0


# =====================================================================
# Messages
# =====================================================================


class TestMessages:
    async def test_get_messages_missing_channel_param(self, server_client):
        resp = await server_client.get("/api/messages")
        assert resp.status_code == 400
        assert "channel" in resp.json()["error"].lower()

    async def test_send_and_get_messages(self, server_client):
        # Create channel first
        await server_client.post(
            "/api/channels",
            json={"name": "msg-chan", "description": "for messages"},
        )
        send_resp = await server_client.post(
            "/api/send",
            json={
                "channel": "msg-chan",
                "sender": "alice",
                "message": "Hello world",
            },
        )
        assert send_resp.status_code == 200
        body = send_resp.json()
        assert body["success"] is True
        assert "message_id" in body

        # Retrieve and check
        get_resp = await server_client.get(
            "/api/messages", params={"channel": "msg-chan"}
        )
        assert get_resp.status_code == 200
        messages = get_resp.json()["messages"]
        user_msgs = [m for m in messages if m["sender"] == "alice"]
        assert len(user_msgs) == 1
        assert user_msgs[0]["content"] == "Hello world"

    async def test_send_auto_creates_channel(self, server_client):
        resp = await server_client.post(
            "/api/send",
            json={
                "channel": "auto-created",
                "sender": "bot",
                "message": "I exist now",
            },
        )
        assert resp.status_code == 200
        # Channel should now exist
        channels_resp = await server_client.get("/api/channels")
        names = [ch["id"] for ch in channels_resp.json()]
        assert "auto-created" in names

    async def test_send_missing_fields(self, server_client):
        resp = await server_client.post(
            "/api/send",
            json={"channel": "some-chan"},
        )
        assert resp.status_code == 400
        assert "sender" in resp.json()["error"].lower()

    async def test_send_invalid_json(self, server_client):
        resp = await server_client.post(
            "/api/send",
            content=b"{broken",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

    async def test_get_messages_with_limit(self, server_client):
        await server_client.post(
            "/api/channels",
            json={"name": "limit-chan", "description": "limit test"},
        )
        for i in range(5):
            await server_client.post(
                "/api/send",
                json={
                    "channel": "limit-chan",
                    "sender": "bot",
                    "message": f"msg-{i}",
                },
            )
        resp = await server_client.get(
            "/api/messages", params={"channel": "limit-chan", "limit": "2"}
        )
        assert resp.status_code == 200
        assert len(resp.json()["messages"]) == 2

    async def test_send_message_with_mentions(self, server_client):
        await server_client.post(
            "/api/channels",
            json={"name": "mention-chan", "description": "mention test"},
        )
        resp = await server_client.post(
            "/api/send",
            json={
                "channel": "mention-chan",
                "sender": "user",
                "message": "Hey @python_developer check this",
            },
        )
        assert resp.status_code == 200

        get_resp = await server_client.get(
            "/api/messages", params={"channel": "mention-chan"}
        )
        user_msgs = [
            m for m in get_resp.json()["messages"] if m["sender"] == "user"
        ]
        assert len(user_msgs) == 1
        assert "python_developer" in user_msgs[0]["metadata"].get("mentions", [])


# =====================================================================
# Agents
# =====================================================================


class TestAgents:
    async def test_list_agents(self, server_client):
        resp = await server_client.get("/api/agents")
        assert resp.status_code == 200
        body = resp.json()
        assert "agents" in body
        assert "total_agents" in body

    async def test_register_agent(self, server_client):
        resp = await server_client.post(
            "/api/agents",
            json={
                "agent_id": "test_agent",
                "name": "Test Agent",
                "triggers": ["test"],
                "capabilities": ["testing"],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["agent_id"] == "test_agent"

    async def test_register_agent_missing_id(self, server_client):
        resp = await server_client.post(
            "/api/agents",
            json={"name": "No ID"},
        )
        assert resp.status_code == 400
        assert "agent_id" in resp.json()["error"].lower()

    async def test_register_agent_invalid_json(self, server_client):
        resp = await server_client.post(
            "/api/agents",
            content=b"nope",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

    async def test_get_agent_detail(self, server_client):
        # Agents from conftest.py fixtures are file-backed
        resp = await server_client.get("/api/agents/python_developer")
        assert resp.status_code == 200
        body = resp.json()
        assert body["agent_id"] == "python_developer"

    async def test_get_agent_detail_404(self, server_client):
        resp = await server_client.get("/api/agents/nonexistent_agent")
        assert resp.status_code == 404

    async def test_get_agent_prompt(self, server_client):
        resp = await server_client.get("/api/agents/python_developer/prompt")
        assert resp.status_code == 200
        body = resp.json()
        assert body["agent_id"] == "python_developer"
        assert "prompt" in body
        assert len(body["prompt"]) > 0

    async def test_get_agent_prompt_404(self, server_client):
        resp = await server_client.get("/api/agents/nonexistent_agent/prompt")
        assert resp.status_code == 404


# =====================================================================
# Agent memory
# =====================================================================


class TestAgentMemory:
    async def test_get_agent_memory_404(self, server_client):
        resp = await server_client.get("/api/agents/nonexistent_agent/memory")
        assert resp.status_code == 404

    async def test_add_agent_fact(self, server_client):
        resp = await server_client.post(
            "/api/agents/python_developer/memory/facts",
            json={
                "fact": "Python 3.12 supports f-string nesting",
                "learned_from": "test",
                "confidence": "high",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["agent_id"] == "python_developer"
        assert body["fact"]["fact"] == "Python 3.12 supports f-string nesting"

    async def test_add_agent_fact_missing_fact_field(self, server_client):
        resp = await server_client.post(
            "/api/agents/python_developer/memory/facts",
            json={"learned_from": "test"},
        )
        assert resp.status_code == 400
        assert "fact" in resp.json()["error"].lower()

    async def test_add_agent_fact_invalid_json(self, server_client):
        resp = await server_client.post(
            "/api/agents/python_developer/memory/facts",
            content=b"bad",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

    async def test_clean_agent_memory(self, server_client):
        # First add some facts so memory exists
        await server_client.post(
            "/api/agents/python_developer/memory/facts",
            json={"fact": "test fact 1", "learned_from": "test"},
        )
        resp = await server_client.post(
            "/api/agents/python_developer/memory/clean",
            json={"keep_last": 10, "dry_run": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "working_memory_removed" in body
        assert "working_memory_kept" in body


# =====================================================================
# Agent creation
# =====================================================================


class TestAgentCreation:
    async def test_create_agent(self, server_client):
        resp = await server_client.post(
            "/api/agents/create",
            json={
                "name": "New Agent",
                "role": "Tester",
                "primary_task": "Run tests",
                "personality": "Thorough and detail-oriented",
                "capabilities": ["testing"],
                "domain_expertise": ["qa"],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["agent_id"] == "new_agent"
        assert body["config"]["role"] == "Tester"

    async def test_create_agent_missing_fields(self, server_client):
        resp = await server_client.post(
            "/api/agents/create",
            json={"name": "Incomplete"},
        )
        assert resp.status_code == 400
        assert "role" in resp.json()["error"].lower()

    async def test_create_agent_invalid_json(self, server_client):
        resp = await server_client.post(
            "/api/agents/create",
            content=b"nope",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

    async def test_create_agent_duplicate(self, server_client):
        payload = {
            "name": "Dup Agent",
            "role": "Tester",
            "primary_task": "test",
        }
        await server_client.post("/api/agents/create", json=payload)
        resp = await server_client.post("/api/agents/create", json=payload)
        assert resp.status_code == 409


# =====================================================================
# Agent registry (visual profiles)
# =====================================================================


class TestAgentRegistry:
    async def test_get_agent_registry(self, server_client):
        resp = await server_client.get("/api/agent-registry")
        assert resp.status_code == 200
        body = resp.json()
        # Should be a dict of agent_id -> profile
        assert isinstance(body, dict)


# =====================================================================
# Settings
# =====================================================================


class TestSettings:
    async def test_get_settings(self, server_client):
        resp = await server_client.get("/api/settings")
        assert resp.status_code == 200
        body = resp.json()
        assert "api_key_masked" in body
        assert "claude_cmd" in body
        assert "agents_root" in body
        assert "response_timeout" in body
        assert "execution_backend" in body
        assert "claude_code_connected" in body

    async def test_post_settings(self, server_client):
        resp = await server_client.post(
            "/api/settings",
            json={
                "response_timeout": 120,
                "execution_backend": "api",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Verify persisted
        get_resp = await server_client.get("/api/settings")
        assert get_resp.json()["response_timeout"] == 120
        assert get_resp.json()["execution_backend"] == "api"

    async def test_post_settings_invalid_timeout(self, server_client):
        # Timeouts outside 30-600 should be ignored (not error, just not stored)
        resp = await server_client.post(
            "/api/settings",
            json={"response_timeout": 5},
        )
        assert resp.status_code == 200
        # Should still have default
        get_resp = await server_client.get("/api/settings")
        assert get_resp.json()["response_timeout"] != 5

    async def test_post_settings_invalid_backend(self, server_client):
        resp = await server_client.post(
            "/api/settings",
            json={"execution_backend": "invalid_value"},
        )
        assert resp.status_code == 200
        # Should still have default
        get_resp = await server_client.get("/api/settings")
        assert get_resp.json()["execution_backend"] != "invalid_value"

    async def test_post_settings_invalid_json(self, server_client):
        resp = await server_client.post(
            "/api/settings",
            content=b"bad json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400


# =====================================================================
# Permissions
# =====================================================================


class TestPermissions:
    async def test_get_permissions_empty(self, server_client):
        resp = await server_client.get("/api/permissions")
        assert resp.status_code == 200
        body = resp.json()
        assert "services" in body
        assert "permissions" in body

    async def test_post_permissions(self, server_client):
        resp = await server_client.post(
            "/api/permissions",
            json={
                "services": [
                    {
                        "id": "openai",
                        "type": "llm",
                        "name": "OpenAI",
                        "new_key": "sk-test1234567890abcdef",
                    },
                ],
                "permissions": {"python_developer": {"openai": True}},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # Verify key is masked on read-back
        get_resp = await server_client.get("/api/permissions")
        services = get_resp.json()["services"]
        assert len(services) == 1
        assert services[0]["has_key"] is True
        assert services[0]["key_masked"].startswith("...")

    async def test_post_permissions_invalid_json(self, server_client):
        resp = await server_client.post(
            "/api/permissions",
            content=b"nope",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400


# =====================================================================
# Tools
# =====================================================================


class TestTools:
    async def test_list_tools_no_config(self, server_client):
        """Tools endpoint returns empty list when no cohort_tools.json exists."""
        resp = await server_client.get("/api/tools")
        assert resp.status_code == 200
        assert resp.json() == {"tools": []}

    async def test_list_tools_with_cohort_tools(self, server_client, data_dir):
        """Tools endpoint reads from cohort_tools.json."""
        import json

        cohort_tools = {
            "version": 1,
            "tools": ["code_review"],
            "display_names": {"code_review": "Code Review"},
            "descriptions": {"code_review": "Review code changes"},
        }
        (data_dir / "cohort_tools.json").write_text(
            json.dumps(cohort_tools), encoding="utf-8"
        )

        resp = await server_client.get("/api/tools")
        assert resp.status_code == 200
        tools = resp.json()["tools"]
        assert len(tools) == 1
        assert tools[0]["id"] == "code_review"
        assert tools[0]["name"] == "Code Review"
        assert tools[0]["description"] == "Review code changes"
        assert tools[0]["implemented"] is True


# =====================================================================
# Tasks
# =====================================================================


class TestTasks:
    async def test_get_tasks_empty(self, server_client):
        resp = await server_client.get("/api/tasks")
        assert resp.status_code == 200
        assert resp.json() == {"tasks": []}

    async def test_get_outputs_empty(self, server_client):
        resp = await server_client.get("/api/outputs")
        assert resp.status_code == 200
        assert resp.json() == {"outputs": []}


# =====================================================================
# Roundtable
# =====================================================================


class TestRoundtable:
    async def test_start_roundtable(self, server_client):
        resp = await server_client.post(
            "/api/roundtable/start",
            json={
                "channel_id": "rt-test",
                "topic": "Test topic",
                "initial_agents": ["python_developer"],
                "max_turns": 5,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        session = body["session"]
        assert session["channel_id"] == "rt-test"
        assert session["topic"] == "Test topic"
        assert session["state"] == "active"
        return session["session_id"]

    async def test_start_roundtable_missing_channel(self, server_client):
        resp = await server_client.post(
            "/api/roundtable/start",
            json={"topic": "No channel"},
        )
        assert resp.status_code == 400
        assert "channel_id" in resp.json()["error"]

    async def test_start_roundtable_missing_topic(self, server_client):
        resp = await server_client.post(
            "/api/roundtable/start",
            json={"channel_id": "rt-notopic"},
        )
        assert resp.status_code == 400
        assert "topic" in resp.json()["error"]

    async def test_start_roundtable_duplicate_channel(self, server_client):
        await server_client.post(
            "/api/roundtable/start",
            json={"channel_id": "rt-dup", "topic": "First"},
        )
        resp = await server_client.post(
            "/api/roundtable/start",
            json={"channel_id": "rt-dup", "topic": "Second"},
        )
        assert resp.status_code == 409

    async def test_get_roundtable_status(self, server_client):
        # Start a session first
        start_resp = await server_client.post(
            "/api/roundtable/start",
            json={"channel_id": "rt-status", "topic": "Status test"},
        )
        session_id = start_resp.json()["session"]["session_id"]

        resp = await server_client.get(f"/api/roundtable/{session_id}/status")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    async def test_get_roundtable_status_404(self, server_client):
        resp = await server_client.get("/api/roundtable/nonexistent/status")
        assert resp.status_code == 404

    async def test_record_turn_missing_fields(self, server_client):
        start_resp = await server_client.post(
            "/api/roundtable/start",
            json={"channel_id": "rt-turn", "topic": "Turn test"},
        )
        session_id = start_resp.json()["session"]["session_id"]

        resp = await server_client.post(
            f"/api/roundtable/{session_id}/record-turn",
            json={"speaker": "alice"},
        )
        assert resp.status_code == 400
        assert "message_id" in resp.json()["error"]

    async def test_record_turn_and_end(self, server_client):
        # Start session
        start_resp = await server_client.post(
            "/api/roundtable/start",
            json={"channel_id": "rt-lifecycle", "topic": "Lifecycle test"},
        )
        session_id = start_resp.json()["session"]["session_id"]

        # Post a message to the channel so we have a message_id
        msg_resp = await server_client.post(
            "/api/send",
            json={
                "channel": "rt-lifecycle",
                "sender": "python_developer",
                "message": "My contribution to the roundtable",
            },
        )
        message_id = msg_resp.json()["message_id"]

        # Record a turn
        turn_resp = await server_client.post(
            f"/api/roundtable/{session_id}/record-turn",
            json={
                "speaker": "python_developer",
                "message_id": message_id,
                "was_recommended": True,
            },
        )
        assert turn_resp.status_code == 200
        assert turn_resp.json()["success"] is True

        # End session
        end_resp = await server_client.post(
            f"/api/roundtable/{session_id}/end",
        )
        assert end_resp.status_code == 200
        assert end_resp.json()["success"] is True

    async def test_end_roundtable_404(self, server_client):
        resp = await server_client.post("/api/roundtable/nonexistent/end")
        assert resp.status_code == 404

    async def test_get_channel_roundtable_no_session(self, server_client):
        resp = await server_client.get("/api/roundtable/channel/empty-chan")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["has_session"] is False
        assert body["session"] is None

    async def test_get_channel_roundtable_with_session(self, server_client):
        await server_client.post(
            "/api/roundtable/start",
            json={"channel_id": "rt-lookup", "topic": "Lookup test"},
        )
        resp = await server_client.get("/api/roundtable/channel/rt-lookup")
        assert resp.status_code == 200
        body = resp.json()
        assert body["has_session"] is True
        assert body["session"]["channel_id"] == "rt-lookup"

    async def test_get_next_speaker(self, server_client):
        start_resp = await server_client.post(
            "/api/roundtable/start",
            json={
                "channel_id": "rt-speaker",
                "topic": "Speaker test",
                "initial_agents": ["python_developer", "web_developer"],
            },
        )
        session_id = start_resp.json()["session"]["session_id"]

        resp = await server_client.get(
            f"/api/roundtable/{session_id}/next-speaker"
        )
        # Either 200 with a recommendation or 400 if no speaker available
        assert resp.status_code in (200, 400)


# =====================================================================
# Test connection (settings)
# =====================================================================


class TestConnection:
    async def test_connection_no_cli_configured(self, server_client):
        resp = await server_client.post("/api/settings/test-connection")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "not configured" in body["error"].lower()
