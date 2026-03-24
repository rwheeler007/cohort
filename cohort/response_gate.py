"""Unified response gate for cohort agent routing.

Replaces the two-layer system (blunt rate limiter + session-only scoring)
with a single 3-tier gate that runs on every message, session or not.

Architecture (inspired by BOSS's should_allow_response):

    Tier 3: Emergency backstop    -- hard rate limits, always checked first
    Tier 1: Heuristic scoring     -- fast (<10ms), reuses meeting.py leaf functions
    Tier 2: Session-aware scoring -- deeper scoring when orchestrator session is active

The gate is fail-open: any error at any tier defaults to ALLOW.

Explicit @mentions always bypass gating entirely.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# =====================================================================
# Configuration
# =====================================================================

# Tier 1 heuristic thresholds
TIER1_ALLOW_THRESHOLD = 0.55   # Above: allow immediately
TIER1_BLOCK_THRESHOLD = 0.15   # Below: block immediately
# Between: escalate to Tier 2 (session scoring) if available, else allow

# Tier 3 emergency backstop
EMERGENCY_MAX_RESPONSES_PER_MINUTE = 20
EMERGENCY_MAX_CONVERSATION_DEPTH = 15
EMERGENCY_RATE_LIMIT_SECONDS = 3

# Senders exempt from all gating
SYSTEM_SENDERS = frozenset({
    "system", "human", "user", "admin",
})

# Gate decision cache: (channel_id, agent_id) -> (GateDecision, timestamp)
_gate_cache: dict[tuple[str, str], tuple[GateDecision, float]] = {}
GATE_CACHE_TTL_SECONDS = 30


# =====================================================================
# Data structures
# =====================================================================

@dataclass
class GateDecision:
    """Result of the response gate."""

    allowed: bool
    tier_used: int       # 0=bypass, 1=heuristic, 2=session, 3=backstop
    score: float
    reason: str
    cached: bool = False


@dataclass
class GateState:
    """Mutable singleton for gate tracking data."""

    # Rate limiting: agent_id -> epoch timestamp
    last_response_time: dict[str, float] = field(default_factory=dict)

    # Loop detection: (channel_id, agent_id) -> [epoch timestamps]
    recent_responses: dict[tuple[str, str], list[float]] = field(default_factory=dict)

    # Conversation depth: message_id -> depth
    conversation_depth: dict[str, int] = field(default_factory=dict)


_state = GateState()


# =====================================================================
# Public API
# =====================================================================

def should_allow_response(
    channel_id: str,
    agent_id: str,
    message_content: str,
    *,
    is_explicit_mention: bool = False,
    thread_id: str | None = None,
    chat: Any | None = None,
    agent_config: dict[str, Any] | None = None,
    orchestrator: Any | None = None,
) -> GateDecision:
    """Unified response gate.  Single entry point for all gating decisions.

    Parameters
    ----------
    channel_id:
        Channel the response would be posted to.
    agent_id:
        Agent that wants to respond.
    message_content:
        The message the agent is responding to.
    is_explicit_mention:
        True if the agent was explicitly @mentioned in the message.
    thread_id:
        Thread/message ID for depth tracking.
    chat:
        Optional ChatManager instance for reading recent messages.
    agent_config:
        Agent config dict (triggers, capabilities, domain_expertise).
        If None, Tier 1 expertise scoring is skipped (still scores other dims).
    orchestrator:
        Optional Orchestrator instance.  If provided and a session is active
        on the channel, Tier 2 session-aware scoring is used for gray-zone
        decisions instead of defaulting to allow.
    """
    # --- Bypass: system senders always allowed ---
    if agent_id.lower() in SYSTEM_SENDERS:
        return GateDecision(True, 0, 1.0, "system_sender_exempt")

    # --- Bypass: explicit @mentions always allowed ---
    if is_explicit_mention:
        return GateDecision(True, 0, 1.0, "explicit_mention")

    # --- Tier 3 first: emergency backstop ---
    tier3 = _tier3_emergency_check(channel_id, agent_id, thread_id)
    if not tier3.allowed:
        _log_decision(channel_id, agent_id, tier3)
        return tier3

    # --- Check cache ---
    cached = _check_cache(channel_id, agent_id)
    if cached is not None:
        cached.cached = True
        _log_decision(channel_id, agent_id, cached)
        return cached

    # --- Tier 1: fast heuristic ---
    tier1 = _tier1_heuristic_gate(
        channel_id, agent_id, message_content,
        chat=chat, agent_config=agent_config,
    )

    if tier1.score >= TIER1_ALLOW_THRESHOLD:
        tier1.allowed = True
        _cache_decision(channel_id, agent_id, tier1)
        _log_decision(channel_id, agent_id, tier1)
        return tier1

    if tier1.score <= TIER1_BLOCK_THRESHOLD:
        tier1.allowed = False
        _cache_decision(channel_id, agent_id, tier1)
        _log_decision(channel_id, agent_id, tier1)
        return tier1

    # --- Tier 2: session-aware scoring (gray zone) ---
    tier2 = _tier2_session_gate(
        channel_id, agent_id, message_content,
        chat=chat, orchestrator=orchestrator,
    )
    if tier2 is not None:
        _log_decision(channel_id, agent_id, tier2)
        return tier2

    # No session available, gray zone defaults to allow
    tier1.allowed = True
    tier1.reason += " (gray_zone_allow)"
    _log_decision(channel_id, agent_id, tier1)
    return tier1


def record_response(channel_id: str, agent_id: str) -> None:
    """Record that an agent responded (for rate limiting and loop detection)."""
    now = time.time()
    _state.last_response_time[agent_id] = now

    key = (channel_id, agent_id)
    if key not in _state.recent_responses:
        _state.recent_responses[key] = []
    _state.recent_responses[key].append(now)


def get_conversation_depth(message_id: str) -> int:
    """Get conversation depth for a message thread."""
    return _state.conversation_depth.get(message_id, 0)


def set_conversation_depth(message_id: str, parent_id: str | None) -> None:
    """Set conversation depth based on parent."""
    if parent_id and parent_id in _state.conversation_depth:
        _state.conversation_depth[message_id] = _state.conversation_depth[parent_id] + 1
    else:
        _state.conversation_depth[message_id] = 1


def is_rate_limited(agent_id: str) -> bool:
    """Check if agent is within the minimum inter-response cooldown."""
    if agent_id in _state.last_response_time:
        elapsed = time.time() - _state.last_response_time[agent_id]
        return elapsed < EMERGENCY_RATE_LIMIT_SECONDS
    return False


# =====================================================================
# Tier 3: Emergency backstop
# =====================================================================

def _tier3_emergency_check(
    channel_id: str,
    agent_id: str,
    thread_id: str | None = None,
) -> GateDecision:
    """Hard limits that prevent runaway loops.  Always checked first."""
    now = time.time()

    # Check responses-per-minute
    key = (channel_id, agent_id)
    if key in _state.recent_responses:
        _state.recent_responses[key] = [
            ts for ts in _state.recent_responses[key] if now - ts < 60
        ]
        count = len(_state.recent_responses[key])
        if count >= EMERGENCY_MAX_RESPONSES_PER_MINUTE:
            return GateDecision(
                False, 3, 0.0,
                f"tier3: {count} responses/min (limit {EMERGENCY_MAX_RESPONSES_PER_MINUTE})",
            )

    # Check conversation depth
    if thread_id:
        depth = _state.conversation_depth.get(thread_id, 0)
        if depth >= EMERGENCY_MAX_CONVERSATION_DEPTH:
            return GateDecision(
                False, 3, 0.0,
                f"tier3: depth {depth} (limit {EMERGENCY_MAX_CONVERSATION_DEPTH})",
            )

    return GateDecision(True, 3, 1.0, "tier3: pass")


# =====================================================================
# Tier 1: Heuristic scoring (no LLM, <10ms)
# =====================================================================

def _tier1_heuristic_gate(
    channel_id: str,
    agent_id: str,
    message_content: str,
    *,
    chat: Any | None = None,
    agent_config: dict[str, Any] | None = None,
) -> GateDecision:
    """Fast heuristic scoring from channel message history.

    Reuses leaf functions from meeting.py.

    Dimensions:
        1. Direct question detection  (0.30)
        2. Agent self-repetition       (0.30)
        3. Channel-wide novelty        (0.20)
        4. Expertise relevance         (0.20)
    """
    start = time.time()

    try:
        from cohort.meeting import (
            extract_keywords,
            calculate_novelty,
            is_directly_questioned,
            calculate_expertise_relevance,
        )
        from cohort.chat import Message

        recent_messages = _get_recent_messages(channel_id, limit=8, chat=chat)

        if not recent_messages:
            return GateDecision(True, 1, 1.0, "tier1: empty_channel")

        score = 0.0
        reasons: list[str] = []

        # 1. Direct question detection (weight: 0.30)
        if is_directly_questioned(agent_id, recent_messages):
            score += 0.30
            reasons.append("questioned")

        # 2. Agent self-repetition (weight: 0.30)
        agent_msgs = [m for m in recent_messages if m.sender == agent_id]
        if agent_msgs:
            novelty = calculate_novelty("[considering response]", agent_msgs[-3:])
            score += 0.30 * novelty
            if novelty < 0.2:
                reasons.append(f"self_repeat({novelty:.2f})")
            else:
                reasons.append(f"novel({novelty:.2f})")
        else:
            # Agent hasn't spoken yet -- high novelty
            score += 0.30
            reasons.append("first_message")

        # 3. Channel-wide novelty (weight: 0.20)
        if len(recent_messages) >= 3:
            last_msg = recent_messages[-1]
            channel_novelty = calculate_novelty(
                last_msg.content, recent_messages[-4:-1],
            )
            score += 0.20 * channel_novelty
            reasons.append(f"ch_novelty({channel_novelty:.2f})")
        else:
            score += 0.20

        # 4. Expertise relevance (weight: 0.20)
        if agent_config:
            topic_keywords: list[str] = []
            for msg in recent_messages[-3:]:
                topic_keywords.extend(extract_keywords(msg.content))
            relevance = calculate_expertise_relevance(agent_config, topic_keywords)
            score += 0.20 * relevance
            reasons.append(f"expertise({relevance:.2f})")
        else:
            # No config available -- neutral score (don't penalize)
            score += 0.10
            reasons.append("no_config")

        elapsed_ms = (time.time() - start) * 1000
        reason_str = f"tier1: {', '.join(reasons)} ({elapsed_ms:.0f}ms)"

        return GateDecision(
            allowed=(score >= TIER1_ALLOW_THRESHOLD),
            tier_used=1,
            score=score,
            reason=reason_str,
        )

    except Exception as e:
        # Fail-open on any error
        logger.warning("Tier 1 gate error for %s: %s", agent_id, e)
        return GateDecision(True, 1, 1.0, f"tier1_error: {e}")


# =====================================================================
# Tier 2: Session-aware scoring
# =====================================================================

def _tier2_session_gate(
    channel_id: str,
    agent_id: str,
    message_content: str,
    *,
    chat: Any | None = None,
    orchestrator: Any | None = None,
) -> GateDecision | None:
    """Session-aware scoring via the orchestrator.

    Only fires when an orchestrator session is active on the channel.
    Returns None if no session is available (caller decides default).
    """
    if orchestrator is None:
        return None

    try:
        session = orchestrator.get_session_for_channel(channel_id)
        if session is None:
            return None

        should_respond, reason = orchestrator.should_agent_respond(
            session.session_id, agent_id, message_content,
        )

        return GateDecision(
            allowed=should_respond,
            tier_used=2,
            score=1.0 if should_respond else 0.0,
            reason=f"tier2: {reason}",
        )

    except Exception as e:
        logger.warning("Tier 2 gate error for %s: %s", agent_id, e)
        # Fail-open
        return None


# =====================================================================
# Helpers
# =====================================================================

def _get_recent_messages(
    channel_id: str,
    limit: int = 8,
    chat: Any | None = None,
) -> list[Any]:
    """Fetch recent messages from a channel via ChatManager."""
    if chat is None:
        return []
    try:
        return chat.get_channel_messages(channel_id, limit=limit)
    except Exception:
        return []


def _check_cache(
    channel_id: str,
    agent_id: str,
) -> GateDecision | None:
    """Return cached Tier 1 decision if still valid."""
    key = (channel_id, agent_id)
    if key in _gate_cache:
        decision, ts = _gate_cache[key]
        if time.time() - ts < GATE_CACHE_TTL_SECONDS:
            return GateDecision(
                allowed=decision.allowed,
                tier_used=decision.tier_used,
                score=decision.score,
                reason=f"cached: {decision.reason}",
            )
        else:
            del _gate_cache[key]
    return None


def _cache_decision(
    channel_id: str,
    agent_id: str,
    decision: GateDecision,
) -> None:
    """Cache a Tier 1 gate decision (Tier 2 decisions are NOT cached)."""
    if decision.tier_used == 1:
        _gate_cache[(channel_id, agent_id)] = (decision, time.time())


def _log_decision(
    channel_id: str,
    agent_id: str,
    decision: GateDecision,
) -> None:
    """Log gate decisions for debugging."""
    level = logging.DEBUG if decision.allowed else logging.INFO
    logger.log(
        level,
        "Gate %s %s in #%s: tier=%d score=%.2f %s%s",
        "ALLOW" if decision.allowed else "BLOCK",
        agent_id,
        channel_id,
        decision.tier_used,
        decision.score,
        decision.reason,
        " (cached)" if decision.cached else "",
    )
