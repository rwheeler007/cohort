"""Local LLM router for Cohort.

Routes prompts to local Ollama models when available. Falls back to None
on any failure (caller handles fallback to Claude CLI).

Design philosophy:
- Zero-dependency (stdlib only)
- Never raises exceptions (returns None on failure)
- Invisible to end user (no error messages, no config required)
- Security: localhost-only HTTP calls, no user input in shell commands
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from cohort.local.config import get_model_for_vram, get_temperature, get_tier_for_model
from cohort.local.detect import detect_hardware
from cohort.local.ollama import OllamaClient


@dataclass
class RouteResult:
    """Result from local routing with model metadata."""

    text: str
    model: str = ""
    tier: int | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_seconds: float = 0.0

logger = logging.getLogger(__name__)


class LocalRouter:
    """Local LLM routing with hardware detection and Ollama HTTP client.

    Singleton-like pattern: creates client on first use, caches hardware info.
    """

    def __init__(self) -> None:
        """Initialize router (lazy -- doesn't connect until first route call)."""
        self._client: OllamaClient | None = None
        self._hardware_info: Any = None  # HardwareInfo, but avoid import cycle
        self._available: bool | None = None  # None = not checked yet

    def _ensure_client(self) -> bool:
        """Ensure Ollama client is initialized and healthy.

        Returns:
            True if client is available and healthy, False otherwise.

        Caches result to avoid repeated health checks.
        """
        # Already checked and failed
        if self._available is False:
            return False

        # First check or re-check
        if self._client is None:
            try:
                self._client = OllamaClient()
            except Exception:
                # Client creation failed (e.g., invalid URL)
                self._available = False
                return False

        # Health check
        try:
            healthy = self._client.health_check()
            self._available = healthy
            return healthy
        except Exception:
            self._available = False
            return False

    def _detect_hardware(self) -> Any:
        """Detect hardware (cached after first call).

        Returns:
            HardwareInfo instance
        """
        if self._hardware_info is None:
            try:
                self._hardware_info = detect_hardware()
            except Exception:
                # Fallback to CPU-only on detection failure
                from cohort.local.detect import HardwareInfo

                self._hardware_info = HardwareInfo(cpu_only=True, platform="unknown")
        return self._hardware_info

    def route(
        self,
        prompt: str,
        task_type: str | None = None,
        temperature: float | None = None,
    ) -> RouteResult | None:
        """Route prompt to local Ollama model.

        Args:
            prompt: Input text
            task_type: Task type hint (code, reasoning, general, creative)
            temperature: Override temperature (None = use task_type default)

        Returns:
            RouteResult with text and metadata, or None on failure.

        Never raises exceptions. Returns None if:
        - Ollama server is down
        - Hardware detection failed (CPU-only)
        - Model not installed
        - Generation failed

        Caller should handle None by falling back to Claude CLI.
        """
        # D4: Never raise exceptions -- graceful failure returns None
        try:
            # Check if Ollama is available
            if not self._ensure_client():
                return None

            # Detect hardware to select model
            hw_info = self._detect_hardware()
            if hw_info.cpu_only:
                # No GPU available -- skip local routing
                # (could support CPU-only models in future, but skip for now)
                return None

            # Select model based on VRAM
            model = get_model_for_vram(hw_info.vram_mb)

            # Verify model is installed
            if self._client is None:
                return None

            available_models = self._client.list_models()
            if model not in available_models:
                # Requested model not installed -- fallback to Claude
                return None

            # Get temperature
            temp = temperature if temperature is not None else get_temperature(task_type)

            # Generate response
            result = self._client.generate(
                model=model,
                prompt=prompt,
                temperature=temp,
            )

            if result is None:
                return None

            tier = get_tier_for_model(model)
            return RouteResult(
                text=result.text,
                model=result.model,
                tier=tier,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                elapsed_seconds=result.elapsed_seconds,
            )

        except Exception:
            # D4: Catch-all safety net -- never propagate exceptions
            return None
