"""Local router configuration.

Hardcoded VRAM-tier model mapping and task-specific temperatures.
Zero external dependencies -- pure Python data structures.
"""

from __future__ import annotations

from typing import TypedDict


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
        "model": "llama3.2:1b",
        "tier": 1,
    },
    {
        "vram_range": (4096, 6144),  # 4-6GB VRAM
        "model": "gemma3:4b",
        "tier": 2,
    },
    {
        "vram_range": (6144, 8192),  # 6-8GB VRAM
        "model": "qwen3:8b",
        "tier": 3,
    },
    {
        "vram_range": (8192, 999999),  # 8GB+ VRAM
        "model": "qwen3:30b-a3b",
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
