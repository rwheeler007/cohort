"""Ollama HTTP client using only urllib.request and json.

Zero pip dependencies. Localhost-only for security.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any


class OllamaClient:
    """Minimal Ollama HTTP client using stdlib only."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        timeout: int = 120,
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
        options: dict[str, Any] | None = None,
    ) -> str | None:
        """Generate text completion from Ollama model.

        Args:
            model: Model name (e.g., "qwen3:8b")
            prompt: Input text
            temperature: Sampling temperature (0.0-1.0)
            options: Additional Ollama options (num_ctx, num_predict, etc.)

        Returns:
            Generated text or None on failure.

        Never raises exceptions -- returns None on any error.
        """
        try:
            # Build request body
            body = {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "keep_alive": "0",  # Immediate unload after generation
                    **(options or {}),
                },
            }

            req = urllib.request.Request(
                f"{self.base_url}/api/generate",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("response", "")

        except Exception:
            # D4: Never raise exceptions -- graceful failure returns None
            return None
