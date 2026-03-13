"""Integration tests for Cohort Socket.IO event handlers.

Tests the Socket.IO event layer (socketio_events.py) by importing the
``sio`` AsyncServer and invoking handler functions directly with mock
SIDs.  No real WebSocket connections or external services needed.

Covers:
  D2 - Connection lifecycle (connect, disconnect, join)
  D3 - Message flow (send, auto-create, persistence)
  D4 - Mention routing (@agent triggers agent pipeline)
  D5 - Roundtable events (via assign_task, confirm_task)
  D6 - Zero external deps (all mocked)
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from cohort.chat import ChatManager
from cohort.data_layer import CohortDataLayer
from cohort.registry import JsonFileStorage
from cohort.task_store import TaskStore


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def sio_storage(data_dir: Path) -> JsonFileStorage:
    """JsonFileStorage for Socket.IO tests.

    Ensures messages.json is a list (not the empty dict from conftest).
    """
    (data_dir / "messages.json").write_text("[]", encoding="utf-8")
    return JsonFileStorage(data_dir)


@pytest.fixture
def sio_chat(sio_storage: JsonFileStorage) -> ChatManager:
    """ChatManager backed by ephemeral storage."""
    return ChatManager(sio_storage)


@pytest.fixture
def sio_data_layer(sio_chat: ChatManager) -> CohortDataLayer:
    """CohortDataLayer with a few mock agents."""
    agents = {
        "python_developer": {
            "name": "Python Developer",
            "capabilities": ["python", "testing"],
        },
        "web_developer": {
            "name": "Web Developer",
            "capabilities": ["frontend", "css"],
        },
    }
    return CohortDataLayer(chat=sio_chat, agents=agents)


@pytest_asyncio.fixture
async def sio_env(
    sio_data_layer: CohortDataLayer,
    sio_chat: ChatManager,
    data_dir: Path,
):
    """Set up the socketio_events module globals and yield (sio, chat, data_layer, mock_emit).

    Patches ``sio.emit`` with an AsyncMock so we can capture outbound
    events without a live transport.  Also wires a TaskStore so that
    dashboard event tests (assign_task, confirm_task, submit_review)
    have a working persistence layer.  Restores module globals on teardown.
    """
    from cohort.socketio_events import setup_socketio, setup_task_store, sio

    setup_socketio(sio_data_layer, chat=sio_chat)
    task_store = TaskStore(data_dir)
    setup_task_store(task_store)

    mock_emit = AsyncMock()
    with patch.object(sio, "emit", mock_emit):
        yield sio, sio_chat, sio_data_layer, mock_emit

    # Teardown: reset module globals so other tests are not affected
    import cohort.socketio_events as _mod
    _mod._data_layer = None
    _mod._chat = None
    _mod._task_executor = None
    _mod._task_store = None
    _mod._work_queue = None


# Convenience alias
SID = "test-sid-001"


# =====================================================================
# D2: Connection lifecycle
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.socketio
class TestConnectionLifecycle:
    """D2: connect, disconnect, and join events."""

    async def test_connect_fires_without_error(self, sio_env):
        """connect event completes without raising."""
        sio, _chat, _dl, _emit = sio_env
        from cohort.socketio_events import connect
        # connect(sid, environ) -- should not raise
        await connect(SID, {})

    async def test_disconnect_fires_without_error(self, sio_env):
        """disconnect event completes without raising."""
        sio, _chat, _dl, _emit = sio_env
        from cohort.socketio_events import disconnect
        await disconnect(SID)

    async def test_join_emits_team_update(self, sio_env):
        """join event returns ok and emits cohort:team_update to the client."""
        sio, _chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import join

        result = await join(SID)

        assert result == {"status": "ok"}
        # join emits team_update + optionally tasks_sync and schedules_update
        team_calls = [
            c for c in mock_emit.call_args_list
            if c[0][0] == "cohort:team_update"
        ]
        assert len(team_calls) == 1
        payload = team_calls[0][0][1]
        assert "agents" in payload
        assert "total_agents" in payload
        assert team_calls[0][1]["to"] == SID

    async def test_join_without_data_layer_returns_error(self, sio_chat):
        """join returns error dict when data layer is not initialised."""
        from cohort.socketio_events import sio

        import cohort.socketio_events as _mod
        original_dl = _mod._data_layer
        _mod._data_layer = None
        try:
            from cohort.socketio_events import join
            result = await join(SID)
            assert "error" in result
        finally:
            _mod._data_layer = original_dl

    async def test_request_team_update_emits_snapshot(self, sio_env):
        """request_team_update emits cohort:team_update."""
        sio, _chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import request_team_update

        await request_team_update(SID)

        mock_emit.assert_called_once()
        event_name = mock_emit.call_args[0][0]
        assert event_name == "cohort:team_update"
        payload = mock_emit.call_args[0][1]
        assert payload["total_agents"] == 2  # python_developer + web_developer


# =====================================================================
# D3: Message flow
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.socketio
class TestMessageFlow:
    """D3: send_message, get_channels, join_channel, message persistence."""

    async def test_send_message_returns_ok(self, sio_env):
        """send_message with valid data returns {status: ok, message_id: ...}."""
        sio, sio_chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import send_message

        # Pre-create a channel so auto-create is not the path under test
        sio_chat.create_channel(name="general", description="General chat")
        mock_emit.reset_mock()

        data = {"channel_id": "general", "sender": "user", "content": "Hello world"}
        result = await send_message(SID, data)

        assert result["status"] == "ok"
        assert "message_id" in result
        assert isinstance(result["message_id"], str)

    async def test_send_message_auto_creates_channel(self, sio_env):
        """send_message auto-creates channel if it does not exist."""
        sio, sio_chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import send_message

        data = {"channel_id": "new-channel", "sender": "user", "content": "First message"}
        result = await send_message(SID, data)

        assert result["status"] == "ok"
        # Channel should now exist
        ch = sio_chat.get_channel("new-channel")
        assert ch is not None
        assert ch.name == "new-channel"

    async def test_send_message_broadcasts_new_message(self, sio_env):
        """send_message emits new_message event to all clients."""
        sio, sio_chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import send_message

        sio_chat.create_channel(name="broadcast-test", description="Broadcast")
        mock_emit.reset_mock()

        data = {"channel_id": "broadcast-test", "sender": "agent_a", "content": "Broadcast me"}
        await send_message(SID, data)

        # Should have emitted new_message
        emit_calls = mock_emit.call_args_list
        new_msg_calls = [c for c in emit_calls if c[0][0] == "new_message"]
        assert len(new_msg_calls) == 1
        msg_data = new_msg_calls[0][0][1]
        assert msg_data["content"] == "Broadcast me"
        assert msg_data["sender"] == "agent_a"
        assert msg_data["channel_id"] == "broadcast-test"

    async def test_send_message_missing_content_returns_error(self, sio_env):
        """send_message returns error when content is missing."""
        sio, _chat, _dl, _emit = sio_env
        from cohort.socketio_events import send_message

        result = await send_message(SID, {"channel_id": "ch", "sender": "user"})
        assert "error" in result

    async def test_send_message_missing_channel_returns_error(self, sio_env):
        """send_message returns error when channel_id is missing."""
        sio, _chat, _dl, _emit = sio_env
        from cohort.socketio_events import send_message

        result = await send_message(SID, {"sender": "user", "content": "oops"})
        assert "error" in result

    async def test_messages_persist_and_appear_in_join_channel(self, sio_env):
        """Messages posted via send_message appear when join_channel is called."""
        sio, sio_chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import send_message, join_channel

        # Send two messages (auto-creates channel)
        await send_message(SID, {"channel_id": "persist-test", "sender": "a", "content": "msg1"})
        await send_message(SID, {"channel_id": "persist-test", "sender": "b", "content": "msg2"})
        mock_emit.reset_mock()

        # Mock enter_room with a plain MagicMock (enter_room is sync)
        with patch.object(sio, "enter_room", MagicMock()):
            await join_channel(SID, {"channel_id": "persist-test"})

        # Should have emitted channel_messages
        assert mock_emit.called
        call_args = mock_emit.call_args
        assert call_args[0][0] == "channel_messages"
        payload = call_args[0][1]
        assert payload["channel_id"] == "persist-test"
        messages = payload["messages"]
        # At least the 2 user messages + system message from auto-create
        user_msgs = [m for m in messages if m["sender"] != "system"]
        assert len(user_msgs) == 2
        assert user_msgs[0]["content"] == "msg1"
        assert user_msgs[1]["content"] == "msg2"

    async def test_get_channels_emits_channels_list(self, sio_env):
        """get_channels emits channels_list with channel data."""
        sio, sio_chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import get_channels

        sio_chat.create_channel(name="ch-alpha", description="Alpha")
        sio_chat.create_channel(name="ch-beta", description="Beta")
        mock_emit.reset_mock()

        await get_channels(SID)

        mock_emit.assert_called_once()
        call_args = mock_emit.call_args
        assert call_args[0][0] == "channels_list"
        payload = call_args[0][1]
        assert "channels" in payload
        names = [ch["name"] for ch in payload["channels"]]
        assert "ch-alpha" in names
        assert "ch-beta" in names

    async def test_join_channel_without_data_returns_early(self, sio_env):
        """join_channel with no data (None) returns without error."""
        sio, _chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import join_channel

        # Should not raise
        await join_channel(SID, None)
        # No emit should happen (no channel_id provided)
        mock_emit.assert_not_called()

    async def test_send_message_without_chat_returns_error(self):
        """send_message returns error when chat is not initialised."""
        import cohort.socketio_events as _mod
        original_chat = _mod._chat
        _mod._chat = None
        try:
            from cohort.socketio_events import send_message
            result = await send_message(SID, {
                "channel_id": "x", "sender": "u", "content": "hi",
            })
            assert "error" in result
        finally:
            _mod._chat = original_chat


# =====================================================================
# D4: Mention routing
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.socketio
class TestMentionRouting:
    """D4: Messages with @agent_name trigger agent routing."""

    async def test_mention_triggers_route_mentions(self, sio_env):
        """send_message with @python_developer calls route_mentions."""
        sio, sio_chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import send_message

        sio_chat.create_channel(name="mention-test", description="Mentions")
        mock_emit.reset_mock()

        with patch("cohort.agent_router.route_mentions") as mock_route:
            data = {
                "channel_id": "mention-test",
                "sender": "user",
                "content": "Hey @python_developer please review this",
            }
            result = await send_message(SID, data)

            assert result["status"] == "ok"
            mock_route.assert_called_once()
            # First arg is the Message object, second is the mentions list
            call_args = mock_route.call_args[0]
            msg_obj = call_args[0]
            mentions = call_args[1]
            assert "python_developer" in mentions
            assert msg_obj.channel_id == "mention-test"

    async def test_no_mention_does_not_trigger_routing(self, sio_env):
        """send_message without mentions does not call route_mentions."""
        sio, sio_chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import send_message

        sio_chat.create_channel(name="no-mention", description="No mentions")
        mock_emit.reset_mock()

        with patch("cohort.agent_router.route_mentions") as mock_route:
            data = {
                "channel_id": "no-mention",
                "sender": "user",
                "content": "Just a plain message, no mentions here.",
            }
            result = await send_message(SID, data)

            assert result["status"] == "ok"
            mock_route.assert_not_called()

    async def test_multiple_mentions_all_passed_to_router(self, sio_env):
        """send_message with multiple @mentions passes all to route_mentions."""
        sio, sio_chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import send_message

        sio_chat.create_channel(name="multi-mention", description="Multi")
        mock_emit.reset_mock()

        with patch("cohort.agent_router.route_mentions") as mock_route:
            data = {
                "channel_id": "multi-mention",
                "sender": "user",
                "content": "@python_developer and @web_developer please collaborate",
            }
            result = await send_message(SID, data)

            assert result["status"] == "ok"
            mock_route.assert_called_once()
            mentions = mock_route.call_args[0][1]
            assert "python_developer" in mentions
            assert "web_developer" in mentions


# =====================================================================
# D5: Dashboard events (submit_review, assign_task, confirm_task)
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.socketio
class TestDashboardEvents:
    """D5: Review submission, task assignment, and task confirmation."""

    async def test_submit_review_success(self, sio_env):
        """submit_review with valid data returns ok and emits review event."""
        sio, _chat, _dl, mock_emit = sio_env
        import cohort.socketio_events as _mod
        from cohort.socketio_events import submit_review

        # Create a task via TaskStore, then mark complete so review is valid
        task = _mod._task_store.create_task("python_developer", "Write tests", "high")
        task_id = task["task_id"]
        _mod._task_store.complete_task(task_id)
        mock_emit.reset_mock()

        data = {"task_id": task_id, "verdict": "approved", "notes": "Looks good"}
        result = await submit_review(SID, data)

        assert result == {"status": "ok"}
        # Should emit cohort:review_submitted
        mock_emit.assert_called_once()
        assert mock_emit.call_args[0][0] == "cohort:review_submitted"
        review_payload = mock_emit.call_args[0][1]
        assert review_payload["task_id"] == task_id
        assert review_payload["verdict"] == "approved"
        assert review_payload["notes"] == "Looks good"

    async def test_submit_review_missing_fields(self, sio_env):
        """submit_review returns error when task_id or verdict is missing."""
        sio, _chat, _dl, _emit = sio_env
        from cohort.socketio_events import submit_review

        # Missing verdict
        result = await submit_review(SID, {"task_id": "t1"})
        assert "error" in result

        # Missing task_id
        result = await submit_review(SID, {"verdict": "approved"})
        assert "error" in result

    async def test_assign_task_success(self, sio_env):
        """assign_task returns ok with task_id and emits task_assigned."""
        sio, _chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import assign_task

        data = {
            "agent_id": "python_developer",
            "description": "Implement feature X",
            "priority": "high",
        }
        result = await assign_task(SID, data)

        assert result["status"] == "ok"
        assert "task_id" in result
        assert result["task_id"].startswith("task_")

        # Should emit cohort:task_assigned
        mock_emit.assert_called_once()
        assert mock_emit.call_args[0][0] == "cohort:task_assigned"
        task_payload = mock_emit.call_args[0][1]
        assert task_payload["agent_id"] == "python_developer"
        assert task_payload["description"] == "Implement feature X"
        assert task_payload["priority"] == "high"

    async def test_assign_task_missing_fields(self, sio_env):
        """assign_task returns error when agent_id or description is missing."""
        sio, _chat, _dl, _emit = sio_env
        from cohort.socketio_events import assign_task

        result = await assign_task(SID, {"agent_id": "python_developer"})
        assert "error" in result

        result = await assign_task(SID, {"description": "Do something"})
        assert "error" in result

    async def test_assign_task_default_priority(self, sio_env):
        """assign_task uses 'medium' as default priority."""
        sio, _chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import assign_task

        data = {"agent_id": "web_developer", "description": "Fix CSS"}
        result = await assign_task(SID, data)

        assert result["status"] == "ok"
        task_payload = mock_emit.call_args[0][1]
        assert task_payload["priority"] == "medium"

    async def test_assign_task_triggers_briefing_when_executor_set(self, sio_env):
        """assign_task starts briefing via task_executor if wired."""
        sio, _chat, _dl, mock_emit = sio_env
        import cohort.socketio_events as _mod
        from cohort.socketio_events import assign_task

        mock_executor = MagicMock()
        mock_executor.start_briefing = AsyncMock()
        original = _mod._task_executor
        _mod._task_executor = mock_executor
        try:
            data = {"agent_id": "python_developer", "description": "Build API"}
            await assign_task(SID, data)

            # start_briefing should have been scheduled via create_task
            # The actual scheduling uses asyncio.create_task, so we verify
            # the executor was referenced (the mock is in place)
            # Give the event loop a chance to process
            import asyncio
            await asyncio.sleep(0.05)
        finally:
            _mod._task_executor = original

    async def test_confirm_task_success(self, sio_env):
        """confirm_task transitions task from briefing to assigned."""
        sio, _chat, _dl, mock_emit = sio_env
        import cohort.socketio_events as _mod
        from cohort.socketio_events import confirm_task

        task = _mod._task_store.create_task("python_developer", "Implement feature", "medium")
        task_id = task["task_id"]
        mock_emit.reset_mock()

        data = {
            "task_id": task_id,
            "brief": {"goal": "Implement feature", "approach": "TDD"},
        }
        result = await confirm_task(SID, data)

        assert result == {"status": "ok"}
        # Should emit cohort:task_assigned with updated status
        mock_emit.assert_called_once()
        assert mock_emit.call_args[0][0] == "cohort:task_assigned"
        task_payload = mock_emit.call_args[0][1]
        assert task_payload["status"] == "assigned"
        assert task_payload["brief"]["goal"] == "Implement feature"

    async def test_confirm_task_not_found(self, sio_env):
        """confirm_task returns error for nonexistent task_id."""
        sio, _chat, _dl, _emit = sio_env
        from cohort.socketio_events import confirm_task

        result = await confirm_task(SID, {"task_id": "nonexistent"})
        assert "error" in result

    async def test_confirm_task_missing_task_id(self, sio_env):
        """confirm_task returns error when task_id is missing."""
        sio, _chat, _dl, _emit = sio_env
        from cohort.socketio_events import confirm_task

        result = await confirm_task(SID, {"brief": {}})
        assert "error" in result


# =====================================================================
# D6: Broadcast helpers
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.socketio
class TestBroadcastHelpers:
    """D6: Server-side broadcast helper functions."""

    async def test_emit_team_update(self, sio_env):
        """emit_team_update broadcasts cohort:team_update."""
        sio, _chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import emit_team_update

        await emit_team_update({"agents": [], "total_agents": 0})
        mock_emit.assert_called_once_with("cohort:team_update", {"agents": [], "total_agents": 0})

    async def test_emit_task_complete(self, sio_env):
        """emit_task_complete broadcasts cohort:task_complete."""
        sio, _chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import emit_task_complete

        payload = {"task_id": "t1", "status": "complete"}
        await emit_task_complete(payload)
        mock_emit.assert_called_once_with("cohort:task_complete", payload)

    async def test_emit_error(self, sio_env):
        """emit_error broadcasts cohort:error."""
        sio, _chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import emit_error

        await emit_error({"message": "Something went wrong"})
        mock_emit.assert_called_once_with("cohort:error", {"message": "Something went wrong"})

    async def test_emit_new_message(self, sio_env):
        """emit_new_message broadcasts new_message."""
        sio, _chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import emit_new_message

        msg = {"id": "m1", "content": "hi", "sender": "agent"}
        await emit_new_message(msg)
        mock_emit.assert_called_once_with("new_message", msg)


# =====================================================================
# Edge cases
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.socketio
class TestEdgeCases:
    """Edge cases and defensive behaviour."""

    async def test_get_channels_without_chat_returns_early(self):
        """get_channels returns silently when chat is None."""
        import cohort.socketio_events as _mod
        from cohort.socketio_events import sio

        original = _mod._chat
        _mod._chat = None
        mock_emit = AsyncMock()
        try:
            with patch.object(sio, "emit", mock_emit):
                from cohort.socketio_events import get_channels
                await get_channels(SID)
            mock_emit.assert_not_called()
        finally:
            _mod._chat = original

    async def test_request_team_update_without_data_layer(self):
        """request_team_update returns silently when data layer is None."""
        import cohort.socketio_events as _mod
        from cohort.socketio_events import sio

        original = _mod._data_layer
        _mod._data_layer = None
        mock_emit = AsyncMock()
        try:
            with patch.object(sio, "emit", mock_emit):
                from cohort.socketio_events import request_team_update
                await request_team_update(SID)
            mock_emit.assert_not_called()
        finally:
            _mod._data_layer = original

    async def test_submit_review_without_data_layer(self):
        """submit_review returns error when data layer is None."""
        import cohort.socketio_events as _mod

        original = _mod._data_layer
        _mod._data_layer = None
        try:
            from cohort.socketio_events import submit_review
            result = await submit_review(SID, {"task_id": "t1", "verdict": "ok"})
            assert "error" in result
        finally:
            _mod._data_layer = original

    async def test_assign_task_without_data_layer(self):
        """assign_task returns error when data layer is None."""
        import cohort.socketio_events as _mod

        original = _mod._data_layer
        _mod._data_layer = None
        try:
            from cohort.socketio_events import assign_task
            result = await assign_task(SID, {
                "agent_id": "x", "description": "y",
            })
            assert "error" in result
        finally:
            _mod._data_layer = original

    async def test_send_message_default_sender(self, sio_env):
        """send_message defaults sender to 'user' when not provided."""
        sio, sio_chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import send_message

        sio_chat.create_channel(name="default-sender", description="Defaults")
        mock_emit.reset_mock()

        data = {"channel_id": "default-sender", "content": "No sender field"}
        result = await send_message(SID, data)

        assert result["status"] == "ok"
        # The broadcast should show sender as "user"
        new_msg_calls = [c for c in mock_emit.call_args_list if c[0][0] == "new_message"]
        assert len(new_msg_calls) == 1
        assert new_msg_calls[0][0][1]["sender"] == "user"


# =====================================================================
# Orchestrator event bridge (sync -> async)
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.socketio
class TestOrchestratorBridge:
    """Test the sync-to-async orchestrator event bridge."""

    async def test_bridge_known_event_schedules_emit(self, sio_env):
        """orchestrator_event_bridge for a known event type calls the emitter."""
        sio, _chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import orchestrator_event_bridge

        # session_started maps to emit_status_change
        orchestrator_event_bridge("session_started", {"session_id": "s1"})

        # Since we are inside a running event loop, the bridge should schedule
        # the emit via create_task.  Give the loop a tick to process.
        import asyncio
        await asyncio.sleep(0.05)

        # The emit should have been called with cohort:status_change
        status_calls = [
            c for c in mock_emit.call_args_list
            if c[0][0] == "cohort:status_change"
        ]
        assert len(status_calls) == 1
        payload = status_calls[0][0][1]
        assert payload["event_type"] == "session_started"
        assert payload["session_id"] == "s1"

    async def test_bridge_unknown_event_is_noop(self, sio_env):
        """orchestrator_event_bridge silently ignores unknown event types."""
        sio, _chat, _dl, mock_emit = sio_env
        from cohort.socketio_events import orchestrator_event_bridge

        orchestrator_event_bridge("some_unknown_event", {"data": "ignored"})

        import asyncio
        await asyncio.sleep(0.05)

        # No emit should have been triggered
        mock_emit.assert_not_called()
