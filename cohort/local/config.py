"""Local router configuration.

Hardcoded VRAM-tier model mapping, task-specific temperatures,
and response mode presets (smart/quick).
Zero external dependencies -- pure Python data structures.
"""

from __future__ import annotations

from typing import Literal, TypedDict


class ModelMapping(TypedDict):
    """VRAM tier -> recommended model."""

    vram_range: tuple[int, int]  # MB range (min, max)
    model: str
    tier: int


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
# "Smart by default": thinking enabled, high token budget.
# "Quick" is opt-in for speed/cost savings.

ResponseMode = Literal["smart", "quick"]

RESPONSE_MODE_PARAMS: dict[str, dict] = {
    "smart": {
        "think": True,
        "num_predict": 16384,   # 16K budget: ~8K thinking + ~8K response
        "keep_alive": "2m",
    },
    "quick": {
        "think": False,
        "num_predict": 4096,    # 4K budget: response tokens only
        "keep_alive": "2m",
    },
}

DEFAULT_RESPONSE_MODE: ResponseMode = "smart"
DEFAULT_KEEP_ALIVE = "2m"

# Empty-response retry: if tokens_out exceeds this but visible content
# is shorter than MIN_CONTENT_CHARS, the model spent all tokens thinking.
THINKING_DRAIN_TOKEN_THRESHOLD = 200
MIN_CONTENT_CHARS = 20


def get_model_for_vram(vram_mb: int) -> str:
    """Get recommended model for available VRAM.

    Args:
        vram_mb: Available VRAM in megabytes

    Returns:
        Model name (e.g., "qwen3:8b")
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
        model_name: Model name (e.g., "qwen3:8b")

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
