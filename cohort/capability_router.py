"""Dynamic capability-based agent routing for cohort.

Replaces hardcoded topic-to-agent mappings with runtime queries
against AgentStore.  Agents are matched by their declared triggers,
capabilities, and domain expertise -- not by name.

Also provides:

- Partnership graph traversal (who must be consulted before execution)
- Acceptance criteria collection helpers
- Memory trim utility

All functions are pure (no side effects) and operate on AgentConfig
objects from the store.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from cohort.agent import AgentConfig

logger = logging.getLogger(__name__)


# =====================================================================
# Stop words (shared with meeting.py / agent.py)
# =====================================================================

_STOP_WORDS: frozenset[str] = frozenset(
    "the a an is are was were in on at to for of and or but with from by "
    "this that these those be have has had do does did will would should "
    "could can may might must i you he she it we they my your his her its "
    "our their me him us them".split()
)


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from text."""
    words = re.findall(r"\b[a-z0-9_.]+\b", text.lower())
    return [w for w in words if w not in _STOP_WORDS and len(w) > 2]


# =====================================================================
# Capability Routing
# =====================================================================

def score_agent_for_topic(
    agent: AgentConfig,
    topic_keywords: list[str],
) -> float:
    """Score how well an agent matches a set of topic keywords (0.0-1.0).

    Checks three sources from the agent's config:
    - triggers (exact keyword matches, highest signal)
    - capabilities (keyword-in-phrase matches)
    - domain_expertise (keyword-in-phrase matches)

    Returns a weighted score normalized to 0.0-1.0.
    """
    if not topic_keywords or agent.status != "active":
        return 0.0

    topic_set = set(topic_keywords)
    score = 0.0

    # Triggers: exact match (highest weight)
    trigger_set = {t.lower().strip(".") for t in agent.triggers}
    trigger_hits = len(topic_set & trigger_set)
    if trigger_set:
        score += 0.50 * min(trigger_hits / max(len(topic_set), 1), 1.0)

    # Capabilities: keyword-in-phrase match
    cap_text = " ".join(agent.capabilities).lower()
    cap_hits = sum(1 for kw in topic_keywords if kw in cap_text)
    if agent.capabilities:
        score += 0.30 * min(cap_hits / max(len(topic_set), 1), 1.0)

    # Domain expertise: keyword-in-phrase match
    exp_text = " ".join(agent.domain_expertise).lower()
    exp_hits = sum(1 for kw in topic_keywords if kw in exp_text)
    if agent.domain_expertise:
        score += 0.20 * min(exp_hits / max(len(topic_set), 1), 1.0)

    return min(score, 1.0)


def find_agents_for_topic(
    agents: list[AgentConfig],
    topic: str,
    *,
    min_score: float = 0.1,
    max_results: int = 8,
    prefer_type: str | None = None,
) -> list[tuple[AgentConfig, float]]:
    """Find agents best-qualified for a topic, ranked by capability match.

    Parameters
    ----------
    agents:
        All available agents (from AgentStore.list_agents()).
    topic:
        Free-text topic description or keywords.
    min_score:
        Minimum relevance score to include (0.0-1.0).
    max_results:
        Maximum number of agents to return.
    prefer_type:
        If set, agents of this type get a 0.1 bonus (e.g., "specialist").

    Returns
    -------
    List of (AgentConfig, score) tuples, sorted by descending score.
    """
    keywords = _extract_keywords(topic)
    if not keywords:
        return []

    scored: list[tuple[AgentConfig, float]] = []
    for agent in agents:
        if agent.status != "active":
            continue
        s = score_agent_for_topic(agent, keywords)

        # Type preference bonus
        if prefer_type and agent.agent_type == prefer_type:
            s += 0.1

        # Skill level bonus: if agent has relevant skill_levels, boost
        skill_levels = agent.education.skill_levels
        if skill_levels:
            relevant_skills = [
                v for k, v in skill_levels.items()
                if any(kw in k.lower() for kw in keywords)
            ]
            if relevant_skills:
                avg_skill = sum(relevant_skills) / len(relevant_skills)
                s += 0.05 * (avg_skill / 10.0)  # up to +0.05 for skill=10

        if s >= min_score:
            scored.append((agent, min(s, 1.0)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:max_results]


def route_task(
    agents: list[AgentConfig],
    task_description: str,
    *,
    prefer_type: str | None = None,
) -> AgentConfig | None:
    """Route a task to the single best-qualified agent.

    Returns None if no agent scores above the minimum threshold.
    """
    results = find_agents_for_topic(
        agents, task_description, min_score=0.15, max_results=1,
        prefer_type=prefer_type,
    )
    return results[0][0] if results else None


# =====================================================================
# Partnership Graph
# =====================================================================

def get_partnerships(agent: AgentConfig) -> dict[str, dict[str, Any]]:
    """Read an agent's declared partnerships.

    Returns a dict of partner_id -> {relationship, protocol, ...}.
    """
    return agent.partnerships or {}


def find_required_consultations(
    agent: AgentConfig,
    task_keywords: list[str],
    available_agents: dict[str, AgentConfig],
) -> list[dict[str, Any]]:
    """Determine which partners must be consulted before this agent executes.

    Examines the agent's partnerships and checks if any partner's
    relationship or protocol matches the task context. Only returns
    partners that actually exist in the current deployment.

    Parameters
    ----------
    agent:
        The agent assigned to execute the task.
    task_keywords:
        Keywords describing the task.
    available_agents:
        Dict of agent_id -> AgentConfig for all registered agents.

    Returns
    -------
    List of dicts with keys: partner_id, relationship, protocol, reason.
    """
    partnerships = get_partnerships(agent)
    if not partnerships:
        return []

    consultations: list[dict[str, Any]] = []
    task_text = " ".join(task_keywords).lower()

    for partner_id, details in partnerships.items():
        # Skip partners that don't exist in this deployment
        if partner_id not in available_agents:
            logger.debug(
                "Partner '%s' of '%s' not in current deployment -- skipping",
                partner_id, agent.agent_id,
            )
            continue

        # Check if partner is active
        partner = available_agents[partner_id]
        if partner.status != "active":
            continue

        relationship = details.get("relationship", "").lower()
        protocol = details.get("protocol", "").lower()
        combined = f"{relationship} {protocol}"

        # Determine if this partnership is relevant to the task
        reason = _match_consultation_reason(combined, task_text, task_keywords)
        if reason:
            consultations.append({
                "partner_id": partner_id,
                "relationship": details.get("relationship", ""),
                "protocol": details.get("protocol", ""),
                "reason": reason,
            })

    return consultations


def _match_consultation_reason(
    partnership_text: str,
    task_text: str,
    task_keywords: list[str],
) -> str | None:
    """Check if a partnership is relevant to the task context.

    Returns a reason string if consultation is needed, None otherwise.
    """
    # Security partnerships: triggered by code changes, auth, crypto, etc.
    security_triggers = {"security", "auth", "crypto", "vulnerability", "input validation"}
    if any(t in partnership_text for t in {"security", "audit", "review"}):
        code_indicators = {"code", "implement", "api", "endpoint", "function", "class",
                          "module", "file", "write", "create", "modify", "add", "fix"}
        if any(ind in task_text for ind in code_indicators) or any(
            ind in task_text for ind in security_triggers
        ):
            return "Security review required for code changes"

    # QA/test partnerships: triggered by implementation tasks
    if any(t in partnership_text for t in {"test", "qa", "quality"}):
        impl_indicators = {"implement", "create", "build", "add", "feature", "code"}
        if any(ind in task_text for ind in impl_indicators):
            return "Test strategy review recommended before implementation"

    # API contract partnerships: triggered by API/interface work
    if any(t in partnership_text for t in {"contract", "api", "interface", "schema"}):
        if any(kw in task_keywords for kw in ["api", "endpoint", "interface", "schema"]):
            return "API contract alignment needed with partner"

    # Architecture partnerships: triggered by structural changes
    if any(t in partnership_text for t in {"architecture", "design", "structure"}):
        if any(kw in task_keywords for kw in ["refactor", "architecture", "redesign", "migrate"]):
            return "Architectural review recommended"

    return None


def build_partnership_graph(
    agents: list[AgentConfig],
) -> dict[str, list[dict[str, str]]]:
    """Build the full partnership graph for all agents.

    Returns a dict mapping each agent_id to its list of partnerships
    (only including partners that exist in the current agent set).
    """
    agent_ids = {a.agent_id for a in agents}
    graph: dict[str, list[dict[str, str]]] = {}

    for agent in agents:
        partnerships = get_partnerships(agent)
        edges: list[dict[str, str]] = []
        for partner_id, details in partnerships.items():
            if partner_id in agent_ids:
                edges.append({
                    "partner_id": partner_id,
                    "relationship": details.get("relationship", ""),
                    "protocol": details.get("protocol", ""),
                })
        if edges:
            graph[agent.agent_id] = edges

    return graph


# =====================================================================
# Acceptance Criteria Helpers
# =====================================================================

def collect_acceptance_criteria(
    task_description: str,
    assignee: AgentConfig,
    consultations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build an acceptance criteria scaffold for a task.

    Combines the assignee's success_criteria with consultation
    requirements from partners. Returns a structure that can be
    attached to a task before execution.
    """
    criteria: dict[str, Any] = {
        "task": task_description,
        "assignee": assignee.agent_id,
        "created_at": datetime.now().isoformat(),
        "criteria": [],
        "consultation_requirements": [],
    }

    # Base criteria from the assignee's config
    for i, sc in enumerate(assignee.success_criteria, 1):
        criteria["criteria"].append({
            "id": f"SC-{i}",
            "description": sc,
            "source": assignee.agent_id,
            "status": "pending",
        })

    # Consultation-derived criteria
    for consultation in consultations:
        criteria["consultation_requirements"].append({
            "partner_id": consultation["partner_id"],
            "reason": consultation["reason"],
            "protocol": consultation["protocol"],
            "status": "pending",
        })

    return criteria


# =====================================================================
# Memory Trim
# =====================================================================

def trim_agent_memory(
    memory_dict: dict[str, Any],
    keep_last: int = 15,
) -> dict[str, Any]:
    """Trim an agent's working memory while preserving learned facts.

    Parameters
    ----------
    memory_dict:
        Raw memory dict (from memory.json).
    keep_last:
        Number of recent working_memory entries to keep.

    Returns
    -------
    Trimmed memory dict (mutated in place and returned).
    """
    wm = memory_dict.get("working_memory", [])
    if len(wm) > keep_last:
        trimmed = len(wm) - keep_last
        memory_dict["working_memory"] = wm[-keep_last:]
        logger.info(
            "Trimmed %d working memory entries for %s",
            trimmed, memory_dict.get("agent_id", "unknown"),
        )

    # Learned facts are NEVER trimmed
    # Active tasks are NEVER trimmed

    return memory_dict
