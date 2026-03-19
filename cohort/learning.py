"""Conversation learning system for Cohort.

Extracts durable knowledge from agent conversations, deduplicates against
existing facts, builds and evolves a native user profile. All processing
uses the local Qwen model (free, private, no API calls).

Runs asynchronously after agent responses are delivered -- never blocks chat.
All failures are non-fatal (logged, never raised).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from cohort.agent import LearnedFact
from cohort.agent_context import _normalize_query, _score_text
from cohort.agent_store import AgentStore
from cohort.local.config import (
    DEFAULT_MODEL,
    LEARNING_DEDUP_THRESHOLD,
    LEARNING_ENABLED,
    LEARNING_EXTRACTION_PARAMS,
    LEARNING_EXTRACTION_PROMPT,
    LEARNING_GATE_THRESHOLD,
    LEARNING_MAX_FACTS_PER_AGENT,
    LEARNING_MIN_RESPONSE_LENGTH,
    LEARNING_PROFILE_DISTILL_PROMPT,
    LEARNING_PROFILE_EVOLVE_DAYS,
    LEARNING_PROFILE_MIN_NEW_PREFS,
    LEARNING_SKIP_AGENTS,
)

logger = logging.getLogger(__name__)

# Valid fact categories from extraction
_VALID_CATEGORIES = {"domain_fact", "procedure", "preference", "correction", "tool_usage"}

# Question words that indicate substantive queries
_QUESTION_WORDS = {"how", "why", "what", "explain", "describe", "when", "where", "which"}

# Profile location
_PROFILE_PATH = Path.home() / ".cohort" / "profile.json"


# =====================================================================
# Heuristic Gate
# =====================================================================

def _should_extract(message: str, response: str, agent_id: str) -> bool:
    """Decide whether a conversation is worth extracting facts from.

    Uses cheap heuristics (no model call). Returns True when 2+ signals
    suggest the response contains durable knowledge.
    """
    if not LEARNING_ENABLED:
        return False

    if agent_id.lower() in LEARNING_SKIP_AGENTS:
        return False

    # Always skip very short responses (acknowledgments, yes/no)
    if len(response) < LEARNING_MIN_RESPONSE_LENGTH:
        return False

    # Always skip error responses
    if response.lstrip().startswith(("[Error]", "[Timeout]", "Error:")):
        return False

    signals = 0

    # Signal: substantial response
    if len(response) > LEARNING_GATE_THRESHOLD:
        signals += 1

    # Signal: contains code
    if "```" in response:
        signals += 1

    # Signal: structured content (lists, headers)
    if re.search(r"^[\s]*[-*\d]+[.)]\s", response, re.MULTILINE):
        signals += 1
    if re.search(r"^#{1,4}\s", response, re.MULTILINE):
        signals += 1

    # Signal: user asked a substantive question
    first_word = message.strip().split()[0].lower().rstrip("?:") if message.strip() else ""
    if first_word in _QUESTION_WORDS:
        signals += 1

    return signals >= 2


# =====================================================================
# Fact Extraction
# =====================================================================

def _extract_facts(
    agent_id: str,
    message: str,
    response: str,
    client: Any,
    model: str,
) -> list[dict[str, str]]:
    """Call local Qwen to extract facts from a conversation.

    Returns list of dicts with keys: fact, confidence, category.
    Returns empty list on any failure.
    """
    # Truncate to keep extraction prompt reasonable
    msg_truncated = message[:1000]
    resp_truncated = response[:3000]

    prompt = LEARNING_EXTRACTION_PROMPT.format(
        message=msg_truncated,
        agent_id=agent_id,
        response=resp_truncated,
    )

    try:
        result = client.generate(
            model=model,
            prompt=prompt,
            temperature=LEARNING_EXTRACTION_PARAMS["temperature"],
            think=LEARNING_EXTRACTION_PARAMS["think"],
            keep_alive=LEARNING_EXTRACTION_PARAMS["keep_alive"],
            options={"num_predict": LEARNING_EXTRACTION_PARAMS["num_predict"]},
        )
    except Exception:
        logger.debug("[!] Qwen extraction call failed", exc_info=True)
        return []

    if result is None or not result.text.strip():
        return []

    return _parse_facts_json(result.text.strip())


def _parse_facts_json(text: str) -> list[dict[str, str]]:
    """Parse JSON facts from model output. Handles malformed JSON gracefully."""
    # Try direct parse first
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return _validate_facts(data)
    except json.JSONDecodeError:
        pass

    # Try extracting JSON array from surrounding text
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, list):
                return _validate_facts(data)
        except json.JSONDecodeError:
            pass

    return []


def _validate_facts(facts: list[Any]) -> list[dict[str, str]]:
    """Validate and normalize extracted facts."""
    valid = []
    for item in facts:
        if not isinstance(item, dict):
            continue
        fact_text = item.get("fact", "").strip()
        if not fact_text or len(fact_text) < 10:
            continue

        confidence = item.get("confidence", "medium")
        if confidence not in ("high", "medium", "low"):
            confidence = "medium"

        category = item.get("category", "domain_fact")
        if category not in _VALID_CATEGORIES:
            category = "domain_fact"

        valid.append({
            "fact": fact_text,
            "confidence": confidence,
            "category": category,
        })

    # Cap at 5 facts per extraction (prevent runaway)
    return valid[:5]


# =====================================================================
# Deduplication
# =====================================================================

def _is_duplicate(
    new_fact: str,
    existing_facts: list[LearnedFact],
    threshold: float = LEARNING_DEDUP_THRESHOLD,
) -> tuple[bool, int | None]:
    """Check if a fact is a near-duplicate of any existing fact.

    Uses bidirectional term-overlap scoring (same logic as fact retrieval).

    Returns:
        (is_dup, index) -- index of matching fact for timestamp freshening,
        or (False, None) if no duplicate found.
    """
    new_terms = _normalize_query(new_fact)
    if not new_terms:
        return False, None

    for i, existing in enumerate(existing_facts):
        existing_text = existing.fact
        # Forward: how well does the new fact match the existing one?
        forward = _score_text(existing_text, new_terms)
        # Reverse: how well does the existing fact match the new one?
        existing_terms = _normalize_query(existing_text)
        reverse = _score_text(new_fact, existing_terms) if existing_terms else 0.0

        if max(forward, reverse) >= threshold:
            return True, i

    return False, None


def _enforce_fact_cap(
    facts: list[LearnedFact],
    cap: int = LEARNING_MAX_FACTS_PER_AGENT,
) -> list[LearnedFact]:
    """Trim facts to cap, dropping oldest low-confidence facts first."""
    if len(facts) <= cap:
        return facts

    # Sort by (confidence_rank, timestamp) -- low-confidence + old = first to go
    conf_rank = {"high": 2, "medium": 1, "low": 0}

    indexed = list(enumerate(facts))
    # Sort by confidence (ascending) then timestamp (ascending = oldest first)
    indexed.sort(key=lambda x: (
        conf_rank.get(x[1].confidence, 1),
        x[1].timestamp or "",
    ))

    # Drop from the front (lowest confidence, oldest)
    to_drop = len(facts) - cap
    drop_indices = {indexed[i][0] for i in range(to_drop)}

    return [f for i, f in enumerate(facts) if i not in drop_indices]


# =====================================================================
# Core Learning Loop
# =====================================================================

def _learn_from_conversation(
    agent_id: str,
    channel_id: str,
    message: str,
    response: str,
    agent_store: AgentStore,
) -> int:
    """Extract facts from conversation and store in agent memory.

    Returns number of new facts stored.
    """
    from cohort.local.ollama import OllamaClient
    from cohort.memory_manager import MemoryManager

    client = OllamaClient(timeout=60)
    if not client.health_check():
        return 0

    # Extract facts via Qwen
    raw_facts = _extract_facts(agent_id, message, response, client, DEFAULT_MODEL)
    if not raw_facts:
        return 0

    # Load existing memory for dedup
    mm = MemoryManager(agent_store)
    memory = agent_store.load_memory(agent_id)
    existing_facts = memory.learned_facts if memory else []

    stored = 0
    now = datetime.now().isoformat()

    for raw in raw_facts:
        is_dup, dup_idx = _is_duplicate(raw["fact"], existing_facts)

        if is_dup and dup_idx is not None:
            # Freshen the existing fact's timestamp
            mm.update_fact_timestamp(agent_id, dup_idx, now)
            continue

        fact = LearnedFact(
            fact=raw["fact"],
            learned_from=f"conversation:{channel_id}",
            timestamp=now,
            confidence=raw["confidence"],
        )
        mm.add_learned_fact(agent_id, fact)
        existing_facts.append(fact)  # Track for dedup within this batch
        stored += 1

    # Enforce fact cap if needed
    if stored > 0 and len(existing_facts) > LEARNING_MAX_FACTS_PER_AGENT:
        memory = agent_store.load_memory(agent_id)
        if memory:
            memory.learned_facts = _enforce_fact_cap(memory.learned_facts)
            agent_store.save_memory(agent_id, memory)

    # Check if profile evolution is due
    if stored > 0:
        _maybe_evolve_profile(agent_store, client, DEFAULT_MODEL)

    return stored


# =====================================================================
# User Profile Builder
# =====================================================================

def bootstrap_profile(
    display_name: str,
    display_role: str,
    core_paragraph: str = "",
) -> dict[str, Any]:
    """Create initial ~/.cohort/profile.json.

    If core_paragraph is empty, creates a minimal profile from display info.
    Returns the profile dict.
    """
    if not core_paragraph:
        core_paragraph = f"{display_name} ({display_role})."

    profile: dict[str, Any] = {
        "version": "1.0",
        "core_paragraph": core_paragraph,
        "adaptation_rules": {
            "response_length": "medium",
            "summarize_back": True,
            "confirm_decisions": True,
            "praise_before_feedback": True,
            "options_per_question": 3,
            "custom_rules": [],
        },
        "last_updated": datetime.now().isoformat(),
    }

    _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROFILE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")

    # Reset the cached profile path in agent_context
    try:
        import cohort.agent_context as ac
        ac._DEFAULT_PROFILE_PATH = None  # noqa: SLF001 -- force re-discovery
    except Exception:
        pass

    logger.info("[OK] Created user profile at %s", _PROFILE_PATH)
    return profile


def load_profile() -> dict[str, Any] | None:
    """Load the current profile, or None if it doesn't exist."""
    if not _PROFILE_PATH.exists():
        return None
    try:
        return json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _maybe_evolve_profile(
    agent_store: AgentStore,
    client: Any,
    model: str,
) -> bool:
    """Re-distill user profile if enough new preference data has accumulated.

    Criteria: profile > LEARNING_PROFILE_EVOLVE_DAYS old AND
    LEARNING_PROFILE_MIN_NEW_PREFS new preference/correction facts since last update.

    Returns True if profile was updated.
    """
    profile = load_profile()
    if profile is None:
        return False

    # Check age
    last_updated = profile.get("last_updated", "")
    if last_updated:
        try:
            last_dt = datetime.fromisoformat(last_updated.split("+")[0])
            days_since = (datetime.now() - last_dt).days
            if days_since < LEARNING_PROFILE_EVOLVE_DAYS:
                return False
        except (ValueError, TypeError):
            pass

    # Collect preference/correction facts across all agents
    pref_facts: list[str] = []
    for agent in agent_store.list_agents(include_hidden=True):
        memory = agent_store.load_memory(agent.agent_id)
        if memory is None:
            continue
        for fact in memory.learned_facts:
            source = getattr(fact, "learned_from", "")
            # Only include conversation-sourced facts
            if not source.startswith("conversation:"):
                continue
            # Check category in fact text or source (category stored in fact text)
            fact_lower = fact.fact.lower()
            if any(kw in fact_lower for kw in (
                "prefer", "don't", "do not", "shorter", "longer",
                "stop", "always", "never", "instead",
            )):
                pref_facts.append(fact.fact)

    if len(pref_facts) < LEARNING_PROFILE_MIN_NEW_PREFS:
        return False

    # Distill into profile update
    observations = "\n".join(f"- {f}" for f in pref_facts[-50:])  # Last 50
    prompt = LEARNING_PROFILE_DISTILL_PROMPT.format(observations=observations)

    try:
        result = client.generate(
            model=model,
            prompt=prompt,
            temperature=0.15,
            think=False,
            keep_alive="2m",
            options={"num_predict": 2048},
        )
    except Exception:
        logger.debug("[!] Profile evolution Qwen call failed", exc_info=True)
        return False

    if result is None or not result.text.strip():
        return False

    # Parse the profile JSON
    try:
        new_profile = json.loads(result.text.strip())
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", result.text.strip(), re.DOTALL)
        if not match:
            return False
        try:
            new_profile = json.loads(match.group())
        except json.JSONDecodeError:
            return False

    # Merge into existing profile (preserve fields Qwen didn't output)
    if "core_paragraph" in new_profile:
        profile["core_paragraph"] = new_profile["core_paragraph"]
    if "adaptation_rules" in new_profile and isinstance(new_profile["adaptation_rules"], dict):
        if "adaptation_rules" not in profile:
            profile["adaptation_rules"] = {}
        profile["adaptation_rules"].update(new_profile["adaptation_rules"])

    profile["last_updated"] = datetime.now().isoformat()
    profile["version"] = "1.0"

    _PROFILE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    logger.info("[OK] Evolved user profile with %d preference observations", len(pref_facts))

    # Reset cached profile path
    try:
        import cohort.agent_context as ac
        ac._DEFAULT_PROFILE_PATH = None  # noqa: SLF001
    except Exception:
        pass

    return True


# =====================================================================
# Async Entry Point
# =====================================================================

def maybe_learn_async(
    agent_id: str,
    channel_id: str,
    message: str,
    response: str,
    agent_store: AgentStore | None,
) -> None:
    """Non-blocking entry point for conversation learning.

    Spawns a daemon thread to extract facts. Never raises.
    Called from agent_router.py after response is posted.
    """
    if agent_store is None or not LEARNING_ENABLED:
        return

    if not _should_extract(message, response, agent_id):
        return

    t = threading.Thread(
        target=_safe_learn,
        args=(agent_id, channel_id, message, response, agent_store),
        daemon=True,
        name=f"learn-{agent_id}",
    )
    t.start()


def _safe_learn(
    agent_id: str,
    channel_id: str,
    message: str,
    response: str,
    agent_store: AgentStore,
) -> None:
    """Thread target. Wraps _learn_from_conversation with error handling."""
    try:
        count = _learn_from_conversation(agent_id, channel_id, message, response, agent_store)
        if count > 0:
            logger.info("[OK] Learned %d fact(s) from %s in #%s", count, agent_id, channel_id)
    except Exception:
        logger.debug("[!] Learning extraction failed for %s", agent_id, exc_info=True)
