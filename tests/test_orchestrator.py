"""Tests for cohort.orchestrator -- session management and proactive synthesis."""

import pytest

from cohort.chat import ChatManager, Message
from cohort.orchestrator import Orchestrator, SessionState, TurnMode
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
        "agent_a": {"triggers": ["python"], "capabilities": ["backend"]},
        "agent_b": {"triggers": ["security"], "capabilities": ["audit"]},
        "agent_c": {"triggers": ["test"], "capabilities": ["qa"]},
    }
    orch = Orchestrator(chat, agents=agent_configs)
    return orch, chat


def _post_and_record(orch, chat, session, sender, content):
    """Post a message and record the turn."""
    msg = chat.post_message(
        channel_id=session.channel_id,
        sender=sender,
        content=content,
    )
    orch.record_turn(session.session_id, sender, msg.id)
    return msg


# =====================================================================
# Basic session lifecycle
# =====================================================================

class TestSessionLifecycle:
    def test_start_and_end(self, tmp_path):
        orch, chat = _setup(tmp_path)
        session = orch.start_session(
            "test-ch", "Test topic",
            initial_agents=["agent_a", "agent_b"],
        )
        assert session.state == SessionState.ACTIVE.value
        summary = orch.end_session(session.session_id)
        assert summary is not None
        assert summary["topic"] == "Test topic"

    def test_pause_and_resume(self, tmp_path):
        orch, chat = _setup(tmp_path)
        session = orch.start_session(
            "test-ch", "Test",
            initial_agents=["agent_a"],
        )
        assert orch.pause_session(session.session_id) is True
        assert orch.sessions[session.session_id].state == SessionState.PAUSED.value
        assert orch.resume_session(session.session_id) is True
        assert orch.sessions[session.session_id].state == SessionState.ACTIVE.value

    def test_record_turn(self, tmp_path):
        orch, chat = _setup(tmp_path)
        session = orch.start_session(
            "test-ch", "Test",
            initial_agents=["agent_a"],
        )
        msg = chat.post_message("test-ch", "agent_a", "Hello world")
        assert orch.record_turn(session.session_id, "agent_a", msg.id) is True
        assert session.current_turn == 1
        assert "agent_a" in session.participants_contributed


# =====================================================================
# Proactive synthesis detection
# =====================================================================

class TestSynthesisDetection:
    def test_no_synthesis_on_fresh_session(self, tmp_path):
        orch, chat = _setup(tmp_path)
        session = orch.start_session(
            "test-ch", "Test",
            initial_agents=["agent_a", "agent_b", "agent_c"],
        )
        assert orch.detect_synthesis_opportunity(session.session_id) is None

    def test_contradiction_detected(self, tmp_path):
        """Post messages directly (without record_turn) to test detection in isolation."""
        orch, chat = _setup(tmp_path)
        session = orch.start_session(
            "test-ch", "Python architecture",
            initial_agents=["agent_a", "agent_b", "agent_c"],
        )
        # Post messages directly to avoid record_turn triggering synthesis
        chat.post_message("test-ch", "agent_a",
                         "I think we should use Django for the backend framework")
        chat.post_message("test-ch", "agent_b",
                         "I disagree, however FastAPI would be better for this backend")
        chat.post_message("test-ch", "agent_c",
                         "Actually I disagree with the Django approach, instead use Flask for backend")
        session.current_turn = 3

        result = orch.detect_synthesis_opportunity(session.session_id)
        assert result is not None
        assert result["type"] == "contradiction"

    def test_consensus_detected(self, tmp_path):
        orch, chat = _setup(tmp_path)
        session = orch.start_session(
            "test-ch", "Testing strategy",
            initial_agents=["agent_a", "agent_b", "agent_c"],
        )
        chat.post_message("test-ch", "agent_a",
                         "I agree we should use pytest for testing, good point about coverage")
        chat.post_message("test-ch", "agent_b",
                         "Yes exactly, pytest testing with coverage is correct approach")
        chat.post_message("test-ch", "agent_c",
                         "Absolutely agree, pytest testing coverage is the right call indeed")
        session.current_turn = 3

        result = orch.detect_synthesis_opportunity(session.session_id)
        assert result is not None
        assert result["type"] == "consensus"

    def test_stall_detected(self, tmp_path):
        orch, chat = _setup(tmp_path)
        session = orch.start_session(
            "test-ch", "Architecture review",
            initial_agents=["agent_a", "agent_b", "agent_c"],
        )
        # Post varied early messages
        chat.post_message("test-ch", "agent_a",
                         "The architecture needs microservice decomposition patterns")
        chat.post_message("test-ch", "agent_b",
                         "We should evaluate the database schema migration strategy")
        # Now repeat similar content
        chat.post_message("test-ch", "agent_a",
                         "The architecture needs microservice decomposition patterns again")
        chat.post_message("test-ch", "agent_b",
                         "The architecture microservice decomposition patterns are important")
        chat.post_message("test-ch", "agent_c",
                         "Architecture microservice decomposition patterns need review")
        session.current_turn = 5

        result = orch.detect_synthesis_opportunity(session.session_id)
        assert result is not None
        assert result["type"] == "stall"

    def test_synthesis_triggered_by_record_turn(self, tmp_path):
        """Verify that record_turn auto-triggers synthesis when conditions are met."""
        orch, chat = _setup(tmp_path)
        events = []
        orch._on_event = lambda name, data: events.append((name, data))

        session = orch.start_session(
            "test-ch", "Python architecture",
            initial_agents=["agent_a", "agent_b", "agent_c"],
        )
        # Post enough disagreeing messages via record_turn
        _post_and_record(orch, chat, session, "agent_a",
                         "I think we should use Django for the backend framework")
        _post_and_record(orch, chat, session, "agent_b",
                         "I disagree, however FastAPI would be better for this backend")
        _post_and_record(orch, chat, session, "agent_c",
                         "Actually I disagree with the Django approach, instead use Flask for backend")

        # Check that synthesis_triggered event was emitted
        synthesis_events = [e for e in events if e[0] == "synthesis_triggered"]
        assert len(synthesis_events) >= 1

    def test_synthesis_cooldown(self, tmp_path):
        orch, chat = _setup(tmp_path)
        session = orch.start_session(
            "test-ch", "Testing strategy",
            initial_agents=["agent_a", "agent_b", "agent_c"],
        )
        # Set cooldown as if synthesis just triggered
        if not hasattr(orch, "_last_synthesis_turn"):
            orch._last_synthesis_turn = {}
        session.current_turn = 5
        orch._last_synthesis_turn[session.session_id] = 4  # just triggered

        # Should be rate-limited regardless of content
        result = orch.detect_synthesis_opportunity(session.session_id)
        assert result is None


# =====================================================================
# Synthesis message building
# =====================================================================

class TestSynthesisMessages:
    def test_contradiction_message(self, tmp_path):
        orch, chat = _setup(tmp_path)
        session = orch.start_session("test-ch", "Test", initial_agents=["agent_a"])
        msg = orch._build_synthesis_message(
            {"type": "contradiction", "agents": ["agent_a", "agent_b"]},
            session,
        )
        assert "[SYNTHESIS]" in msg
        assert "@agent_a" in msg

    def test_consensus_message(self, tmp_path):
        orch, chat = _setup(tmp_path)
        session = orch.start_session("test-ch", "Test", initial_agents=["agent_a"])
        msg = orch._build_synthesis_message(
            {"type": "consensus", "agents": ["agent_a"]},
            session,
        )
        assert "consensus" in msg.lower()

    def test_stall_message(self, tmp_path):
        orch, chat = _setup(tmp_path)
        session = orch.start_session(
            "test-ch", "Test",
            initial_agents=["agent_a", "agent_b"],
        )
        session.last_speakers = ["agent_a"]
        msg = orch._build_synthesis_message(
            {"type": "stall", "reason": "low novelty"},
            session,
        )
        assert "[SYNTHESIS]" in msg

    def test_divergence_message(self, tmp_path):
        orch, chat = _setup(tmp_path)
        session = orch.start_session("test-ch", "Test", initial_agents=["agent_a"])
        msg = orch._build_synthesis_message(
            {"type": "divergence", "original_keywords": ["python", "api"]},
            session,
        )
        assert "python" in msg.lower() or "refocus" in msg.lower()
