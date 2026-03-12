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

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from cohort.local.config import (
    DEFAULT_MODEL,
    DEFAULT_RESPONSE_MODE,
    DISTILLATION_PARAMS,
    DISTILLATION_PROMPT,
    MIN_CONTENT_CHARS,
    RESPONSE_MODE_PARAMS,
    THINKING_DRAIN_TOKEN_THRESHOLD,
    classify_confidence,
    get_model_for_vram,
    get_temperature,
    get_tier_for_model,
)
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
    pipeline: str = "local"  # "local", "smartest", "smartest-degraded", "claude"
    confidence: str = "high"  # "high" or "guarded" (review task on small model)

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

    def distill(self, raw_output: str) -> str | None:
        """Phase 2 distillation: compress Qwen reasoning into a briefing.

        Args:
            raw_output: Raw text from Phase 1 (Qwen reasoning pass).

        Returns:
            Compressed briefing string, or None on failure.

        Never raises exceptions.
        """
        try:
            if not self._ensure_client() or self._client is None:
                return None

            prompt = DISTILLATION_PROMPT.format(qwen_output=raw_output)

            result = self._client.generate(
                model=DEFAULT_MODEL,
                prompt=prompt,
                temperature=DISTILLATION_PARAMS["temperature"],
                think=DISTILLATION_PARAMS["think"],
                keep_alive=DISTILLATION_PARAMS["keep_alive"],
                options={"num_predict": DISTILLATION_PARAMS["num_predict"]},
            )

            if result is None or not result.text.strip():
                return None

            return result.text.strip()
        except Exception:
            return None

    def route(
        self,
        prompt: str,
        task_type: str | None = None,
        temperature: float | None = None,
        response_mode: str = "smarter",
        system: str | None = None,
    ) -> RouteResult | None:
        """Route prompt to local Ollama model.

        Args:
            prompt: Input text
            task_type: Task type hint (code, reasoning, general, creative)
            temperature: Override temperature (None = use task_type default)
            response_mode: "smart" (no thinking), "smarter" (thinking enabled),
                           or "smartest" (handled by caller, uses smarter params here)
            system: Optional system prompt (grounding rules, etc.)

        Returns:
            RouteResult with text and metadata, or None on failure.

        Never raises exceptions. Returns None if:
        - Ollama server is down
        - Hardware detection failed (CPU-only)
        - Model not installed
        - Generation failed

        If smarter/smartest mode produces an empty response (thinking drain),
        automatically retries in smart mode.

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
                return None

            # Select model based on VRAM
            model = get_model_for_vram(hw_info.vram_mb)

            # Verify model is installed
            if self._client is None:
                return None

            available_models = self._client.list_models()
            if model not in available_models:
                return None

            # Get temperature
            temp = temperature if temperature is not None else get_temperature(task_type)

            # Get response mode parameters
            mode_params = RESPONSE_MODE_PARAMS.get(
                response_mode, RESPONSE_MODE_PARAMS[DEFAULT_RESPONSE_MODE]
            )

            # Generate response
            result = self._client.generate(
                model=model,
                prompt=prompt,
                temperature=temp,
                system=system,
                think=mode_params["think"],
                keep_alive=mode_params["keep_alive"],
                options={"num_predict": mode_params["num_predict"]},
            )

            if result is None:
                return None

            # Thinking drain retry: if smarter/smartest mode produced empty/truncated
            # content (thinking consumed the entire num_predict budget),
            # auto-retry in smart mode (no thinking).
            if (
                response_mode in ("smarter", "smartest")
                and result.tokens_out > THINKING_DRAIN_TOKEN_THRESHOLD
                and len(result.text.strip()) < MIN_CONTENT_CHARS
            ):
                logger.info(
                    "[!] %s mode thinking drain (%d tokens out, %d chars). "
                    "Retrying in smart mode.",
                    response_mode, result.tokens_out, len(result.text.strip()),
                )
                smart_params = RESPONSE_MODE_PARAMS["smart"]
                result = self._client.generate(
                    model=model,
                    prompt=prompt,
                    temperature=temp,
                    system=system,
                    think=smart_params["think"],
                    keep_alive=smart_params["keep_alive"],
                    options={"num_predict": smart_params["num_predict"]},
                )
                if result is None:
                    return None

            tier = get_tier_for_model(model)
            confidence = classify_confidence(
                prompt=prompt, pipeline="local", tier=tier,
                response_mode=response_mode,
            )
            return RouteResult(
                text=result.text,
                model=result.model,
                tier=tier,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                elapsed_seconds=result.elapsed_seconds,
                confidence=confidence,
            )

        except Exception:
            # D4: Catch-all safety net -- never propagate exceptions
            return None

    def route_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_executor: Callable[[str, dict[str, Any]], str],
        temperature: float = 0.4,
        system: str | None = None,
        max_turns: int = 10,
        response_mode: str = "smarter",
    ) -> RouteResult | None:
        """Route a tool-enabled conversation to local Ollama model.

        Implements the agentic tool loop: model generates a response,
        if it contains tool_calls we execute them locally and feed results
        back, repeating until the model produces a final text response
        or max_turns is exhausted.

        Args:
            messages: Conversation messages [{role, content, ...}]
            tools: Tool schemas (OpenAI-compatible for Ollama /api/chat)
            tool_executor: Callable(name, arguments) -> result_string
            temperature: Sampling temperature
            system: Optional system prompt
            max_turns: Maximum tool loop iterations before stopping

        Returns:
            RouteResult with final text, or None on failure.
            Caller should handle None by falling back to Claude CLI.

        Never raises exceptions.
        """
        try:
            if not self._ensure_client():
                return None

            hw_info = self._detect_hardware()
            if hw_info.cpu_only:
                return None

            model = get_model_for_vram(hw_info.vram_mb)

            if self._client is None:
                return None

            available_models = self._client.list_models()
            if model not in available_models:
                return None

            # Tool execution loop
            total_tokens_in = 0
            total_tokens_out = 0
            tools_used: list[str] = []
            turn = 0

            # Work on a mutable copy of messages
            conversation = list(messages)

            mode_params = RESPONSE_MODE_PARAMS.get(
                response_mode, RESPONSE_MODE_PARAMS[DEFAULT_RESPONSE_MODE]
            )

            while turn < max_turns:
                turn += 1

                result = self._client.chat(
                    model=model,
                    messages=conversation,
                    tools=tools,
                    temperature=temperature,
                    system=system if turn == 1 else None,  # System only on first call
                    think=mode_params["think"],
                    keep_alive=mode_params["keep_alive"],
                    options={"num_predict": mode_params["num_predict"]},
                )

                if result is None:
                    logger.warning("[!] Tool loop: Ollama chat returned None on turn %d", turn)
                    return None

                total_tokens_in += result.tokens_in
                total_tokens_out += result.tokens_out

                # Check for tool calls
                if result.tool_calls:
                    # Append assistant message with tool calls
                    conversation.append({
                        "role": "assistant",
                        "content": result.content or "",
                        "tool_calls": result.tool_calls,
                    })

                    # Execute each tool call and append results
                    for tc in result.tool_calls:
                        func = tc.get("function", {})
                        tool_name = func.get("name", "unknown")
                        tool_args = func.get("arguments", {})

                        # Arguments may be a JSON string or a dict
                        if isinstance(tool_args, str):
                            try:
                                tool_args = json.loads(tool_args)
                            except (json.JSONDecodeError, TypeError):
                                tool_args = {}

                        logger.info("[>>] Tool loop turn %d: %s(%s)",
                                    turn, tool_name,
                                    ", ".join(f"{k}={v!r}" for k, v in list(tool_args.items())[:3]))

                        tool_result = tool_executor(tool_name, tool_args)
                        tools_used.append(tool_name)

                        conversation.append({
                            "role": "tool",
                            "content": tool_result,
                        })

                    # Continue loop -- model needs to process tool results
                    continue

                # No tool calls -- model produced a final text response
                if result.content:
                    tier = get_tier_for_model(model)
                    elapsed = result.elapsed_seconds

                    if tools_used:
                        logger.info(
                            "[OK] Tool loop completed: %d turns, tools: %s",
                            turn, ", ".join(tools_used),
                        )

                    # Extract user message for confidence classification
                    user_text = ""
                    for m in messages:
                        if m.get("role") == "user":
                            user_text = m.get("content", "")
                    confidence = classify_confidence(
                        prompt=user_text, pipeline="local", tier=tier,
                        response_mode=response_mode,
                    )

                    return RouteResult(
                        text=result.content,
                        model=model,
                        tier=tier,
                        tokens_in=total_tokens_in,
                        tokens_out=total_tokens_out,
                        elapsed_seconds=elapsed,
                        confidence=confidence,
                    )

                # No content and no tool calls -- unexpected, bail out
                logger.warning("[!] Tool loop: empty response on turn %d", turn)
                return None

            # Max turns exhausted
            logger.warning("[!] Tool loop exhausted max_turns=%d, tools used: %s",
                           max_turns, ", ".join(tools_used))
            # Return whatever content we accumulated (if any)
            return None

        except Exception:
            # D4: Catch-all safety net -- never propagate exceptions
            return None
