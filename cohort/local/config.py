"""Local router configuration.

Hardcoded VRAM-tier model mapping, task-specific temperatures,
and response mode presets (smart/smarter/smartest).

Model tier settings are user-configurable via tier_settings.json.
Each tier (smart/smarter/smartest) has a primary and fallback model.
Zero external dependencies -- pure Python data structures.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Literal, TypedDict

logger = logging.getLogger(__name__)


class ModelMapping(TypedDict):
    """VRAM tier -> recommended model."""

    vram_range: tuple[int, int]  # MB range (min, max)
    model: str
    tier: int


# =====================================================================
# Default Model
# =====================================================================
# Single source of truth for the primary model name.
# All fallbacks and preference lists reference this constant.

DEFAULT_MODEL = "qwen3.5:9b"


# =====================================================================
# VRAM-Tier Model Mapping
# =====================================================================
# Maps available VRAM to recommended model.
# Sorted by increasing VRAM requirement.

VRAM_TIER_MODELS: list[ModelMapping] = [
    {
        "vram_range": (0, 4096),  # <4GB VRAM
        "model": "qwen3.5:2b",
        "tier": 1,
    },
    {
        "vram_range": (4096, 6144),  # 4-6GB VRAM
        "model": "qwen3.5:4b",
        "tier": 2,
    },
    {
        "vram_range": (6144, 10240),  # 6-10GB VRAM
        "model": "qwen3.5:9b",
        "tier": 3,
    },
    {
        "vram_range": (10240, 999999),  # 10GB+ VRAM
        "model": "qwen3.5:9b",
        "tier": 4,
    },
]


# =====================================================================
# Task-Specific Temperatures
# =====================================================================
# Conservative defaults prevent hallucination.
# Lower temp = more deterministic, higher = more creative.

TASK_TEMPERATURES: dict[str, float] = {
    "code": 0.15,  # Code generation: very deterministic
    "reasoning": 0.3,  # Reasoning tasks: moderate determinism
    "general": 0.4,  # General chat: balanced
    "creative": 0.7,  # Creative writing: more variation
}

DEFAULT_TEMPERATURE = 0.4


# =====================================================================
# Model Descriptions (for setup wizard display)
# =====================================================================

MODEL_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "qwen3.5:2b": {
        "size": "~2.7 GB",
        "summary": "Lightweight multimodal model with 256K context, fast on any hardware",
    },
    "qwen3.5:4b": {
        "size": "~3.4 GB",
        "summary": "Compact multimodal model with 256K context, good balance of speed and quality",
    },
    "qwen3.5:9b": {
        "size": "~6.6 GB",
        "summary": "Strong reasoning and coding with 256K context, great for most tasks",
    },
}


# =====================================================================
# Response Mode Configuration
# =====================================================================
# Three tiers: Smart (fast), Smarter (thinking, default), Smartest (Qwen+Claude).
# Smartest requires Claude CLI -- gated at runtime.

ResponseMode = Literal["smart", "smarter", "smartest"]

RESPONSE_MODE_PARAMS: dict[str, dict] = {
    "smart": {
        "think": False,
        "num_predict": 4096,    # 4K budget: response tokens only
        "keep_alive": "2m",
    },
    "smarter": {
        "think": True,
        "num_predict": 16384,   # 16K budget: ~8K thinking + ~8K response
        "keep_alive": "2m",
    },
    "smartest": {
        "think": True,
        "num_predict": 16384,   # Phase 1 params (same as smarter)
        "keep_alive": "2m",
    },
}

DEFAULT_RESPONSE_MODE: ResponseMode = "smarter"
DEFAULT_KEEP_ALIVE = "2m"

# =====================================================================
# Distillation Configuration (Phase 2 of Smartest pipeline)
# =====================================================================
# Qwen compresses its own reasoning into a structured briefing for Claude.

DISTILLATION_PARAMS: dict = {
    "think": False,
    "num_predict": 8192,
    "keep_alive": "2m",
    "temperature": 0.15,        # Very deterministic extraction
}

DISTILLATION_PROMPT = (
    "You are distilling an AI agent's detailed analysis into a briefing "
    "for a senior AI model (Claude). Your job is to preserve ALL substantive "
    "content while stripping noise. Remove meta-commentary, hedging, filler, "
    "and repetition -- but keep every concrete fact, data point, code snippet, "
    "specific recommendation, and technical detail.\n\n"
    "Scale your output to match the substance:\n"
    "- Simple question with a clear answer: 50-200 words\n"
    "- Moderate analysis with several points: 200-1000 words\n"
    "- Complex multi-faceted analysis: 1000-5000 words\n"
    "Never pad short answers. Never truncate rich analysis.\n\n"
    "Output these sections (skip any that are empty):\n\n"
    "### Key Findings\n"
    "- Core observations, conclusions, data points, and code examples\n\n"
    "### Recommended Approach\n"
    "- Specific actions, implementations, or solutions proposed\n\n"
    "### Constraints & Caveats\n"
    "- Limitations, risks, or things explicitly noted to avoid\n\n"
    "### Confidence Assessment\n"
    "- One line: High/Medium/Low and why\n\n"
    "--- ORIGINAL ANALYSIS ---\n"
    "{qwen_output}\n"
    "--- END ---\n\n"
    "Distilled briefing:"
)

# =====================================================================
# Smartest Claude Prompt (Phase 3 of Smartest pipeline)
# =====================================================================
# Claude gets persona + distilled briefing, NOT full channel history.

SMARTEST_CLAUDE_PROMPT = (
    "You are responding as the {agent_id} agent.\n\n"
    "Follow this persona exactly:\n"
    "---\n{persona}\n---\n\n"
    "{grounding_rules}\n\n"
    "A local AI model has analyzed the conversation and produced this briefing:\n\n"
    "--- ANALYSIS BRIEFING ---\n"
    "{distilled_briefing}\n"
    "--- END BRIEFING ---\n\n"
    "Now respond to the user's message. Use the briefing as your research/context, "
    "but write your response in your own voice. Do not reference the briefing or "
    "the local model's analysis.\n\n"
    "User message:\n{user_message}"
)

# =====================================================================
# Roundtable Model Preferences
# =====================================================================
# Auto-selection fallback order for compiled roundtable when no model
# is explicitly provided. First match against installed models wins.

ROUNDTABLE_MODEL_PREFERENCES: list[str] = [
    DEFAULT_MODEL,
    "qwen3.5:35b-a3b", "qwen3.5:4b",
    "qwen3.5:2b",
]

# Empty-response retry: if tokens_out exceeds this but visible content
# is shorter than MIN_CONTENT_CHARS, the model spent all tokens thinking.
THINKING_DRAIN_TOKEN_THRESHOLD = 200
MIN_CONTENT_CHARS = 20


# =====================================================================
# Configurable Model Tier Settings
# =====================================================================
# Each tier can be independently configured with a primary model and
# fallback. "cloud_api" as a model name routes to the user's cloud
# API key (Anthropic/OpenAI). Settings persist to tier_settings.json.

TIER_SETTINGS_PATH = Path(__file__).parent.parent / "data" / "tier_settings.json"

DEFAULT_TIER_SETTINGS: dict[str, dict[str, str | None]] = {
    "smart": {
        "primary": "local",       # Uses VRAM-detected model, no thinking
        "fallback": None,
    },
    "smarter": {
        "primary": "local",       # Uses VRAM-detected model, thinking on
        "fallback": "smart",      # Degrade to smart on failure
    },
    "smartest": {
        "primary": "qwen3.5:35b-a3b",  # Local 35B MoE escalation model
        "fallback": "cloud_api",        # Cloud API if 35B unavailable
    },
}


def get_tier_settings() -> dict[str, dict[str, str | None]]:
    """Load tier settings from disk, falling back to defaults.

    Returns:
        Dict with keys "smart", "smarter", "smartest", each containing
        "primary" and "fallback" model identifiers.
    """
    settings = dict(DEFAULT_TIER_SETTINGS)
    try:
        if TIER_SETTINGS_PATH.is_file():
            with open(TIER_SETTINGS_PATH) as f:
                user_settings = json.load(f)
            # Merge: user settings override defaults per-tier
            for tier in ("smart", "smarter", "smartest"):
                if tier in user_settings:
                    settings[tier] = {**settings[tier], **user_settings[tier]}
    except Exception as e:
        logger.warning("Failed to load tier settings: %s (using defaults)", e)
    return settings


def save_tier_settings(settings: dict[str, dict[str, str | None]]) -> bool:
    """Save tier settings to disk.

    Returns:
        True if saved successfully.
    """
    try:
        TIER_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(TIER_SETTINGS_PATH, "w") as f:
            json.dump(settings, f, indent=2)
        return True
    except Exception as e:
        logger.warning("Failed to save tier settings: %s", e)
        return False


def get_smartest_model() -> str:
    """Get the configured primary model for the Smartest tier.

    Returns:
        Model name (e.g., "qwen3.5:35b-a3b"), "cloud_api", or "local".
    """
    settings = get_tier_settings()
    return settings.get("smartest", {}).get("primary", "qwen3.5:35b-a3b")


def get_smartest_fallback() -> str | None:
    """Get the configured fallback for the Smartest tier.

    Returns:
        "cloud_api", "smarter", model name, or None.
    """
    settings = get_tier_settings()
    return settings.get("smartest", {}).get("fallback", "cloud_api")


def get_model_for_vram(vram_mb: int) -> str:
    """Get recommended model for available VRAM.

    Args:
        vram_mb: Available VRAM in megabytes

    Returns:
        Model name (e.g., "qwen3.5:9b")
    """
    for mapping in VRAM_TIER_MODELS:
        min_vram, max_vram = mapping["vram_range"]
        if min_vram <= vram_mb < max_vram:
            return mapping["model"]

    # Fallback: smallest model
    return VRAM_TIER_MODELS[0]["model"]


def get_tier_for_model(model_name: str) -> int | None:
    """Get the tier number for a model name.

    Args:
        model_name: Model name (e.g., "qwen3.5:9b")

    Returns:
        Tier number (1-4) or None if model not in mapping.
    """
    for mapping in VRAM_TIER_MODELS:
        if mapping["model"] == model_name:
            return mapping["tier"]
    return None


def get_temperature(task_type: str | None = None) -> float:
    """Get temperature for task type.

    Args:
        task_type: Task type (code, reasoning, general, creative)

    Returns:
        Temperature value (0.0-1.0)
    """
    if task_type is None:
        return DEFAULT_TEMPERATURE
    return TASK_TEMPERATURES.get(task_type.lower(), DEFAULT_TEMPERATURE)


# =====================================================================
# Confidence Classification
# =====================================================================
# Classifies response confidence based on task type, pipeline, and model
# tier. Small local models are reliable for generation but unreliable
# for review/synthesis tasks (correcting, critiquing, orchestrating).
# This is a structural signal, not a self-assessment by the model.

# Prompt keywords that indicate review/synthesis tasks --
# where small models hallucinate corrections confidently.
_REVIEW_KEYWORDS: set[str] = {
    "review", "critique", "correct", "evaluate", "assess",
    "what did they miss", "what's wrong", "find issues",
    "compare", "verify", "validate", "fact-check", "audit",
    "improve this", "fix this", "what errors",
}

# Model size threshold: models at or below this tier are flagged
# for review tasks. Tier 3 = qwen3.5:9b, Tier 4 = qwen3.5:9b (10GB+).
_SMALL_MODEL_MAX_TIER = 4


def classify_confidence(
    prompt: str,
    pipeline: str,
    tier: int | None,
    response_mode: str = "smarter",
) -> str:
    """Classify confidence level of a response.

    Returns:
        "high" - Claude pipeline or local model on generation tasks
        "guarded" - Local model on review/synthesis tasks (hallucination risk)

    The classification is based on task type detection (keyword matching
    on the prompt) and the pipeline used. Claude-backed responses are
    always "high". Local model responses are "guarded" when the prompt
    indicates a review, critique, or correction task.
    """
    # Claude-backed pipelines are always high confidence
    if pipeline in ("smartest", "claude"):
        return "high"

    # Local model: check if the task is review/synthesis
    if tier is not None and tier <= _SMALL_MODEL_MAX_TIER:
        prompt_lower = prompt.lower()
        for keyword in _REVIEW_KEYWORDS:
            if keyword in prompt_lower:
                return "guarded"

    return "high"
