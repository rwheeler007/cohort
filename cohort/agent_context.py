"""Agent context builder for Cohort prompt injection.

Loads an agent's persistent memory (learned facts, collaborators, active tasks)
and builds a compact context block for injection into the agent's prompt.

Mirrors SMACK's load_boss_context() but adapted for Cohort's data model.
Also handles user profile loading via the shared profile adapter module.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# =====================================================================
# Fact scoring (ported from BOSS tools/memory_search.py)
# =====================================================================

_NOISE_WORDS = frozenset(
    "the a an is are was were in on at to for of and or but with from by "
    "this that these those be have has had do does did will would should "
    "could can may might must i you he she it we they".split()
)

MAX_FACTS = 10
MAX_CONTEXT_TOKENS = 500  # Rough budget (~2000 chars)


def _normalize_query(query: str) -> list[str]:
    """Split query into lowercase search terms, dropping noise words."""
    terms = re.findall(r"[a-z0-9_]+", query.lower())
    return [t for t in terms if t not in _NOISE_WORDS and len(t) > 1]


def _score_text(text: str, terms: list[str]) -> float:
    """Score text against query terms. Returns 0.0-1.0 based on term overlap.

    Uses word-boundary matching to prevent spurious substring hits
    (e.g., query term 'our' matching 'Courses').
    """
    if not text or not terms:
        return 0.0
    # Split into word tokens for boundary-aware matching
    text_words = set(re.findall(r"[a-z0-9_]+", text.lower()))
    matched = sum(1 for t in terms if t in text_words)
    return matched / len(terms)


def _recency_boost(timestamp_str: str | None, max_boost: float = 0.2) -> float:
    """Add up to max_boost for recent entries. Decays over 90 days."""
    if not timestamp_str:
        return 0.0
    try:
        ts = timestamp_str.replace("Z", "+00:00")
        if "T" in ts:
            dt = datetime.fromisoformat(ts.split("+")[0])
        else:
            dt = datetime.strptime(ts[:10], "%Y-%m-%d")
        days_ago = max(0, (datetime.now() - dt).days)
        decay = max(0.0, 1.0 - (days_ago / 90))
        return decay * max_boost
    except (ValueError, TypeError):
        return 0.0


def _is_headline_only(fact: dict[str, Any]) -> bool:
    """Detect shallow temporal facts that are just article headlines.

    These have no actionable content -- just a title and source attribution.
    They dilute the context when padded into the prompt.
    """
    text = fact.get("fact", "")
    source = fact.get("learned_from", "")
    # Temporal injector headlines: short text with "(via domain.com)" suffix
    if "temporal_facts_injector" in source:
        # Real temporal facts have explanatory content (100+ chars typical).
        # Headlines are just titles: usually < 120 chars.
        if len(text) < 150 and ("(via " in text or text.startswith("[")):
            return True
    return False


def _select_facts(
    facts: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    """Select the most relevant learned facts for the current query.

    Uses term-overlap scoring + recency/confidence boosts.
    Confidence and recency only boost facts that have baseline relevance
    (at least one query term match). This prevents headline-only temporal
    facts from scoring above zero purely on recency + high confidence.

    Falls back to most recent *substantive* facts if scoring produces
    fewer than 3 hits.
    """
    terms = _normalize_query(query)
    if not terms:
        # No query -- return most recent substantive facts
        substantive = [f for f in facts if not _is_headline_only(f)]
        return (substantive or facts)[-MAX_FACTS:]

    scored = []
    for fact in facts:
        fact_text = fact.get("fact", "")
        base = _score_text(fact_text, terms)

        # Only apply boosts when there's baseline relevance (term overlap > 0)
        if base > 0.0:
            conf_boost = {"high": 0.1, "medium": 0.0, "low": -0.05}.get(
                fact.get("confidence", "medium"), 0.0
            )
            rec = _recency_boost(fact.get("timestamp"))
            score = min(1.0, base + conf_boost + rec)
        else:
            score = 0.0

        scored.append((score, fact))

    scored.sort(key=lambda x: x[0], reverse=True)
    # Filter out headline-only facts from scored results
    top = [f for s, f in scored[:MAX_FACTS * 2] if s > 0.0 and not _is_headline_only(f)]
    top = top[:MAX_FACTS]

    # Pad with most recent substantive facts if fewer than 3 scored hits
    if len(top) < 3:
        scored_ids = {id(f) for f in top}
        substantive_pad = [
            f for f in reversed(facts)
            if id(f) not in scored_ids and not _is_headline_only(f)
        ]
        top += substantive_pad[: MAX_FACTS - len(top)]

    return top


# =====================================================================
# Agent context builder
# =====================================================================

def load_agent_context(
    agent_id: str,
    query: str = "",
    agent_store: Any | None = None,
) -> str:
    """Build a context block from the agent's persistent memory.

    Includes:
    - Learned facts (contextually selected against current query)
    - Active tasks
    - Collaborator awareness

    Args:
        agent_id: The agent whose memory to load.
        query: The current user message (for contextual fact selection).
        agent_store: AgentStore instance for loading memory.

    Returns:
        Formatted context string for prompt injection, or empty string.
    """
    if agent_store is None:
        return ""

    memory = agent_store.load_memory(agent_id)
    if memory is None:
        return ""

    parts: list[str] = []

    # Learned facts -- contextually selected
    facts_raw = [f.to_dict() if hasattr(f, "to_dict") else f for f in memory.learned_facts]
    if facts_raw:
        selected = _select_facts(facts_raw, query)
        if selected:
            lines = []
            for f in selected:
                conf = f.get("confidence", "medium")
                source = f.get("learned_from", "unknown")
                lines.append(f"- {f['fact']} ({source}, {conf} confidence)")
            parts.append("## Your Knowledge\n" + "\n".join(lines))

    # Active tasks
    if memory.active_tasks:
        lines = []
        for task in memory.active_tasks:
            status = task.get("status", "unknown")
            collab = task.get("collaborator", "")
            desc = task.get("task", task.get("description", ""))
            line = f"- {desc} (status: {status})"
            if collab:
                line += f" with @{collab}"
            lines.append(line)
        parts.append("## Your Active Tasks\n" + "\n".join(lines))

    # Collaborators (compact -- just names and relationships)
    if memory.collaborators:
        collabs = []
        for name, info in memory.collaborators.items():
            rel = info.get("relationship", "")
            collabs.append(f"@{name}" + (f" ({rel})" if rel else ""))
        if collabs:
            parts.append("## Your Collaborators\n" + ", ".join(collabs))

    if not parts:
        return ""

    return "=== AGENT MEMORY ===\n" + "\n\n".join(parts) + "\n=== END AGENT MEMORY ===\n"


# =====================================================================
# Project memory loader
# =====================================================================

MAX_PROJECT_MEMORY_ENTRIES = 10
MAX_PROJECT_MEMORY_TOKENS = 500  # Rough budget (~2000 chars)


def load_project_memory(
    agent_id: str,
    project_path: str | None = None,
    query: str = "",
) -> str:
    """Load per-project working memory for an agent.

    Project memory lives at ``{project_path}/.cohort/data/agents/{agent_id}/project_memory.json``.
    Entries are scored against the current query for relevance, same as learned facts.

    Returns:
        Formatted context string for prompt injection, or empty string.
    """
    if not project_path or not agent_id:
        return ""

    mem_path = Path(project_path) / ".cohort" / "data" / "agents" / agent_id / "project_memory.json"
    if not mem_path.exists():
        return ""

    try:
        data = json.loads(mem_path.read_text(encoding="utf-8"))
    except Exception:
        return ""

    entries = data.get("entries", [])
    if not entries:
        return ""

    # Score entries against current query for relevance
    terms = _normalize_query(query)
    scored: list[tuple[float, dict]] = []
    for entry in entries:
        text = f"{entry.get('input', '')} {entry.get('response', '')}"
        score = _score_text(text, terms) if terms else 0.0
        score += _recency_boost(entry.get("timestamp"))
        scored.append((score, entry))

    # Sort by score descending, take top N
    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [e for _, e in scored[:MAX_PROJECT_MEMORY_ENTRIES]]

    if not selected:
        return ""

    lines: list[str] = []
    char_budget = MAX_PROJECT_MEMORY_TOKENS * 4  # ~4 chars per token
    used = 0
    for entry in selected:
        channel = entry.get("channel", "")
        inp = entry.get("input", "")[:200]
        resp = entry.get("response", "")[:200]
        ts = entry.get("timestamp", "")[:10]  # Just the date
        line = f"- [{ts}] #{channel}: Q: {inp} | A: {resp}"
        if used + len(line) > char_budget:
            break
        lines.append(line)
        used += len(line)

    if not lines:
        return ""

    return (
        "=== PROJECT MEMORY ===\n"
        "Your previous interactions in this project:\n"
        + "\n".join(lines)
        + "\n=== END PROJECT MEMORY ===\n"
    )


# =====================================================================
# User profile loader (delegates to shared BOSS module)
# =====================================================================

# Default profile location for Cohort installations
_DEFAULT_PROFILE_PATH: Path | None = None


def _find_profile_path() -> Path | None:
    """Locate the user profile JSON file.

    Search order:
    1. ~/.cohort/profile.json (Cohort-native)
    2. BOSS smack/data/user_profile.json (if BOSS_ROOT is available)
    """
    global _DEFAULT_PROFILE_PATH
    if _DEFAULT_PROFILE_PATH is not None:
        return _DEFAULT_PROFILE_PATH

    # Check Cohort-native location
    cohort_profile = Path.home() / ".cohort" / "profile.json"
    if cohort_profile.exists():
        _DEFAULT_PROFILE_PATH = cohort_profile
        return _DEFAULT_PROFILE_PATH

    # Check BOSS location (development / shared install)
    try:
        from config.paths import BOSS_ROOT
        boss_profile = BOSS_ROOT / "smack" / "data" / "user_profile.json"
        if boss_profile.exists():
            _DEFAULT_PROFILE_PATH = boss_profile
            return _DEFAULT_PROFILE_PATH
    except ImportError:
        pass

    # Try common BOSS locations
    for candidate in [
        Path("G:/BOSS/smack/data/user_profile.json"),
        Path("C:/BOSS/smack/data/user_profile.json"),
    ]:
        if candidate.exists():
            _DEFAULT_PROFILE_PATH = candidate
            return _DEFAULT_PROFILE_PATH

    return None


def load_user_profile_block(conversation_context: str = "") -> str:
    """Load the user profile and return a prompt injection block.

    Attempts to use the BOSS profile adapter (with distillation) if available.
    Falls back to a simple core-paragraph-only injection if the adapter
    isn't importable (e.g., standalone Cohort install without BOSS).

    Args:
        conversation_context: Recent conversation text for distillation targeting.

    Returns:
        Formatted profile block for prompt injection, or empty string.
    """
    profile_path = _find_profile_path()
    if profile_path is None:
        return ""

    # Try the full BOSS profile adapter (with distillation)
    try:
        from tools.user_profile.profile_adapter import get_profile_block
        return get_profile_block(profile_path, conversation_context)
    except ImportError:
        pass

    # Fallback: load core paragraph directly from JSON
    try:
        with open(profile_path, encoding="utf-8") as f:
            profile = json.load(f)
        core = profile.get("core_paragraph", "")
        if not core:
            return ""

        # Build adaptation rules
        rules = profile.get("adaptation_rules", {})
        directives = []
        if rules.get("summarize_back") is False:
            directives.append("- Do NOT restate or summarize what the user said")
        if rules.get("confirm_decisions") is False:
            directives.append("- Do NOT ask for confirmation on things already decided")
        if rules.get("praise_before_feedback") is False:
            directives.append("- Do NOT pad with praise before giving feedback")
        opts = rules.get("options_per_question")
        if opts is not None and opts <= 1:
            directives.append("- Do NOT present multiple options when user has indicated a direction")
        length = rules.get("response_length", "")
        if length in ("minimal", "short"):
            directives.append("- Keep responses concise -- lead with the answer, skip filler")
        for custom in rules.get("custom_rules", []):
            directives.append(f"- {custom}")

        parts = ["=== OPERATOR PROFILE ===", core]
        if directives:
            parts.append("")
            parts.append("Directives:")
            parts.extend(directives)
        parts.append("=== END OPERATOR PROFILE ===")
        return "\n".join(parts)

    except Exception:
        logger.debug("Failed to load user profile from %s", profile_path, exc_info=True)
        return ""
