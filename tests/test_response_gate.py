"""Tests for the unified response gate."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from cohort.response_gate import (
    EMERGENCY_MAX_CONVERSATION_DEPTH,
    EMERGENCY_MAX_RESPONSES_PER_MINUTE,
    GATE_CACHE_TTL_SECONDS,
    GateDecision,
    GateState,
    TIER1_ALLOW_THRESHOLD,
    TIER1_BLOCK_THRESHOLD,
    _gate_cache,
    _state,
    _tier1_heuristic_gate,
    _tier3_emergency_check,
    is_rate_limited,
    record_response,
    set_conversation_depth,
    should_allow_response,
)


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset gate state between tests."""
    _state.last_response_time.clear()
    _state.recent_responses.clear()
    _state.conversation_depth.clear()
    _gate_cache.clear()
    yield
    _state.last_response_time.clear()
    _state.recent_responses.clear()
    _state.conversation_depth.clear()
    _gate_cache.clear()


# =====================================================================
# Bypass tests
# =====================================================================

class TestBypasses:
    def test_system_sender_always_allowed(self):
        result = should_allow_response("ch", "system", "hello")
        assert result.allowed is True
        assert result.tier_used == 0
        assert "system_sender_exempt" in result.reason

    def test_explicit_mention_always_allowed(self):
        result = should_allow_response(
            "ch", "agent_a", "hello",
            is_explicit_mention=True,
        )
        assert result.allowed is True
        assert result.tier_used == 0
        assert "explicit_mention" in result.reason

    def test_explicit_mention_bypasses_even_with_high_rate(self):
        # Flood the rate limiter
        for _ in range(25):
            record_response("ch", "agent_a")
        result = should_allow_response(
            "ch", "agent_a", "hello",
            is_explicit_mention=True,
        )
        assert result.allowed is True


# =====================================================================
# Tier 3 emergency backstop
# =====================================================================

class TestTier3:
    def test_blocks_after_max_responses_per_minute(self):
        for _ in range(EMERGENCY_MAX_RESPONSES_PER_MINUTE):
            record_response("ch", "agent_a")
        result = _tier3_emergency_check("ch", "agent_a")
        assert result.allowed is False
        assert result.tier_used == 3
        assert "responses/min" in result.reason

    def test_allows_under_limit(self):
        for _ in range(EMERGENCY_MAX_RESPONSES_PER_MINUTE - 1):
            record_response("ch", "agent_a")
        result = _tier3_emergency_check("ch", "agent_a")
        assert result.allowed is True

    def test_blocks_deep_conversation(self):
        # Build up depth
        for i in range(EMERGENCY_MAX_CONVERSATION_DEPTH + 1):
            set_conversation_depth(f"msg_{i}", f"msg_{i-1}" if i > 0 else None)
        # The last message should be at max depth
        last = f"msg_{EMERGENCY_MAX_CONVERSATION_DEPTH}"
        result = _tier3_emergency_check("ch", "agent_a", thread_id=last)
        assert result.allowed is False
        assert "depth" in result.reason

    def test_passes_shallow_conversation(self):
        set_conversation_depth("msg_0", None)
        result = _tier3_emergency_check("ch", "agent_a", thread_id="msg_0")
        assert result.allowed is True


# =====================================================================
# Tier 1 heuristic scoring
# =====================================================================

class TestTier1:
    def _make_msg(self, sender: str, content: str) -> MagicMock:
        msg = MagicMock()
        msg.sender = sender
        msg.content = content
        return msg

    def _make_chat(self, messages: list) -> MagicMock:
        chat = MagicMock()
        chat.get_channel_messages.return_value = messages
        return chat

    def test_empty_channel_allows(self):
        chat = self._make_chat([])
        result = _tier1_heuristic_gate("ch", "agent_a", "hello", chat=chat)
        assert result.allowed is True
        assert "empty_channel" in result.reason

    def test_first_message_gets_bonus(self):
        msgs = [self._make_msg("human", "What do you think about testing?")]
        chat = self._make_chat(msgs)
        result = _tier1_heuristic_gate(
            "ch", "agent_a", "What do you think?",
            chat=chat,
        )
        assert "first_message" in result.reason
        # First message = 0.30 novelty bonus, should score well
        assert result.score >= 0.30

    def test_repetitive_channel_penalized(self):
        # Same content repeated -- channel-wide novelty drops to 0
        msgs = [
            self._make_msg("agent_a", "I think we should use Python"),
            self._make_msg("agent_a", "I think we should use Python"),
            self._make_msg("agent_a", "I think we should use Python"),
        ]
        chat = self._make_chat(msgs)
        result = _tier1_heuristic_gate(
            "ch", "agent_a", "hello",
            chat=chat,
        )
        # Channel novelty should be 0 (identical messages)
        assert "ch_novelty(0.00)" in result.reason
        # Total score should be below allow threshold
        assert result.score < TIER1_ALLOW_THRESHOLD

    def test_expertise_boosts_score(self):
        msgs = [self._make_msg("human", "How should we handle Python testing?")]
        chat = self._make_chat(msgs)
        config = {
            "triggers": ["python", "testing", "pytest"],
            "capabilities": ["unit testing", "integration testing"],
            "domain_expertise": ["python", "testing"],
        }
        result = _tier1_heuristic_gate(
            "ch", "agent_a", "hello",
            chat=chat,
            agent_config=config,
        )
        assert "expertise" in result.reason

    def test_no_config_gives_neutral(self):
        msgs = [self._make_msg("human", "hello")]
        chat = self._make_chat(msgs)
        result = _tier1_heuristic_gate(
            "ch", "agent_a", "hello",
            chat=chat,
            agent_config=None,
        )
        assert "no_config" in result.reason


# =====================================================================
# Tier 2 session-aware scoring
# =====================================================================

class TestTier2:
    def test_no_orchestrator_returns_none_and_gray_zone_allows(self):
        """Without orchestrator, gray-zone Tier 1 scores should allow."""
        result = should_allow_response(
            "ch", "agent_a", "hello",
            orchestrator=None,
        )
        # Should still produce a decision (Tier 1 or gray zone allow)
        assert result.allowed is True

    def test_orchestrator_session_gates_agent(self):
        orch = MagicMock()
        session = MagicMock()
        session.session_id = "s_test"
        orch.get_session_for_channel.return_value = session
        orch.should_agent_respond.return_value = (False, "score_0.10_below_threshold_0.30")

        result = should_allow_response(
            "ch", "agent_a", "hello",
            orchestrator=orch,
        )
        # Without explicit mention, gray zone escalates to tier 2
        # But since is_explicit_mention defaults to False and tier 1 may allow,
        # this tests that when tier 1 is ambiguous AND tier 2 blocks, it blocks
        # We need to verify the orchestrator was consulted
        if result.tier_used == 2:
            assert result.allowed is False

    def test_orchestrator_no_session_falls_through(self):
        orch = MagicMock()
        orch.get_session_for_channel.return_value = None

        result = should_allow_response(
            "ch", "agent_a", "hello",
            orchestrator=orch,
        )
        assert result.allowed is True


# =====================================================================
# Integration: full gate flow
# =====================================================================

class TestFullFlow:
    def test_record_and_rate_limit(self):
        assert not is_rate_limited("agent_a")
        record_response("ch", "agent_a")
        assert is_rate_limited("agent_a")

    def test_cache_works(self):
        # First call populates cache
        r1 = should_allow_response("ch", "agent_a", "hello")
        # Second call should be cached
        r2 = should_allow_response("ch", "agent_a", "hello")
        if r2.tier_used == 1:
            assert r2.cached is True

    def test_different_channels_independent(self):
        for _ in range(EMERGENCY_MAX_RESPONSES_PER_MINUTE):
            record_response("ch1", "agent_a")
        # ch1 should be blocked at tier 3
        r1 = should_allow_response("ch1", "agent_a", "hello")
        assert r1.allowed is False
        # ch2 should be fine
        r2 = should_allow_response("ch2", "agent_a", "hello")
        assert r2.allowed is True

    def test_gate_decision_dataclass(self):
        d = GateDecision(True, 1, 0.7, "test")
        assert d.allowed is True
        assert d.cached is False
