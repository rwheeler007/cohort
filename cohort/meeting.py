"""Stakeholder gating and contribution scoring for cohort.

Prevents conversational loops by gating agent responses based on
contribution value.  Agents can approve and step back after contributing,
and the system detects topic shifts to re-engage relevant expertise.

Key concepts:

- **Stakeholder status**: active, approved_silent, observer, dormant
- **Contribution score**: 0.0--1.0 based on novelty, expertise, ownership, questions
- **Gating**: speak only if score > threshold for current status
- **Topic shifts**: re-evaluate stakeholder relevance when discussion changes
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any

from cohort.chat import Channel, ChatManager, Message

logger = logging.getLogger(__name__)


# =====================================================================
# Enums
# =====================================================================

class StakeholderStatus(Enum):
    """Agent participation status in meeting mode."""

    ACTIVE = "active_stakeholder"
    APPROVED_SILENT = "approved_silent"
    OBSERVER = "observer"
    DORMANT = "dormant"


# =====================================================================
# Configuration constants
# =====================================================================

STAKEHOLDER_THRESHOLDS: dict[str, float] = {
    StakeholderStatus.ACTIVE.value: 0.3,
    StakeholderStatus.APPROVED_SILENT.value: 0.7,
    StakeholderStatus.OBSERVER.value: 0.8,
    StakeholderStatus.DORMANT.value: 1.0,
}

SCORING_WEIGHTS: dict[str, float] = {
    "novelty": 0.35,
    "expertise": 0.30,
    "ownership": 0.20,
    "question": 0.15,
}

RELEVANCE_DIMENSIONS: dict[str, float] = {
    "domain_expertise": 0.30,
    "complementary_value": 0.25,
    "historical_success": 0.20,
    "phase_alignment": 0.15,
    "data_ownership": 0.10,
}

COMPLEMENTARY_PAIRS: dict[str, list[str]] = {
    "javascript_developer": ["web_developer"],
    "web_developer": ["javascript_developer"],
    "python_developer": ["supervisor_agent", "qa_agent"],
    "supervisor_agent": ["python_developer"],
    "qa_agent": ["python_developer"],
    "cpp_developer": ["system_coder"],
    "system_coder": ["cpp_developer"],
}

PHASE_KEYWORDS: dict[str, list[str]] = {
    "DISCOVER": [
        "research", "investigate", "find", "search", "past",
        "history", "bug_fixes", "similar", "existing",
    ],
    "PLAN": [
        "design", "architecture", "approach", "strategy",
        "outline", "plan", "structure",
    ],
    "EXECUTE": [
        "implement", "code", "write", "create", "build",
        "add", ".py", ".tsx", ".js", ".cpp", ".html",
    ],
    "VALIDATE": [
        "test", "review", "check", "verify", "quality",
        "validate", "compliance", "audit",
    ],
}

DATA_SOURCES: dict[str, list[str]] = {
    "supervisor_agent": [
        "memory.json", "session_metrics", "monitoring_data", "compliance_reports",
    ],
    "qa_agent": ["test_results", "past_issues", "root_causes"],
    "code_archaeologist": [
        "validation_confidence", "quality_patterns", "success_rates",
    ],
    "documentation_agent": ["changelogs", "learnings", "release_notes"],
}

TOPIC_SHIFT_THRESHOLD: float = 0.3
TOPIC_LOOKBACK_MESSAGES: int = 5


# =====================================================================
# Keyword utilities
# =====================================================================

_STOP_WORDS: frozenset[str] = frozenset(
    "the a an is are was were in on at to for of and or but with from by "
    "this that these those be have has had do does did will would should "
    "could can may might must i you he she it we they my your his her its "
    "our their me him us them".split()
)


def extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from *text*.

    Filters out common stop words and short words.
    """
    words = re.findall(r"\b[a-z0-9_]+\b", text.lower())
    return [w for w in words if w not in _STOP_WORDS and len(w) > 3]


def calculate_keyword_overlap(
    keywords1: list[str], keywords2: list[str]
) -> float:
    """Jaccard similarity between two keyword lists."""
    set1, set2 = set(keywords1), set(keywords2)
    if not set1 or not set2:
        return 0.0
    union = len(set1 | set2)
    return len(set1 & set2) / union if union else 0.0


# =====================================================================
# Meeting context
# =====================================================================

def initialize_meeting_context(
    initial_agents: list[str] | None = None,
) -> dict[str, Any]:
    """Create a fresh meeting context dict."""
    context: dict[str, Any] = {
        "stakeholder_status": {},
        "current_topic": {"keywords": [], "primary_stakeholders": []},
    }
    if initial_agents:
        for agent_id in initial_agents:
            context["stakeholder_status"][agent_id] = StakeholderStatus.ACTIVE.value
            context["current_topic"]["primary_stakeholders"].append(agent_id)
    return context


# =====================================================================
# Scoring functions
# =====================================================================

def calculate_novelty(
    proposed_message: str, recent_messages: list[Message]
) -> float:
    """How novel *proposed_message* is compared to *recent_messages*.

    Returns 1.0 for completely novel, 0.0 for exact duplicate.
    """
    if not recent_messages:
        return 1.0
    proposed_keywords = set(extract_keywords(proposed_message))
    if not proposed_keywords:
        return 0.0
    max_overlap = 0.0
    for msg in recent_messages:
        existing = set(extract_keywords(msg.content))
        if not existing:
            continue
        overlap = len(proposed_keywords & existing) / len(proposed_keywords)
        max_overlap = max(max_overlap, overlap)
    return 1.0 - max_overlap


def calculate_expertise_relevance(
    agent_config: dict[str, Any], topic_keywords: list[str]
) -> float:
    """Match topic keywords to agent triggers/capabilities/domain_expertise."""
    triggers = agent_config.get("triggers", [])
    capabilities = agent_config.get("capabilities", [])
    domain_expertise = agent_config.get("domain_expertise", [])
    agent_keywords: list[str] = []
    for trigger in triggers:
        agent_keywords.extend(extract_keywords(str(trigger)))
    for cap in capabilities:
        agent_keywords.extend(extract_keywords(str(cap)))
    for exp in domain_expertise:
        agent_keywords.extend(extract_keywords(str(exp)))
    if not agent_keywords:
        return 0.0
    return calculate_keyword_overlap(topic_keywords, agent_keywords)


def is_directly_questioned(
    agent_id: str, recent_messages: list[Message]
) -> bool:
    """Check if *agent_id* was directly asked a question recently."""
    mention = f"@{agent_id}"
    for msg in recent_messages[-3:]:
        if mention in msg.content and "?" in msg.content:
            return True
    return False


# =====================================================================
# Composite relevance matrix
# =====================================================================

def detect_current_phase(recent_messages: list[Message]) -> str:
    """Detect workflow phase (DISCOVER/PLAN/EXECUTE/VALIDATE)."""
    if not recent_messages:
        return "DISCOVER"
    all_kw: list[str] = []
    for msg in recent_messages[-5:]:
        all_kw.extend(extract_keywords(msg.content))
    phase_scores: dict[str, int] = {}
    for phase, keywords in PHASE_KEYWORDS.items():
        phase_scores[phase] = sum(1 for kw in all_kw if kw in keywords)
    if not any(phase_scores.values()):
        return "DISCOVER"
    return max(phase_scores.items(), key=lambda x: x[1])[0]


def calculate_complementary_value(
    agent_id: str, meeting_context: dict[str, Any]
) -> float:
    """Score based on complementary agent pairs that are active."""
    stakeholder_status = meeting_context.get("stakeholder_status", {})
    active = [
        sid
        for sid, status in stakeholder_status.items()
        if status == StakeholderStatus.ACTIVE.value
    ]
    complementary = COMPLEMENTARY_PAIRS.get(agent_id, [])
    if not complementary:
        return 0.0
    active_comp = [c for c in complementary if c in active]
    return len(active_comp) / len(complementary) if active_comp else 0.0


def calculate_historical_success(
    agent_id: str,
    topic_keywords: list[str],
    *,
    agent_profiles: dict[str, Any] | None = None,
) -> float:
    """Estimate past success on similar topics.

    If *agent_profiles* is provided, the caller can supply working
    memory entries.  Otherwise returns a neutral 0.5.
    """
    if not agent_profiles:
        return 0.5
    profile = agent_profiles.get(agent_id)
    if not profile:
        return 0.5
    working_memory = getattr(profile, "working_memory", None) or []
    if not working_memory:
        return 0.5
    similar = 0
    topic_set = set(topic_keywords)
    for entry in working_memory:
        entry_text = str(entry.get("input", "")) + str(entry.get("response", ""))
        if len(set(extract_keywords(entry_text)) & topic_set) > 2:
            similar += 1
    return similar / len(working_memory) if working_memory else 0.5


def calculate_phase_alignment(
    agent_id: str, current_phase: str, agent_config: dict[str, Any]
) -> float:
    """How relevant is the agent for the current workflow phase."""
    phase_relevance: dict[str, dict[str, list[str]]] = {
        "DISCOVER": {
            "high": ["qa_agent", "supervisor_agent", "documentation_agent"],
            "medium": ["code_archaeologist"],
            "low": ["implementer", "developer"],
        },
        "PLAN": {
            "high": ["architect", "primary_developer"],
            "medium": ["designer", "technical_specialist"],
            "low": ["qa_agent", "reviewer"],
        },
        "EXECUTE": {
            "high": [
                "python_developer", "javascript_developer", "web_developer",
                "cpp_developer", "database_developer",
            ],
            "medium": ["system_coder"],
            "low": ["reviewer", "supervisor"],
        },
        "VALIDATE": {
            "high": ["code_archaeologist", "supervisor_agent", "reviewer"],
            "medium": ["tester", "qa_agent"],
            "low": ["implementer"],
        },
    }
    phase_map = phase_relevance.get(current_phase, {})
    for tier, agents in phase_map.items():
        for pattern in agents:
            if pattern in agent_id.lower():
                return {"high": 1.0, "medium": 0.6, "low": 0.2}[tier]
    return 0.5


def calculate_data_ownership(
    agent_id: str, topic_keywords: list[str]
) -> float:
    """Score based on unique operational data the agent owns."""
    sources = DATA_SOURCES.get(agent_id, [])
    if not sources:
        return 0.0
    topic_set = set(topic_keywords)
    relevant = sum(
        1 for src in sources if topic_set & set(extract_keywords(src))
    )
    if relevant == 0:
        return 0.3
    return min(1.0, relevant / len(sources) + 0.5)


def calculate_composite_relevance(
    agent_id: str,
    meeting_context: dict[str, Any],
    agent_config: dict[str, Any],
    recent_messages: list[Message],
    *,
    agent_profiles: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Multi-dimensional composite relevance score (5 dimensions)."""
    topic_kw = meeting_context.get("current_topic", {}).get("keywords", [])
    current_phase = detect_current_phase(recent_messages)

    scores: dict[str, float] = {
        "domain_expertise": calculate_expertise_relevance(agent_config, topic_kw),
        "complementary_value": calculate_complementary_value(agent_id, meeting_context),
        "historical_success": calculate_historical_success(
            agent_id, topic_kw, agent_profiles=agent_profiles
        ),
        "phase_alignment": calculate_phase_alignment(
            agent_id, current_phase, agent_config
        ),
        "data_ownership": calculate_data_ownership(agent_id, topic_kw),
    }

    total = sum(scores[dim] * RELEVANCE_DIMENSIONS[dim] for dim in RELEVANCE_DIMENSIONS)
    scores["composite_total"] = total
    scores["detected_phase"] = current_phase  # type: ignore[assignment]
    return scores


def calculate_contribution_score(
    agent_id: str,
    proposed_message: str,
    meeting_context: dict[str, Any],
    agent_config: dict[str, Any],
    recent_messages: list[Message],
) -> float:
    """Calculate contribution value score (0.0--1.0).

    Dimensions: novelty (35%), expertise (30%), ownership (20%), question (15%).
    """
    score = 0.0

    # Novelty
    novelty = calculate_novelty(proposed_message, recent_messages[-5:])
    score += SCORING_WEIGHTS["novelty"] * novelty

    # Expertise relevance
    topic_kw = meeting_context.get("current_topic", {}).get("keywords", [])
    score += SCORING_WEIGHTS["expertise"] * calculate_expertise_relevance(
        agent_config, topic_kw
    )

    # Ownership
    primary = meeting_context.get("current_topic", {}).get("primary_stakeholders", [])
    if agent_id in primary:
        score += SCORING_WEIGHTS["ownership"]

    # Direct question
    if is_directly_questioned(agent_id, recent_messages):
        score += SCORING_WEIGHTS["question"]

    return score


# =====================================================================
# Main gating function
# =====================================================================

def should_agent_speak(
    agent_id: str,
    message: Message,
    channel: Channel,
    chat: ChatManager | None = None,
    agent_config: dict[str, Any] | None = None,
    use_composite_relevance: bool = False,
) -> bool:
    """Determine if *agent_id* should respond.

    - Explicit ``@agent_id`` mention always allows speech.
    - Otherwise contribution score is checked against the threshold
      for the agent's current stakeholder status.

    Parameters
    ----------
    agent_id:
        Agent identifier.
    message:
        The triggering message.
    channel:
        Current channel (must have *meeting_context* for gating).
    chat:
        Optional :class:`ChatManager` for fetching recent messages.
    agent_config:
        Agent configuration dict (triggers, capabilities).  The caller
        provides this -- no filesystem access needed.
    use_composite_relevance:
        If *True*, use the composite relevance matrix instead of
        basic contribution score.
    """
    # Explicit mention always overrides
    if f"@{agent_id}" in message.content:
        return True

    # No meeting context = chat mode, allow
    if not channel.meeting_context:
        return True

    meeting_ctx = channel.meeting_context
    stakeholder_status = meeting_ctx.get("stakeholder_status", {}).get(
        agent_id, StakeholderStatus.OBSERVER.value
    )
    threshold = STAKEHOLDER_THRESHOLDS.get(stakeholder_status, 0.8)

    # Get recent messages
    recent_messages: list[Message] = []
    if chat is not None:
        recent_messages = chat.get_channel_messages(channel.id, limit=10)

    config = agent_config or {"triggers": [], "capabilities": []}

    if use_composite_relevance:
        relevance = calculate_composite_relevance(
            agent_id=agent_id,
            meeting_context=meeting_ctx,
            agent_config=config,
            recent_messages=recent_messages,
        )
        score = relevance["composite_total"]
    else:
        score = calculate_contribution_score(
            agent_id=agent_id,
            proposed_message="[considering response]",
            meeting_context=meeting_ctx,
            agent_config=config,
            recent_messages=recent_messages,
        )

    return score >= threshold


# =====================================================================
# Topic shift detection
# =====================================================================

def detect_topic_shift(
    messages: list[Message], meeting_context: dict[str, Any]
) -> bool:
    """Detect if the conversation topic has significantly changed."""
    if not messages:
        return False
    recent_kw: set[str] = set()
    for msg in messages[-TOPIC_LOOKBACK_MESSAGES:]:
        recent_kw.update(extract_keywords(msg.content))
    previous_kw = set(
        meeting_context.get("current_topic", {}).get("keywords", [])
    )
    if not previous_kw:
        return False
    overlap = calculate_keyword_overlap(list(recent_kw), list(previous_kw))
    return overlap < TOPIC_SHIFT_THRESHOLD


def identify_stakeholders_for_topic(
    topic_keywords: list[str],
    agents: dict[str, dict[str, Any]],
    relevance_threshold: float = 0.5,
) -> list[str]:
    """Identify agents that should be active for *topic_keywords*.

    Parameters
    ----------
    agents:
        Mapping of ``agent_id -> config dict`` (with triggers/capabilities).
        The caller provides this instead of scanning a filesystem.
    """
    stakeholders: list[str] = []
    for agent_id, config in agents.items():
        relevance = calculate_expertise_relevance(config, topic_keywords)
        if relevance >= relevance_threshold:
            stakeholders.append(agent_id)
    return stakeholders


def update_stakeholder_status(
    agent_id: str,
    new_status: StakeholderStatus,
    channel: Channel,
    chat: ChatManager,
) -> None:
    """Update an agent's stakeholder status in a channel."""
    if not channel.meeting_context:
        channel.meeting_context = {
            "stakeholder_status": {},
            "contribution_history": [],
            "current_topic": {"keywords": [], "primary_stakeholders": []},
        }
    meeting_ctx = channel.meeting_context
    stakeholder_status = meeting_ctx.get("stakeholder_status", {})
    stakeholder_status[agent_id] = new_status.value
    meeting_ctx["stakeholder_status"] = stakeholder_status
    channel.meeting_context = meeting_ctx
