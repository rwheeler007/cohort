"""Cohort Local LLM MCP Server -- Ollama inference as a Claude Code tool.

Exposes local Ollama model inference via MCP so Claude CLI agents can
delegate specific sub-tasks to local models.  Useful for:

- Code generation drafts (cheap, fast, no API cost)
- Data transformation (regex extraction, formatting)
- Security scanning with local models
- Any task where a local 8B-30B model is sufficient

Usage::

    python -m cohort.mcp.local_llm_server       # stdio transport
    fastmcp dev cohort/mcp/local_llm_server.py   # MCP inspector
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from cohort.local.ollama import OllamaClient

logger = logging.getLogger(__name__)

# =====================================================================
# Server
# =====================================================================

mcp = FastMCP("cohort_local_llm")

_client = OllamaClient()


# =====================================================================
# Input models
# =====================================================================


class GenerateInput(BaseModel):
    """Input for local LLM text generation."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    prompt: str = Field(
        ...,
        description="The prompt to send to the local model.",
        min_length=1,
        max_length=50000,
    )
    model: str = Field(
        default="",
        description=(
            "Model name (e.g. 'qwen3:8b', 'qwen3:30b-a3b'). "
            "Empty string = auto-select based on GPU hardware."
        ),
        max_length=100,
    )
    temperature: float = Field(
        default=0.4,
        ge=0.0,
        le=2.0,
        description="Sampling temperature (0.0 = deterministic, higher = more creative).",
    )
    task_type: str = Field(
        default="general",
        description="Task hint for auto model/temp selection: code, reasoning, general, creative.",
        max_length=20,
    )


class ListModelsInput(BaseModel):
    """Input for listing available local models."""

    model_config = ConfigDict(extra="forbid")


# =====================================================================
# Tools
# =====================================================================


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": False,
    }
)
def local_llm_generate(params: GenerateInput) -> str:
    """Generate text using a local Ollama model.

    Returns the generated text with model metadata.  Use this for cheap,
    fast sub-tasks that don't need frontier model quality -- code drafts,
    data extraction, formatting, classification.

    Auto-selects the best model for your GPU if model is left empty.
    """
    # Auto-select model if not specified
    model = params.model.strip()
    if not model:
        try:
            from cohort.local.config import get_model_for_vram
            from cohort.local.detect import detect_hardware

            hw = detect_hardware()
            if hw.cpu_only:
                return "Error: No GPU detected. Local LLM requires a GPU with VRAM."
            model = get_model_for_vram(hw.vram_mb)
        except Exception as exc:
            return f"Error: Hardware detection failed: {exc}"

    # Health check
    if not _client.health_check():
        return "Error: Ollama server is not running. Start it with: ollama serve"

    # Check model availability
    available = _client.list_models()
    if model not in available:
        avail_str = ", ".join(available[:5]) if available else "(none installed)"
        return f"Error: Model '{model}' not installed. Available: {avail_str}"

    # Resolve temperature from task_type if using default
    temp = params.temperature
    if params.temperature == 0.4 and params.task_type != "general":
        try:
            from cohort.local.config import get_temperature
            temp = get_temperature(params.task_type)
        except Exception:
            pass

    # Generate
    result = _client.generate(
        model=model,
        prompt=params.prompt,
        temperature=temp,
    )

    if result is None:
        return "Error: Generation failed. Check Ollama server logs."

    return (
        f"{result.text}\n\n---\n"
        f"[Model: {result.model}, "
        f"Tokens: {result.tokens_in}/{result.tokens_out}, "
        f"Time: {result.elapsed_seconds}s]"
    )


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": False,
    }
)
def local_llm_models(params: ListModelsInput) -> str:
    """List locally available Ollama models with their tier mapping.

    Shows installed models, their approximate size, and which VRAM
    tier they belong to.
    """
    if not _client.health_check():
        return "Error: Ollama server is not running. Start it with: ollama serve"

    models = _client.list_models()
    if not models:
        return "No models installed. Pull one with: ollama pull qwen3:8b"

    try:
        from cohort.local.config import MODEL_DESCRIPTIONS, get_tier_for_model
    except ImportError:
        MODEL_DESCRIPTIONS = {}  # type: ignore[assignment]

        def get_tier_for_model(m: str) -> int | None:  # type: ignore[misc]
            return None

    lines = ["Available local models:"]
    for m in sorted(models):
        tier = get_tier_for_model(m)
        desc = MODEL_DESCRIPTIONS.get(m, {})
        size = desc.get("size", "?")
        summary = desc.get("summary", "")
        tier_str = f"Tier {tier}" if tier else "Custom"
        lines.append(f"  - {m} ({size}, {tier_str}) {summary}")

    return "\n".join(lines)


# =====================================================================
# Entry point
# =====================================================================

if __name__ == "__main__":
    mcp.run()
