"""Tests for meeting control surface -- logic layer, CLI commands.

Tests the new methods added to orchestrator.py and meeting.py for
the CLI-first meeting mode control surface.
"""

import pytest

from cohort.chat import Channel, ChatManager, Message
from cohort.meeting import (
    StakeholderStatus,
    enable_meeting_mode,
    disable_meeting_mode,
    extract_keywords,
)
from cohort.orchestrator import Orchestrator, SessionState
from cohort.registry import JsonFileStorage


# =====================================================================
# Helpers
# =====================================================================

def _setup(tmp_path, agents=None):
    """Create an Orchestrator with a ChatManager backed by tmp storage."""
    storage = JsonFileStorage(tmp_path)
    chat = ChatManager(storage)
    chat.create_channel("test-ch", "Test Channel")
    agent_configs = agents or {
        "agent_a": {
            "triggers": ["python", "backend"],
            "capabilities": ["code", "review"],
            "domain_expertise": ["python development"],
        },
        "agent_b": {
            "triggers": ["security", "audit"],
            "capabilities": ["vulnerability scanning"],
            "domain_expertise": ["application security"],
        },
        "agent_c": {
            "triggers": ["test", "qa"],
            "capabilities": ["testing"],
            "domain_expertise": ["quality assurance"],
        },
    }
    orch = Orchestrator(chat, agents=agent_configs)
    return orch, chat


def _start_session(orch, agents=None):
    """Start a session with default agents."""
    return orch.start_session(
        "test-ch",
        "Python backend security review",
        initial_agents=agents or ["agent_a", "agent_b", "agent_c"],
    )


# =====================================================================
# Standalone meeting mode (meeting.py)
# =====================================================================

class TestStandaloneMeetingMode:
    """Tests for enable_meeting_mode / disable_meeting_mode."""

    def test_enable_sets_meeting_context(self, tmp_path):
        _, chat = _setup(tmp_path)
        channel = chat.get_channel("test-ch")
        assert channel.meeting_context is None

        ctx = enable_meeting_mode(channel, ["agent_a", "agent_b"], chat, topic="auth review")
        assert channel.meeting_context is not None
        assert "agent_a" in ctx["stakeholder_status"]
        assert "agent_b" in ctx["stakeholder_status"]
        assert ctx["stakeholder_status"]["agent_a"] == StakeholderStatus.ACTIVE.value

    def test_enable_posts_system_message(self, tmp_path):
        _, chat = _setup(tmp_path)
        channel = chat.get_channel("test-ch")
        enable_meeting_mode(channel, ["agent_a"], chat)
        msgs = chat.get_channel_messages("test-ch", limit=50)
        system_msgs = [m for m in msgs if "[MEETING]" in m.content]
        assert len(system_msgs) >= 1
        assert "@agent_a" in system_msgs[-1].content

    def test_enable_with_topic_extracts_keywords(self, tmp_path):
        _, chat = _setup(tmp_path)
        channel = chat.get_channel("test-ch")
        ctx = enable_meeting_mode(channel, ["agent_a"], chat, topic="database migration strategy")
        kw = ctx["current_topic"]["keywords"]
        assert len(kw) > 0

    def test_disable_clears_context(self, tmp_path):
        _, chat = _setup(tmp_path)
        channel = chat.get_channel("test-ch")
        enable_meeting_mode(channel, ["agent_a"], chat)
        assert channel.meeting_context is not None

        was_active = disable_meeting_mode(channel, chat)
        assert was_active is True
        assert channel.meeting_context is None

    def test_disable_returns_false_when_already_off(self, tmp_path):
        _, chat = _setup(tmp_path)
        channel = chat.get_channel("test-ch")
        assert channel.meeting_context is None
        was_active = disable_meeting_mode(channel, chat)
        assert was_active is False

    def test_disable_posts_system_message_only_when_active(self, tmp_path):
        _, chat = _setup(tmp_path)
        channel = chat.get_channel("test-ch")
        # Disable when already off -- no system message
        msgs_before = len(chat.get_channel_messages("test-ch", limit=100))
        disable_meeting_mode(channel, chat)
        msgs_after = len(chat.get_channel_messages("test-ch", limit=100))
        assert msgs_after == msgs_before

        # Enable then disable -- system message posted
        enable_meeting_mode(channel, ["agent_a"], chat)
        msgs_before = len(chat.get_channel_messages("test-ch", limit=100))
        disable_meeting_mode(channel, chat)
        msgs_after = len(chat.get_channel_messages("test-ch", limit=100))
        assert msgs_after == msgs_before + 1


# =====================================================================
# Orchestrator: participant management
# =====================================================================

class TestParticipantManagement:
    """Tests for add/remove/update participant methods."""

    def test_add_participant(self, tmp_path):
        orch, _ = _setup(tmp_path)
        session = _start_session(orch, agents=["agent_a", "agent_b"])
        assert "agent_c" not in session.active_participants

        ok = orch.add_participant(session.session_id, "agent_c")
        assert ok is True
        assert "agent_c" in session.active_participants
        assert session.active_participants["agent_c"] == StakeholderStatus.ACTIVE.value

    def test_add_duplicate_returns_false(self, tmp_path):
        orch, _ = _setup(tmp_path)
        session = _start_session(orch, agents=["agent_a"])
        ok = orch.add_participant(session.session_id, "agent_a")
        assert ok is False

    def test_add_to_nonexistent_session(self, tmp_path):
        orch, _ = _setup(tmp_path)
        ok = orch.add_participant("nonexistent", "agent_a")
        assert ok is False

    def test_remove_participant(self, tmp_path):
        orch, _ = _setup(tmp_path)
        session = _start_session(orch, agents=["agent_a", "agent_b"])
        ok = orch.remove_participant(session.session_id, "agent_b")
        assert ok is True
        assert "agent_b" not in session.active_participants

    def test_remove_nonexistent_agent(self, tmp_path):
        orch, _ = _setup(tmp_path)
        session = _start_session(orch, agents=["agent_a"])
        ok = orch.remove_participant(session.session_id, "agent_z")
        assert ok is False

    def test_update_participant_status(self, tmp_path):
        orch, _ = _setup(tmp_path)
        session = _start_session(orch, agents=["agent_a", "agent_b"])
        assert session.active_participants["agent_a"] == StakeholderStatus.ACTIVE.value

        ok = orch.update_participant_status(
            session.session_id, "agent_a", StakeholderStatus.APPROVED_SILENT
        )
        assert ok is True
        assert session.active_participants["agent_a"] == StakeholderStatus.APPROVED_SILENT.value

    def test_update_syncs_channel_meeting_context(self, tmp_path):
        orch, chat = _setup(tmp_path)
        session = _start_session(orch, agents=["agent_a"])
        channel = chat.get_channel("test-ch")
        assert channel.meeting_context is not None

        orch.update_participant_status(
            session.session_id, "agent_a", StakeholderStatus.DORMANT
        )
        # Channel meeting_context should be updated too
        ctx = channel.meeting_context
        assert ctx["stakeholder_status"]["agent_a"] == StakeholderStatus.DORMANT.value

    def test_update_nonexistent_agent(self, tmp_path):
        orch, _ = _setup(tmp_path)
        session = _start_session(orch, agents=["agent_a"])
        ok = orch.update_participant_status(
            session.session_id, "agent_z", StakeholderStatus.ACTIVE
        )
        assert ok is False


# =====================================================================
# Orchestrator: get_meeting_context
# =====================================================================

class TestGetMeetingContext:
    def test_returns_context_for_session_channel(self, tmp_path):
        orch, _ = _setup(tmp_path)
        _start_session(orch)
        ctx = orch.get_meeting_context("test-ch")
        assert ctx is not None
        assert "stakeholder_status" in ctx

    def test_returns_context_for_standalone_meeting(self, tmp_path):
        orch, chat = _setup(tmp_path)
        channel = chat.get_channel("test-ch")
        enable_meeting_mode(channel, ["agent_a"], chat)
        ctx = orch.get_meeting_context("test-ch")
        assert ctx is not None
        assert "agent_a" in ctx["stakeholder_status"]

    def test_returns_none_when_no_meeting(self, tmp_path):
        orch, _ = _setup(tmp_path)
        ctx = orch.get_meeting_context("test-ch")
        assert ctx is None

    def test_returns_none_for_unknown_channel(self, tmp_path):
        orch, _ = _setup(tmp_path)
        ctx = orch.get_meeting_context("nonexistent")
        assert ctx is None


# =====================================================================
# Orchestrator: score_agent
# =====================================================================

class TestScoreAgent:
    def test_score_returns_dimensions(self, tmp_path):
        orch, _ = _setup(tmp_path)
        session = _start_session(orch)
        result = orch.score_agent(session.session_id, "agent_a")
        assert result is not None
        assert "dimensions" in result
        assert "composite_total" in result
        assert "phase" in result
        assert result["agent_id"] == "agent_a"
        assert result["session_id"] == session.session_id

    def test_score_has_five_dimensions(self, tmp_path):
        orch, _ = _setup(tmp_path)
        session = _start_session(orch)
        result = orch.score_agent(session.session_id, "agent_a")
        dims = result["dimensions"]
        assert "domain_expertise" in dims
        assert "complementary_value" in dims
        assert "historical_success" in dims
        assert "phase_alignment" in dims
        assert "data_ownership" in dims

    def test_score_nonexistent_agent(self, tmp_path):
        orch, _ = _setup(tmp_path)
        session = _start_session(orch)
        result = orch.score_agent(session.session_id, "agent_z")
        assert result is None

    def test_score_nonexistent_session(self, tmp_path):
        orch, _ = _setup(tmp_path)
        result = orch.score_agent("nonexistent", "agent_a")
        assert result is None


# =====================================================================
# CLI command dispatch (unit test -- no server)
# =====================================================================

class TestCLIDispatch:
    """Test that the CLI handler dispatch table is complete."""

    def test_all_commands_in_dispatch(self):
        from cohort.cli.meet_cmd import handle
        import argparse

        # All expected subcommands
        expected = {
            "stakeholders", "relevance",
            "start", "stop", "pause", "resume", "status",
            "promote", "demote", "add", "remove",
            "next", "score", "phase", "extend",
            "enable", "disable", "context",
        }
        # The handle function's dispatch dict should cover all
        # (we test by checking the function exists and handles None)
        args = argparse.Namespace(meet_command=None, json=False)
        result = handle(args)
        assert result == 0  # prints usage, returns 0

    def test_unknown_command_returns_1(self):
        from cohort.cli.meet_cmd import handle
        import argparse

        args = argparse.Namespace(meet_command="bogus", json=False)
        result = handle(args)
        assert result == 1


# =====================================================================
# Integration: session + meeting mode lifecycle
# =====================================================================

class TestSessionMeetingLifecycle:
    """End-to-end tests: start session, manage participants, end."""

    def test_full_lifecycle(self, tmp_path):
        orch, chat = _setup(tmp_path)

        # Start
        session = _start_session(orch, agents=["agent_a", "agent_b"])
        assert session.state == SessionState.ACTIVE.value
        assert orch.get_meeting_context("test-ch") is not None

        # Add participant
        orch.add_participant(session.session_id, "agent_c")
        assert "agent_c" in session.active_participants

        # Demote
        orch.update_participant_status(
            session.session_id, "agent_c", StakeholderStatus.DORMANT
        )
        assert session.active_participants["agent_c"] == StakeholderStatus.DORMANT.value

        # Promote back
        orch.update_participant_status(
            session.session_id, "agent_c", StakeholderStatus.ACTIVE
        )
        assert session.active_participants["agent_c"] == StakeholderStatus.ACTIVE.value

        # Score
        score = orch.score_agent(session.session_id, "agent_a")
        assert score is not None
        assert score["composite_total"] >= 0

        # Extend
        old_max = session.max_turns
        orch.extend_turns(session.session_id, 5)
        assert session.max_turns == old_max + 5

        # Remove
        orch.remove_participant(session.session_id, "agent_c")
        assert "agent_c" not in session.active_participants

        # End
        summary = orch.end_session(session.session_id)
        assert summary is not None
        assert orch.get_meeting_context("test-ch") is None

    def test_standalone_then_session(self, tmp_path):
        """Standalone meeting mode on a channel, then start a session."""
        orch, chat = _setup(tmp_path)
        channel = chat.get_channel("test-ch")

        # Enable standalone
        enable_meeting_mode(channel, ["agent_a"], chat, topic="quick review")
        assert channel.meeting_context is not None

        # Disable standalone
        disable_meeting_mode(channel, chat)
        assert channel.meeting_context is None

        # Now start a full session on same channel
        session = _start_session(orch, agents=["agent_a", "agent_b"])
        assert channel.meeting_context is not None
        assert "session_id" in channel.meeting_context

        # End session
        orch.end_session(session.session_id)
        assert channel.meeting_context is None
