"""Tests for Local LLM MCP server (cohort.mcp.local_llm_server).

Uses mocking -- no real Ollama or GPU required.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cohort.local.detect import HardwareInfo
from cohort.local.ollama import GenerateResult

# =====================================================================
# Helpers
# =====================================================================


def _make_generate_input(**kwargs):
    """Create a GenerateInput instance with defaults."""
    from cohort.mcp.local_llm_server import GenerateInput

    defaults = {
        "prompt": "Write a hello world function",
        "model": "qwen3:8b",
        "temperature": 0.4,
        "task_type": "general",
    }
    defaults.update(kwargs)
    return GenerateInput(**defaults)


def _make_list_models_input():
    """Create a ListModelsInput instance."""
    from cohort.mcp.local_llm_server import ListModelsInput

    return ListModelsInput()


# =====================================================================
# local_llm_generate tests
# =====================================================================


class TestLocalLLMGenerate:
    def test_generate_with_valid_model(self):
        from cohort.mcp.local_llm_server import _client, local_llm_generate

        mock_result = GenerateResult(
            text="def hello(): print('Hello!')",
            model="qwen3:8b",
            tokens_in=20,
            tokens_out=10,
            elapsed_seconds=1.5,
        )

        with patch.object(_client, "health_check", return_value=True), \
             patch.object(_client, "list_models", return_value=["qwen3:8b"]), \
             patch.object(_client, "generate", return_value=mock_result):
            result = local_llm_generate(_make_generate_input(model="qwen3:8b"))

        assert "def hello()" in result
        assert "qwen3:8b" in result
        assert "Tokens: 20/10" in result

    def test_generate_auto_selects_model(self):
        from cohort.mcp.local_llm_server import _client, local_llm_generate

        hw = HardwareInfo(
            gpu_name="RTX 3080",
            vram_mb=12288,
            cpu_only=False,
            platform="linux",
        )
        mock_result = GenerateResult(
            text="auto-selected output",
            model="qwen3.5:9b",
            tokens_in=10,
            tokens_out=5,
            elapsed_seconds=2.0,
        )

        with patch("cohort.local.detect.detect_hardware", return_value=hw), \
             patch.object(_client, "health_check", return_value=True), \
             patch.object(_client, "list_models", return_value=["qwen3.5:9b"]), \
             patch.object(_client, "generate", return_value=mock_result):
            result = local_llm_generate(_make_generate_input(model=""))

        assert "auto-selected output" in result
        assert "qwen3.5:9b" in result

    def test_generate_cpu_only_returns_error(self):
        from cohort.mcp.local_llm_server import local_llm_generate

        hw = HardwareInfo(cpu_only=True, platform="linux")

        with patch("cohort.local.detect.detect_hardware", return_value=hw):
            result = local_llm_generate(_make_generate_input(model=""))

        assert "Error" in result
        assert "No GPU" in result

    def test_generate_ollama_down_returns_error(self):
        from cohort.mcp.local_llm_server import _client, local_llm_generate

        with patch.object(_client, "health_check", return_value=False):
            result = local_llm_generate(_make_generate_input())

        assert "Error" in result
        assert "not running" in result

    def test_generate_model_not_installed_returns_error(self):
        from cohort.mcp.local_llm_server import _client, local_llm_generate

        with patch.object(_client, "health_check", return_value=True), \
             patch.object(_client, "list_models", return_value=["phi3:mini"]):
            result = local_llm_generate(_make_generate_input(model="qwen3:8b"))

        assert "Error" in result
        assert "not installed" in result
        assert "phi3:mini" in result

    def test_generate_failure_returns_error(self):
        from cohort.mcp.local_llm_server import _client, local_llm_generate

        with patch.object(_client, "health_check", return_value=True), \
             patch.object(_client, "list_models", return_value=["qwen3:8b"]), \
             patch.object(_client, "generate", return_value=None):
            result = local_llm_generate(_make_generate_input())

        assert "Error" in result
        assert "failed" in result

    def test_generate_task_type_adjusts_temperature(self):
        from cohort.mcp.local_llm_server import _client, local_llm_generate

        mock_result = GenerateResult(
            text="code output", model="qwen3:8b",
            tokens_in=10, tokens_out=5, elapsed_seconds=1.0,
        )

        with patch.object(_client, "health_check", return_value=True), \
             patch.object(_client, "list_models", return_value=["qwen3:8b"]), \
             patch.object(_client, "generate", return_value=mock_result) as mock_gen:
            local_llm_generate(_make_generate_input(task_type="code"))

        # Should have used code temperature (0.15) instead of default (0.4)
        call_kwargs = mock_gen.call_args
        assert call_kwargs[1]["temperature"] == 0.15


# =====================================================================
# local_llm_models tests
# =====================================================================


class TestLocalLLMModels:
    def test_list_models_returns_formatted(self):
        from cohort.mcp.local_llm_server import _client, local_llm_models

        with patch.object(_client, "health_check", return_value=True), \
             patch.object(_client, "list_models", return_value=["qwen3:8b", "qwen3:30b-a3b"]):
            result = local_llm_models(_make_list_models_input())

        assert "Available local models" in result
        assert "qwen3:8b" in result
        assert "qwen3:30b-a3b" in result

    def test_list_models_ollama_down(self):
        from cohort.mcp.local_llm_server import _client, local_llm_models

        with patch.object(_client, "health_check", return_value=False):
            result = local_llm_models(_make_list_models_input())

        assert "Error" in result
        assert "not running" in result

    def test_list_models_empty(self):
        from cohort.mcp.local_llm_server import _client, local_llm_models

        with patch.object(_client, "health_check", return_value=True), \
             patch.object(_client, "list_models", return_value=[]):
            result = local_llm_models(_make_list_models_input())

        assert "No models installed" in result


# =====================================================================
# Input validation tests
# =====================================================================


class TestInputValidation:
    def test_generate_empty_prompt_rejected(self):
        from pydantic import ValidationError

        from cohort.mcp.local_llm_server import GenerateInput

        with pytest.raises(ValidationError):
            GenerateInput(prompt="")

    def test_generate_temperature_bounds(self):
        from pydantic import ValidationError

        from cohort.mcp.local_llm_server import GenerateInput

        with pytest.raises(ValidationError):
            GenerateInput(prompt="test", temperature=-0.1)

        with pytest.raises(ValidationError):
            GenerateInput(prompt="test", temperature=2.1)

        # Valid boundaries
        GenerateInput(prompt="test", temperature=0.0)
        GenerateInput(prompt="test", temperature=2.0)
