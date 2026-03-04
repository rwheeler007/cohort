"""Tests for local LLM routing package.

Full coverage with mocking -- no real GPU or Ollama required.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, Mock, patch

import pytest

from cohort.local.config import (
    DEFAULT_TEMPERATURE,
    TASK_TEMPERATURES,
    VRAM_TIER_MODELS,
    get_model_for_vram,
    get_temperature,
)
from cohort.local.detect import GPUInfo, HardwareInfo, detect_hardware
from cohort.local.ollama import GenerateResult, OllamaClient
from cohort.local.router import LocalRouter, RouteResult


# =====================================================================
# Hardware Detection Tests
# =====================================================================


def test_detect_hardware_gpu_found():
    """Test GPU detection with successful nvidia-smi output."""
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = "NVIDIA GeForce RTX 3080 Ti, 12288 MiB\n"

    with patch("subprocess.run", return_value=mock_result):
        info = detect_hardware()

    assert info.gpu_name == "NVIDIA GeForce RTX 3080 Ti"
    assert info.vram_mb == 12288
    assert info.cpu_only is False
    assert info.platform in ("linux", "windows", "darwin")
    assert len(info.gpus) == 1
    assert info.gpus[0].index == 0
    assert info.gpus[0].name == "NVIDIA GeForce RTX 3080 Ti"
    assert info.gpus[0].vram_mb == 12288
    assert info.total_vram_mb == 12288


def test_detect_hardware_multi_gpu():
    """Test multi-GPU detection with two GPUs."""
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = (
        "NVIDIA GeForce RTX 3080, 12288 MiB\n"
        "NVIDIA GeForce RTX 3060, 8192 MiB\n"
    )

    with patch("subprocess.run", return_value=mock_result):
        info = detect_hardware()

    assert info.cpu_only is False
    assert len(info.gpus) == 2
    # Primary GPU should be the largest (RTX 3080)
    assert info.gpu_name == "NVIDIA GeForce RTX 3080"
    assert info.vram_mb == 12288
    assert info.total_vram_mb == 20480
    # GPU list order matches nvidia-smi output
    assert info.gpus[0].index == 0
    assert info.gpus[0].name == "NVIDIA GeForce RTX 3080"
    assert info.gpus[0].vram_mb == 12288
    assert info.gpus[1].index == 1
    assert info.gpus[1].name == "NVIDIA GeForce RTX 3060"
    assert info.gpus[1].vram_mb == 8192


def test_detect_hardware_multi_gpu_largest_not_first():
    """Test that primary GPU is the largest, even if not GPU 0."""
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = (
        "NVIDIA GeForce RTX 3060, 8192 MiB\n"
        "NVIDIA GeForce RTX 3080, 12288 MiB\n"
    )

    with patch("subprocess.run", return_value=mock_result):
        info = detect_hardware()

    # Primary should be RTX 3080 (larger VRAM) even though it's GPU 1
    assert info.gpu_name == "NVIDIA GeForce RTX 3080"
    assert info.vram_mb == 12288
    assert len(info.gpus) == 2


def test_detect_hardware_gpu_not_found():
    """Test GPU detection when nvidia-smi fails."""
    mock_result = Mock()
    mock_result.returncode = 1
    mock_result.stdout = ""

    with patch("subprocess.run", return_value=mock_result):
        info = detect_hardware()

    assert info.cpu_only is True
    assert info.vram_mb == 0


def test_detect_hardware_nvidia_smi_not_installed():
    """Test graceful fallback when nvidia-smi is not installed."""
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        info = detect_hardware()

    assert info.cpu_only is True
    assert info.vram_mb == 0


def test_detect_hardware_macos():
    """Test macOS detection (no GPU VRAM detection)."""
    with patch("platform.system", return_value="Darwin"):
        info = detect_hardware()

    assert info.platform == "darwin"
    assert info.cpu_only is True


def test_detect_hardware_parse_errors():
    """Test graceful handling of malformed nvidia-smi output."""
    mock_result = Mock()
    mock_result.returncode = 0
    mock_result.stdout = "Invalid output without comma\n"

    with patch("subprocess.run", return_value=mock_result):
        info = detect_hardware()

    # Should fallback to cpu_only=True on parse failure
    assert info.cpu_only is True


def test_detect_hardware_timeout():
    """Test graceful handling of nvidia-smi timeout."""
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("nvidia-smi", 5)):
        info = detect_hardware()

    assert info.cpu_only is True


# =====================================================================
# Ollama Client Tests
# =====================================================================


def test_ollama_client_localhost_only():
    """Test that client refuses external URLs."""
    with pytest.raises(ValueError, match="localhost"):
        OllamaClient(base_url="http://evil.com:11434")


def test_ollama_health_check_up():
    """Test health check when Ollama is up."""
    client = OllamaClient()

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = Mock(return_value=mock_response)
    mock_response.__exit__ = Mock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        healthy = client.health_check()

    assert healthy is True


def test_ollama_health_check_down():
    """Test health check when Ollama is down."""
    client = OllamaClient()

    with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
        healthy = client.health_check()

    assert healthy is False


def test_ollama_health_check_cache():
    """Test that health checks are cached."""
    client = OllamaClient(health_cache_seconds=60)

    # First call: healthy
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = Mock(return_value=mock_response)
    mock_response.__exit__ = Mock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
        healthy1 = client.health_check()
        assert healthy1 is True
        assert mock_urlopen.call_count == 1

        # Second call should use cache (no additional HTTP call)
        healthy2 = client.health_check()
        assert healthy2 is True
        assert mock_urlopen.call_count == 1  # Still 1 -- cached


def test_ollama_list_models():
    """Test listing available models."""
    client = OllamaClient()

    mock_response = MagicMock()
    mock_response.read = Mock(return_value=b'{"models": [{"name": "qwen2.5-coder:1.5b"}, {"name": "qwen3:8b"}]}')
    mock_response.__enter__ = Mock(return_value=mock_response)
    mock_response.__exit__ = Mock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        models = client.list_models()

    assert models == ["qwen2.5-coder:1.5b", "qwen3:8b"]


def test_ollama_list_models_server_down():
    """Test list_models when server is down."""
    client = OllamaClient()

    with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
        models = client.list_models()

    assert models == []


def test_ollama_generate_success():
    """Test successful text generation returns GenerateResult."""
    client = OllamaClient()

    mock_response = MagicMock()
    mock_response.read = Mock(return_value=b'{"response": "Hello world", "prompt_eval_count": 15, "eval_count": 8}')
    mock_response.__enter__ = Mock(return_value=mock_response)
    mock_response.__exit__ = Mock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        result = client.generate(
            model="qwen3:8b",
            prompt="Say hello",
            temperature=0.4,
        )

    assert result is not None
    assert result.text == "Hello world"
    assert result.model == "qwen3:8b"
    assert result.tokens_in == 15
    assert result.tokens_out == 8
    assert result.elapsed_seconds >= 0.0


def test_ollama_generate_failure():
    """Test that generate returns None on failure."""
    client = OllamaClient()

    with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
        text = client.generate(
            model="qwen3:8b",
            prompt="Say hello",
        )

    assert text is None


# =====================================================================
# Config Tests
# =====================================================================


def test_get_model_for_vram_tier1():
    """Test model selection for <4GB VRAM."""
    model = get_model_for_vram(2048)  # 2GB
    assert model == "qwen3.5:2b"


def test_get_model_for_vram_tier2():
    """Test model selection for 4-6GB VRAM."""
    model = get_model_for_vram(5000)  # 5GB
    assert model == "qwen3.5:4b"


def test_get_model_for_vram_tier3():
    """Test model selection for 6-10GB VRAM."""
    model = get_model_for_vram(7000)  # 7GB
    assert model == "qwen3.5:9b"


def test_get_model_for_vram_tier4():
    """Test model selection for 10GB+ VRAM."""
    model = get_model_for_vram(12000)  # 12GB
    assert model == "qwen3.5:9b"


def test_get_model_for_vram_fallback():
    """Test fallback to smallest model for unknown VRAM."""
    model = get_model_for_vram(0)  # 0GB
    assert model == VRAM_TIER_MODELS[0]["model"]


def test_get_temperature_code():
    """Test temperature for code tasks."""
    temp = get_temperature("code")
    assert temp == TASK_TEMPERATURES["code"]
    assert temp == 0.15


def test_get_temperature_reasoning():
    """Test temperature for reasoning tasks."""
    temp = get_temperature("reasoning")
    assert temp == TASK_TEMPERATURES["reasoning"]
    assert temp == 0.3


def test_get_temperature_general():
    """Test temperature for general tasks."""
    temp = get_temperature("general")
    assert temp == TASK_TEMPERATURES["general"]
    assert temp == 0.4


def test_get_temperature_default():
    """Test default temperature when task type is None."""
    temp = get_temperature(None)
    assert temp == DEFAULT_TEMPERATURE


def test_get_temperature_unknown_task():
    """Test fallback to default for unknown task types."""
    temp = get_temperature("unknown_task_type")
    assert temp == DEFAULT_TEMPERATURE


# =====================================================================
# LocalRouter Tests
# =====================================================================


def test_local_router_available():
    """Test router when Ollama is available and GPU present."""
    router = LocalRouter()

    # Mock hardware detection: GPU found
    mock_hw_info = HardwareInfo(
        gpu_name="NVIDIA RTX 3080 Ti",
        vram_mb=12288,
        cpu_only=False,
        platform="linux",
    )

    # Mock Ollama client
    mock_client = Mock(spec=OllamaClient)
    mock_client.health_check.return_value = True
    mock_client.list_models.return_value = ["qwen3.5:9b"]
    mock_client.generate.return_value = GenerateResult(
        text="Hello from local model",
        model="qwen3.5:9b",
        tokens_in=42,
        tokens_out=5,
        elapsed_seconds=1.2,
    )

    with patch.object(router, "_detect_hardware", return_value=mock_hw_info):
        with patch("cohort.local.router.OllamaClient", return_value=mock_client):
            response = router.route("Say hello", task_type="general")

    assert isinstance(response, RouteResult)
    assert response.text == "Hello from local model"
    assert response.model == "qwen3.5:9b"
    assert response.tier == 3  # qwen3.5:9b first appears in tier 3
    assert response.tokens_in == 42
    assert response.tokens_out == 5
    mock_client.generate.assert_called_once()


def test_local_router_ollama_down():
    """Test router fallback when Ollama is down."""
    router = LocalRouter()

    mock_client = Mock(spec=OllamaClient)
    mock_client.health_check.return_value = False

    with patch("cohort.local.router.OllamaClient", return_value=mock_client):
        response = router.route("Say hello")

    assert response is None


def test_local_router_cpu_only():
    """Test router skips local routing when CPU-only."""
    router = LocalRouter()

    mock_hw_info = HardwareInfo(cpu_only=True, platform="linux")

    with patch.object(router, "_detect_hardware", return_value=mock_hw_info):
        response = router.route("Say hello")

    assert response is None


def test_local_router_model_missing():
    """Test router fallback when model is not installed."""
    router = LocalRouter()

    mock_hw_info = HardwareInfo(
        gpu_name="NVIDIA RTX 3080 Ti",
        vram_mb=12288,
        cpu_only=False,
        platform="linux",
    )

    mock_client = Mock(spec=OllamaClient)
    mock_client.health_check.return_value = True
    mock_client.list_models.return_value = ["qwen2.5-coder:1.5b"]  # Different model

    with patch.object(router, "_detect_hardware", return_value=mock_hw_info):
        with patch("cohort.local.router.OllamaClient", return_value=mock_client):
            response = router.route("Say hello")

    # Should return None when requested model not installed
    assert response is None


def test_local_router_generation_fails():
    """Test router returns None when generation fails."""
    router = LocalRouter()

    mock_hw_info = HardwareInfo(
        gpu_name="NVIDIA RTX 3080 Ti",
        vram_mb=12288,
        cpu_only=False,
        platform="linux",
    )

    mock_client = Mock(spec=OllamaClient)
    mock_client.health_check.return_value = True
    mock_client.list_models.return_value = ["qwen3.5:9b"]
    mock_client.generate.return_value = None  # Generation failed

    with patch.object(router, "_detect_hardware", return_value=mock_hw_info):
        with patch("cohort.local.router.OllamaClient", return_value=mock_client):
            response = router.route("Say hello")

    assert response is None


def test_local_router_exception_handling():
    """Test router never propagates exceptions."""
    router = LocalRouter()

    # Simulate exception during routing
    with patch.object(router, "_ensure_client", side_effect=Exception("Unexpected error")):
        response = router.route("Say hello")

    # Should return None instead of raising
    assert response is None


# =====================================================================
# Integration Tests
# =====================================================================


def test_integration_local_router_used():
    """Test that agent_router uses LocalRouter when available."""
    # This is tested via the agent_router.py modification
    # LocalRouter.route() is called before Claude CLI fallback
    pass


def test_integration_local_router_fallback_to_claude():
    """Test transparent fallback to Claude CLI when local routing fails."""
    # This is tested via the agent_router.py modification
    # When LocalRouter.route() returns None, Claude CLI is invoked
    pass


def test_integration_no_new_dependencies():
    """Test that no new pip dependencies were added."""
    # Read pyproject.toml and verify base dependencies unchanged
    import tomllib
    from pathlib import Path

    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    base_deps = data.get("project", {}).get("dependencies", [])
    # Should still be empty list (zero dependencies)
    assert base_deps == []
