"""Cloud LLM backend for Cohort Smartest pipeline.

Provider-agnostic: supports Anthropic, OpenAI, and any OpenAI-compatible
endpoint. Users bring their own API key and choose their provider.

In dev mode, the CLI subprocess path remains available for internal testing.
In distribution (dev mode off), only the cloud API path is exposed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CloudResponse:
    """Result from a cloud LLM call."""

    text: str
    tokens_in: int
    tokens_out: int
    model: str
    elapsed_seconds: float


# =====================================================================
# Provider registry
# =====================================================================

_PROVIDERS: dict[str, type[CloudBackend]] = {}


def _register(name: str):
    """Decorator to register a provider class."""
    def wrapper(cls):
        _PROVIDERS[name] = cls
        return cls
    return wrapper


class CloudBackend:
    """Base class for cloud LLM providers."""

    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def complete(self, system_prompt: str, user_message: str, temperature: float | None = None) -> CloudResponse:
        raise NotImplementedError


@_register("anthropic")
class AnthropicBackend(CloudBackend):
    """Anthropic Messages API."""

    def complete(self, system_prompt: str, user_message: str, temperature: float | None = None) -> CloudResponse:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        t0 = time.monotonic()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        response = client.messages.create(**kwargs)
        elapsed = round(time.monotonic() - t0, 1)
        return CloudResponse(
            text=response.content[0].text,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            model=response.model,
            elapsed_seconds=elapsed,
        )


@_register("openai")
class OpenAIBackend(CloudBackend):
    """OpenAI Chat Completions API (also works for any compatible endpoint)."""

    def complete(self, system_prompt: str, user_message: str, temperature: float | None = None) -> CloudResponse:
        import openai

        client_kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        client = openai.OpenAI(**client_kwargs)
        t0 = time.monotonic()
        req_kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }
        if temperature is not None:
            req_kwargs["temperature"] = temperature
        response = client.chat.completions.create(**req_kwargs)
        elapsed = round(time.monotonic() - t0, 1)
        choice = response.choices[0]
        usage = response.usage
        return CloudResponse(
            text=choice.message.content or "",
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
            model=response.model or self.model,
            elapsed_seconds=elapsed,
        )


# =====================================================================
# Factory
# =====================================================================

# Default models per provider (user can override in settings)
_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
}


def get_cloud_backend(settings: dict[str, Any]) -> CloudBackend | None:
    """Build a CloudBackend from Cohort settings.

    Required settings keys:
        cloud_provider: "anthropic" | "openai"
        cloud_api_key: the user's API key

    Optional:
        cloud_model: model name override
        cloud_base_url: custom endpoint (OpenAI-compatible)

    Returns None if provider/key not configured.
    """
    provider = settings.get("cloud_provider", "").strip().lower()
    api_key = settings.get("cloud_api_key", "").strip()

    if not provider or not api_key:
        return None

    backend_cls = _PROVIDERS.get(provider)
    if backend_cls is None:
        logger.warning("[!] Unknown cloud provider: %s", provider)
        return None

    model = (
        settings.get("cloud_model", "").strip()
        or _DEFAULT_MODELS.get(provider, "")
    )
    if not model:
        logger.warning("[!] No model configured for cloud provider %s", provider)
        return None

    base_url = settings.get("cloud_base_url", "").strip() or None

    return backend_cls(api_key=api_key, model=model, base_url=base_url)


def check_cloud_available(settings: dict[str, Any]) -> bool:
    """Quick check: is a cloud backend configured?"""
    provider = settings.get("cloud_provider", "").strip()
    api_key = settings.get("cloud_api_key", "").strip()
    return bool(provider and api_key)


def list_providers() -> list[str]:
    """Return registered provider names."""
    return sorted(_PROVIDERS.keys())
