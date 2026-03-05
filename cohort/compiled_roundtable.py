"""Compiled Roundtable -- multi-persona single-call discussion engine.

Instead of N separate LLM calls (one per agent), loads all persona
definitions into a single context and produces all perspectives in
structured rounds.  ~90%+ token reduction for 3-8 agent discussions.

Uses Cohort's own AgentStore for persona loading and LocalRouter's
OllamaClient for inference.  No SMACK or BOSS dependencies.

Usage::

    from cohort.compiled_roundtable import run_compiled_roundtable

    result = run_compiled_roundtable(
        agents=["python_developer", "security_agent", "qa_agent"],
        topic="Should we add connection pooling?",
        context="Prior discussion summary here...",
    )
    # result.agent_responses: dict[str, str]
    # result.synthesis: str | None
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# =========================================================================
# Configuration
# =========================================================================

MAX_AGENTS = 8
TOKEN_BUDGET = 120_000  # ~120K input tokens; fits qwen3.5:9b's 262K with room for output
CHARS_PER_TOKEN = 4
DEFAULT_TEMPERATURE = 0.30
DEFAULT_TIMEOUT = 180
DEFAULT_NUM_PREDICT = 8192
DEFAULT_NUM_CTX = 131_072  # 128K context — safe middle ground for 262K model


# =========================================================================
# Data classes
# =========================================================================

@dataclass
class CompiledResult:
    """Result of a compiled roundtable call."""

    agent_responses: dict[str, str]  # agent_id -> full response text
    synthesis: str | None = None
    raw_response: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


# =========================================================================
# Persona loading (uses AgentStore, not filesystem)
# =========================================================================

def _load_persona(agent_id: str) -> str:
    """Load an agent's persona via AgentStore.

    Fallback chain:
      1. AgentStore persona_text (from agent_persona.md)
      2. AgentStore prompt (first 1000 chars of agent_prompt.md)
      3. Minimal identity string
    """
    try:
        from cohort.agent_store import get_store
        store = get_store()
        if store is not None:
            config = store.get(agent_id)
            if config:
                if config.persona_text:
                    return config.persona_text
                # Try full prompt, truncated
                prompt = store.get_prompt(agent_id)
                if prompt:
                    return prompt[:1000].strip()
                # Build from config fields
                parts = [f"# {config.name}", f"**Role**: {config.role}"]
                if config.personality:
                    parts.append(f"**Personality**: {config.personality}")
                if config.capabilities:
                    parts.append(f"**Capabilities**: {', '.join(config.capabilities[:5])}")
                return "\n".join(parts)
    except Exception as exc:
        logger.debug("Persona load via store failed for %s: %s", agent_id, exc)

    # Try local persona loader as fallback
    try:
        from cohort.personas import load_persona as load_from_file
        text = load_from_file(agent_id)
        if text:
            return text
    except Exception:
        pass

    return f"You are the {agent_id} agent."


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // CHARS_PER_TOKEN


# =========================================================================
# Prompt building
# =========================================================================

def build_compiled_prompt(
    agents: list[str],
    topic: str,
    context: str = "",
    rounds: int = 2,
) -> tuple[str, str, int]:
    """Build system + user prompts for a compiled roundtable.

    Returns:
        (system_prompt, user_prompt, estimated_tokens)

    Raises:
        ValueError: If too many agents or token budget exceeded.
    """
    if len(agents) > MAX_AGENTS:
        raise ValueError(
            f"Compiled roundtable supports max {MAX_AGENTS} agents, got {len(agents)}."
        )

    rounds = min(max(rounds, 1), 3)

    # Load personas
    personas: dict[str, str] = {}
    for agent_id in agents:
        personas[agent_id] = _load_persona(agent_id)

    # Build system prompt
    system_parts = [
        f"You are simulating a focused multi-agent discussion between {len(agents)} specialist agents.",
        "Each agent has a distinct role, personality, and expertise. Stay in character for each one.",
        "Format every response block EXACTLY as shown:",
        "",
        "> **agent_name**:",
        "> [response text]",
        "",
        "Use a blank line between each agent's response block.",
        "",
        "## Participants",
    ]

    for agent_id, persona in personas.items():
        system_parts.append(f"\n### {agent_id}")
        system_parts.append(persona)

    system_prompt = "\n".join(system_parts)

    # Build user prompt
    user_parts = [f"## Topic\n{topic}"]

    if context.strip():
        user_parts.append(f"\n## Context\n{context}")

    user_parts.append("\n## Instructions")

    if rounds >= 1:
        user_parts.append(
            "Round 1 - Initial Positions (150-200 words each):\n"
            "Each agent shares their perspective on the topic. Be specific and opinionated. "
            "Use the markers from your expertise. Challenge assumptions."
        )

    if rounds >= 2:
        user_parts.append(
            "\nRound 2 - Cross-Pollination (100-150 words each):\n"
            "Each agent responds to the most important point raised by another agent. "
            "Name who you are responding to. Build on their idea or push back with evidence."
        )

    if rounds >= 3:
        user_parts.append(
            "\nRound 3 - Convergence (80-120 words each):\n"
            "Each agent states their final position incorporating insights from the discussion."
        )

    user_parts.append(
        "\nSynthesis (50-100 words):\n"
        "Summarize where the group agrees and where productive tension remains. "
        "Format as:\n\n> **synthesis**:\n> [summary text]"
    )

    user_prompt = "\n".join(user_parts)

    # Token estimate
    token_est = (len(system_prompt) + len(user_prompt)) // CHARS_PER_TOKEN

    if token_est > TOKEN_BUDGET:
        raise ValueError(
            f"Estimated {token_est} input tokens exceeds budget of {TOKEN_BUDGET}. "
            f"Reduce agent count (currently {len(agents)}) or shorten context."
        )

    return system_prompt, user_prompt, token_est


# =========================================================================
# Response parsing
# =========================================================================

# Matches "> **agent_name**:" at start of line
_AGENT_BLOCK_RE = re.compile(
    r"^\s*>\s*\*\*(\w+)\*\*\s*:\s*$",
    re.MULTILINE,
)

# Looser: "> agent_name:" (models sometimes drop **)
_AGENT_BLOCK_LOOSE_RE = re.compile(
    r"^\s*>\s*(\w+)\s*:\s*$",
    re.MULTILINE,
)


def parse_compiled_response(
    response_text: str,
    expected_agents: list[str],
) -> tuple[dict[str, str], str | None]:
    """Parse the compiled LLM output into per-agent responses.

    Handles both strict (> **agent**:) and loose (> agent:) formatting.

    Returns:
        (agent_responses dict, synthesis text or None)
    """
    agent_responses: dict[str, str] = {}
    synthesis: str | None = None

    if not response_text.strip():
        return agent_responses, synthesis

    # Find all agent blocks (try strict first, fall back to loose)
    blocks = list(_AGENT_BLOCK_RE.finditer(response_text))
    if len(blocks) < len(expected_agents) // 2:
        blocks = list(_AGENT_BLOCK_LOOSE_RE.finditer(response_text))

    # Extract text between consecutive block headers
    for i, match in enumerate(blocks):
        agent_name = match.group(1).lower().strip()
        start = match.end()
        end = blocks[i + 1].start() if i + 1 < len(blocks) else len(response_text)
        block_text = response_text[start:end].strip()

        # Strip leading ">" quote markers
        lines = []
        for line in block_text.split("\n"):
            cleaned = re.sub(r"^\s*>\s?", "", line)
            lines.append(cleaned)
        block_text = "\n".join(lines).strip()

        if agent_name == "synthesis":
            synthesis = block_text
        else:
            matched = False
            for expected in expected_agents:
                if expected.lower() == agent_name or agent_name in expected.lower():
                    agent_responses[expected] = block_text
                    matched = True
                    break
            if not matched:
                agent_responses[agent_name] = block_text

    for agent_id in expected_agents:
        if agent_id not in agent_responses:
            logger.warning(
                "[COMPILED-DS] Agent '%s' not found in response.",
                agent_id,
            )

    return agent_responses, synthesis


# =========================================================================
# LLM call (uses Cohort's OllamaClient)
# =========================================================================

def _call_ollama(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[str, dict[str, Any]]:
    """Make a single Ollama call for the compiled roundtable.

    Uses Cohort's OllamaClient directly (system prompt support).
    Falls back gracefully if Ollama is unavailable.

    Returns:
        (response_text, metadata_dict)
    """
    from cohort.local.ollama import OllamaClient

    client = OllamaClient(timeout=timeout)

    if not client.health_check():
        return "", {"error": "Ollama is not running or not reachable"}

    # Auto-select model if not specified
    if not model:
        available = client.list_models()
        # Prefer large models for multi-persona generation
        preferred = [
            "qwen3.5:9b",                     # Current primary model
            "qwen3:30b-a3b", "qwen3:30b", "qwen2.5:32b",
            "llama3.1:70b", "llama3.3:70b",
            "qwen3:8b", "llama3.2:3b",
        ]
        model = next((m for m in preferred if m in available), None)
        if not model and available:
            model = available[0]  # last resort: whatever is installed
        if not model:
            return "", {"error": "No Ollama models installed"}

    start = time.time()
    result = client.generate(
        model=model,
        prompt=user_prompt,
        system=system_prompt,
        temperature=temperature,
        think=False,        # No thinking for compiled roundtable -- structured output only
        keep_alive="2m",
        options={
            "num_predict": DEFAULT_NUM_PREDICT,
            "num_ctx": DEFAULT_NUM_CTX,
        },
    )
    elapsed_ms = int((time.time() - start) * 1000)

    if result is None:
        return "", {"error": "Ollama generation returned None", "model": model}

    metadata = {
        "model": model,
        "temperature": temperature,
        "latency_ms": elapsed_ms,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
    }

    return result.text, metadata


# =========================================================================
# Main entry point
# =========================================================================

def run_compiled_roundtable(
    agents: list[str],
    topic: str,
    context: str = "",
    rounds: int = 2,
    model: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
) -> CompiledResult:
    """Run a complete compiled roundtable: build prompt, call LLM, parse.

    Args:
        agents: Agent IDs to participate (loaded via AgentStore).
        topic: Discussion topic.
        context: Optional channel history or background.
        rounds: Discussion rounds (1-3, default 2).
        model: Ollama model (None = auto-select best available).
        temperature: Generation temperature.

    Returns:
        CompiledResult with per-agent responses and metadata.
    """
    try:
        system_prompt, user_prompt, token_est = build_compiled_prompt(
            agents=agents,
            topic=topic,
            context=context,
            rounds=rounds,
        )
    except ValueError as e:
        return CompiledResult(
            agent_responses={},
            error=str(e),
            metadata={"agents": agents, "topic": topic},
        )

    logger.info(
        "[COMPILED-DS] Starting: %d agents, ~%d input tokens, model=%s",
        len(agents), token_est, model or "auto",
    )

    response_text, metadata = _call_ollama(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=model,
        temperature=temperature,
    )

    if metadata.get("error"):
        return CompiledResult(
            agent_responses={},
            raw_response=response_text,
            metadata=metadata,
            error=metadata["error"],
        )

    agent_responses, synthesis = parse_compiled_response(response_text, agents)

    metadata["input_token_estimate"] = token_est
    metadata["agents_expected"] = len(agents)
    metadata["agents_parsed"] = len(agent_responses)
    metadata["has_synthesis"] = synthesis is not None
    metadata["response_chars"] = len(response_text)

    logger.info(
        "[COMPILED-DS] Done: %d/%d agents parsed, synthesis=%s, %dms",
        len(agent_responses), len(agents),
        "yes" if synthesis else "no",
        metadata.get("latency_ms", 0),
    )

    return CompiledResult(
        agent_responses=agent_responses,
        synthesis=synthesis,
        raw_response=response_text,
        metadata=metadata,
    )
