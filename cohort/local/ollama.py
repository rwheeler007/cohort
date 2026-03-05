"""Ollama HTTP client using only urllib.request and json.

Zero pip dependencies. Localhost-only for security.
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class GenerateResult:
    """Result from an Ollama generate call with token metadata."""

    text: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_seconds: float = 0.0


@dataclass
class ChatResult:
    """Result from an Ollama /api/chat call with tool calling support."""

    content: str = ""  # Text response (empty when tool_calls present)
    tool_calls: list = None  # type: ignore[assignment]  # [{function: {name, arguments}}]
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_seconds: float = 0.0
    done: bool = True

    def __post_init__(self) -> None:
        if self.tool_calls is None:
            self.tool_calls = []


class OllamaClient:
    """Minimal Ollama HTTP client using stdlib only."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        timeout: int = 180,
        health_cache_seconds: int = 60,
    ):
        """Initialize Ollama client.

        Args:
            base_url: Ollama server URL (localhost only for security)
            timeout: Request timeout in seconds
            health_cache_seconds: Cache health check results for this long

        Security:
            base_url must target localhost only (127.0.0.1 or localhost).
            Client refuses to connect to external hosts.
        """
        # D9: Security - localhost only
        if not ("127.0.0.1" in base_url or "localhost" in base_url):
            raise ValueError(f"Ollama base_url must be localhost, got: {base_url}")

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.health_cache_seconds = health_cache_seconds
        self._health_cache: tuple[bool, float] = (False, 0.0)

    def health_check(self) -> bool:
        """Check if Ollama server is healthy.

        Returns:
            True if server is up and responding, False otherwise.

        Caches result for health_cache_seconds to avoid spamming the server.
        """
        # Check cache first
        cached_status, cache_time = self._health_cache
        if time.time() - cache_time < self.health_cache_seconds:
            return cached_status

        # Fetch fresh status
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/tags",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                healthy = resp.status == 200
                self._health_cache = (healthy, time.time())
                return healthy
        except Exception:
            self._health_cache = (False, time.time())
            return False

    def list_models(self) -> list[str]:
        """List available models on the Ollama server.

        Returns:
            List of model names (e.g., ["llama3.2:1b", "qwen3:8b"])
            Empty list if server is down or no models installed.
        """
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/tags",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = data.get("models", [])
                return [m.get("name", "") for m in models if m.get("name")]
        except Exception:
            return []

    def generate(
        self,
        model: str,
        prompt: str,
        temperature: float = 0.4,
        system: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> GenerateResult | None:
        """Generate text completion from Ollama model.

        Args:
            model: Model name (e.g., "qwen3:8b")
            prompt: Input text (user message)
            temperature: Sampling temperature (0.0-1.0)
            system: Optional system prompt (separate from user prompt)
            options: Additional Ollama options (num_ctx, num_predict, etc.)

        Returns:
            GenerateResult with text and token metadata, or None on failure.

        Never raises exceptions -- returns None on any error.
        """
        try:
            t0 = time.monotonic()

            # Build request body
            body: dict[str, Any] = {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "think": False,  # Disable thinking to avoid long internal chains
                "options": {
                    "temperature": temperature,
                    "keep_alive": "5m",  # Keep model warm for follow-up requests
                    **(options or {}),
                },
            }

            if system:
                body["system"] = system

            req = urllib.request.Request(
                f"{self.base_url}/api/generate",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                elapsed = round(time.monotonic() - t0, 1)
                return GenerateResult(
                    text=data.get("response", ""),
                    model=model,
                    tokens_in=data.get("prompt_eval_count", 0),
                    tokens_out=data.get("eval_count", 0),
                    elapsed_seconds=elapsed,
                )

        except Exception:
            # D4: Never raise exceptions -- graceful failure returns None
            return None

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.4,
        system: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> ChatResult | None:
        """Chat completion with native tool calling support.

        Uses /api/chat (messages array) instead of /api/generate.
        When tools are provided, the model may return tool_calls instead
        of text content. Caller is responsible for the tool execution loop.

        Args:
            model: Model name (e.g., "qwen3.5:9b")
            messages: Conversation messages [{role, content, ...}]
            tools: Tool schemas (OpenAI-compatible format)
            temperature: Sampling temperature
            system: Optional system prompt
            options: Additional Ollama options

        Returns:
            ChatResult with content and/or tool_calls, or None on failure.

        Never raises exceptions -- returns None on any error.
        """
        try:
            t0 = time.monotonic()

            body: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "stream": False,
                "think": True,  # Enable thinking for better tool-use reasoning
                "options": {
                    "temperature": temperature,
                    "keep_alive": "5m",
                    **(options or {}),
                },
            }

            if tools:
                body["tools"] = tools

            if system:
                # Prepend system message
                body["messages"] = [
                    {"role": "system", "content": system},
                    *body["messages"],
                ]

            req = urllib.request.Request(
                f"{self.base_url}/api/chat",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                elapsed = round(time.monotonic() - t0, 1)

                msg = data.get("message", {})
                content = msg.get("content", "")
                tool_calls = msg.get("tool_calls", [])
                done = data.get("done", True)

                return ChatResult(
                    content=content,
                    tool_calls=tool_calls,
                    model=model,
                    tokens_in=data.get("prompt_eval_count", 0),
                    tokens_out=data.get("eval_count", 0),
                    elapsed_seconds=elapsed,
                    done=done,
                )

        except Exception:
            # D4: Never raise exceptions -- graceful failure returns None
            return None
