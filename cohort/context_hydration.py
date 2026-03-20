"""Channel context hydration for per-channel Claude Code sessions.

When a Claude Code session launches for a channel with existing history,
this module builds a context briefing so Claude can participate from the start.

Three-tier degradation:

  Tier A: Local LLM summarizes channel history -> structured briefing (~1000 tokens)
  Tier B: Heuristic message selection -> raw transcript for Claude (~4000 tokens)
  Tier C: Last 20 messages -> plain text transcript (~2000 tokens)

Tier A requires a local LLM (Ollama/llama-server). If unavailable, falls to B.
Tiers B and C always succeed if there are messages. Thread-safe cache with TTL.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# =====================================================================
# Constants
# =====================================================================

HYDRATION_CACHE_TTL_S = 300     # 5 minutes
TIER_A_TOKEN_BUDGET = 1000      # ~4000 chars for LLM summary
TIER_B_TOKEN_BUDGET = 4000      # ~16000 chars for raw transcript
TIER_C_MESSAGE_LIMIT = 20
TIER_B_MESSAGE_LIMIT = 50
HISTORY_READ_LIMIT = 100        # Max messages to read from channel
CHARS_PER_TOKEN = 4

# Senders whose messages are noise for hydration purposes
_NOISE_SENDERS = frozenset({"system"})


# =====================================================================
# LLM Summarization Prompt (Tier A)
# =====================================================================

HYDRATION_SUMMARY_PROMPT = """\
You are preparing a context briefing for an AI assistant that is \
joining an ongoing team chat conversation. The assistant has no \
prior knowledge of what was discussed. Extract the essential context \
so the assistant can participate effectively.

Output these sections (skip any that are empty):

### Topic & Purpose
- What this channel is about and what the team is working on

### Key Decisions Made
- Conclusions, agreements, and choices already settled

### Open Questions
- Unresolved items, pending decisions, active threads

### Important Context
- Technical details, constraints, or preferences mentioned
- Who said what matters (attribute key positions to speakers)

Keep total output under 800 words. Be concrete and specific. \
The reader needs to understand the conversation state, not just \
the topic.

--- CHANNEL: #{channel_name} ---
{channel_description}
--- CONVERSATION HISTORY ---
{transcript}
--- END ---

Context briefing:"""


# =====================================================================
# Cache (in-memory, thread-safe)
# =====================================================================

_hydration_cache: dict[str, dict[str, Any]] = {}
_cache_lock = threading.Lock()


def get_cached_hydration(channel_id: str) -> Optional[str]:
    """Return cached hydration text if still valid, else None."""
    with _cache_lock:
        entry = _hydration_cache.get(channel_id)
        if entry and (time.time() - entry["created_at"]) < HYDRATION_CACHE_TTL_S:
            return entry["text"]
    return None


def set_cached_hydration(channel_id: str, text: str, tier: str) -> None:
    """Store hydration result in cache."""
    with _cache_lock:
        _hydration_cache[channel_id] = {
            "text": text,
            "tier": tier,
            "created_at": time.time(),
        }


def invalidate_hydration(channel_id: str) -> None:
    """Remove a channel's hydration cache entry."""
    with _cache_lock:
        _hydration_cache.pop(channel_id, None)


# =====================================================================
# Main entry point
# =====================================================================

def hydrate_channel_context(
    chat: Any,
    channel_id: str,
) -> Optional[str]:
    """Build hydration context for a channel session.

    Attempts tiers in order: A (LLM summary) -> B (heuristic transcript)
    -> C (last 20 messages). Returns formatted string or None if channel
    has no history.

    Never raises exceptions. Best-effort.
    """
    # Check cache first
    cached = get_cached_hydration(channel_id)
    if cached is not None:
        logger.info("[OK] Hydration cache hit for #%s", channel_id)
        return cached

    try:
        return _hydrate_impl(chat, channel_id)
    except Exception:
        logger.exception("[X] Hydration failed for #%s", channel_id)
        return None


def _hydrate_impl(chat: Any, channel_id: str) -> Optional[str]:
    """Internal hydration logic. May raise."""
    # Read channel metadata
    channel = chat.get_channel(channel_id)
    if not channel:
        return None

    # Read message history
    messages = chat.get_channel_messages(channel_id, limit=HISTORY_READ_LIMIT)
    if not messages:
        return None

    # Filter noise
    filtered = _filter_messages(messages)
    if not filtered:
        return None

    channel_name = getattr(channel, "name", channel_id)
    channel_desc = getattr(channel, "description", "") or ""

    # Try Tier A: LLM summarization
    result = _try_tier_a(filtered, channel_name, channel_desc)
    if result:
        set_cached_hydration(channel_id, result, "A")
        logger.info(
            "[OK] Hydration Tier A for #%s: %d chars",
            channel_id, len(result),
        )
        return result

    # Try Tier B: Heuristic selection + raw transcript
    result = _try_tier_b(filtered, channel_name, channel_desc)
    if result:
        set_cached_hydration(channel_id, result, "B")
        logger.info(
            "[OK] Hydration Tier B for #%s: %d chars",
            channel_id, len(result),
        )
        return result

    # Tier C: Last 20 messages verbatim
    result = _try_tier_c(filtered, channel_name, channel_desc)
    set_cached_hydration(channel_id, result, "C")
    logger.info(
        "[OK] Hydration Tier C for #%s: %d chars",
        channel_id, len(result),
    )
    return result


# =====================================================================
# Noise filtering
# =====================================================================

def _filter_messages(messages: list) -> list:
    """Remove system messages, very short messages, and noise."""
    out = []
    for msg in messages:
        sender = getattr(msg, "sender", "")
        content = getattr(msg, "content", "")
        if sender in _NOISE_SENDERS:
            continue
        if len(content.strip()) < 10:
            continue
        out.append(msg)
    return out


# =====================================================================
# Transcript builder (shared helper)
# =====================================================================

def _build_transcript(
    messages: list,
    max_chars: int,
    per_msg_limit: int = 800,
) -> str:
    """Build a formatted transcript within a character budget."""
    lines: list[str] = []
    total = 0

    for msg in messages:
        sender = getattr(msg, "sender", "unknown")
        content = getattr(msg, "content", "")
        ts = getattr(msg, "timestamp", "")

        # Extract time portion
        if "T" in ts:
            ts_short = ts.split("T")[1][:5]
        else:
            ts_short = ts[:5] if ts else ""

        # Truncate long messages
        if len(content) > per_msg_limit:
            content = content[:per_msg_limit] + "..."

        line = f"[{ts_short}] {sender}: {content}"

        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line) + 1  # +1 for newline

    return "\n".join(lines)


# =====================================================================
# Tier A: Local LLM summarization
# =====================================================================

def _try_tier_a(
    messages: list,
    channel_name: str,
    channel_desc: str,
) -> Optional[str]:
    """Summarize channel history using the local LLM.

    Returns None if no local LLM is available or inference fails.
    """
    try:
        from cohort.local.router import LocalRouter
        router = LocalRouter()
    except Exception:
        return None

    transcript = _build_transcript(
        messages,
        max_chars=8000,
        per_msg_limit=800,
    )

    prompt = HYDRATION_SUMMARY_PROMPT.format(
        channel_name=channel_name,
        channel_description=channel_desc,
        transcript=transcript,
    )

    result = router.route(
        prompt=prompt,
        task_type="reasoning",
        temperature=0.15,
        response_mode="smart",  # No thinking tokens needed for extraction
    )

    if result is None:
        return None

    # Enforce token budget
    max_chars = TIER_A_TOKEN_BUDGET * CHARS_PER_TOKEN
    text = result.text
    if len(text) > max_chars:
        # Truncate at last complete line
        text = text[:max_chars].rsplit("\n", 1)[0]

    return (
        "=== SESSION CONTEXT (summarized from channel history) ===\n"
        f"{text}\n"
        "=== END SESSION CONTEXT ===\n"
    )


# =====================================================================
# Tier B: Heuristic selection + raw transcript
# =====================================================================

def _try_tier_b(
    messages: list,
    channel_name: str,
    channel_desc: str,
) -> Optional[str]:
    """Select substantive messages heuristically and format as transcript.

    Always succeeds if there are messages. Returns None only if empty.
    """
    if not messages:
        return None

    # Heuristic: take last 50 messages + any earlier messages >200 chars
    recent = messages[-TIER_B_MESSAGE_LIMIT:]
    earlier = messages[:-TIER_B_MESSAGE_LIMIT] if len(messages) > TIER_B_MESSAGE_LIMIT else []

    # Pull in substantive earlier messages
    substantive_earlier = [
        m for m in earlier
        if len(getattr(m, "content", "")) > 200
    ]

    selected = substantive_earlier + recent
    if not selected:
        return None

    max_chars = TIER_B_TOKEN_BUDGET * CHARS_PER_TOKEN
    transcript = _build_transcript(selected, max_chars=max_chars, per_msg_limit=600)

    desc_line = f"\n{channel_desc}\n" if channel_desc else "\n"

    return (
        "=== SESSION CONTEXT (channel history -- please internalize) ===\n"
        f"Channel: #{channel_name}"
        f"{desc_line}\n"
        "This is a continuing conversation. You are joining mid-stream.\n"
        "Read the following history to understand what has been discussed:\n\n"
        f"{transcript}\n"
        "=== END SESSION CONTEXT ===\n"
    )


# =====================================================================
# Tier C: Last 20 messages verbatim
# =====================================================================

def _try_tier_c(
    messages: list,
    channel_name: str,
    channel_desc: str,
) -> str:
    """Format the last N messages as a plain transcript. Always succeeds."""
    recent = messages[-TIER_C_MESSAGE_LIMIT:]
    max_chars = TIER_C_MESSAGE_LIMIT * 400 + 500  # ~8500 chars
    transcript = _build_transcript(recent, max_chars=max_chars, per_msg_limit=400)

    desc_line = f"\n{channel_desc}\n" if channel_desc else "\n"

    return (
        "=== SESSION CONTEXT (recent messages) ===\n"
        f"Channel: #{channel_name}"
        f"{desc_line}\n"
        f"{transcript}\n"
        "=== END SESSION CONTEXT ===\n"
    )
