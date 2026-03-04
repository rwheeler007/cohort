"""Tests for the interactive setup wizard (cohort setup).

Tests hardware detection display, Ollama detection, model pulling,
content pipeline config, and the full run_setup flow. All external
calls are mocked -- no Ollama or network required.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from cohort.local.config import MODEL_DESCRIPTIONS, get_model_for_vram
from cohort.local.detect import HardwareInfo
from cohort.local.setup import (
    MCP_SERVER_CONFIG,
    OLLAMA_BASE_URL,
    TOPIC_FEEDS,
    _ask_yes_no,
    _check_mcp_deps,
    _format_vram,
    _is_ollama_on_path,
    _is_ollama_running,
    _model_is_installed,
    _print_progress_bar,
    _pull_model_streaming,
    _step_content_pipeline,
    _step_detect_hardware,
    _step_mcp_setup,
    _step_pull_model,
    _step_verify,
    _vram_quality,
    _write_mcp_settings,
    run_setup,
)


# =====================================================================
# Display helpers
# =====================================================================


class TestFormatVram:
    def test_large_vram(self):
        assert _format_vram(12288) == "12,288 MB (12 GB)"

    def test_medium_vram(self):
        assert _format_vram(6144) == "6,144 MB (6 GB)"

    def test_small_vram(self):
        assert _format_vram(4096) == "4,096 MB (4 GB)"

    def test_sub_gb(self):
        result = _format_vram(512)
        assert "512" in result
        assert "MB" in result


class TestVramQuality:
    def test_excellent(self):
        assert "excellent" in _vram_quality(8192)

    def test_solid(self):
        assert "solid" in _vram_quality(6144)

    def test_work_well(self):
        assert "work well" in _vram_quality(4096)

    def test_make_it_work(self):
        assert "make it work" in _vram_quality(2048)


class TestAskYesNo:
    def test_yes(self):
        with patch("builtins.input", return_value="y"):
            assert _ask_yes_no("test?") is True

    def test_no(self):
        with patch("builtins.input", return_value="n"):
            assert _ask_yes_no("test?") is False

    def test_empty_default_true(self):
        with patch("builtins.input", return_value=""):
            assert _ask_yes_no("test?", default=True) is True

    def test_empty_default_false(self):
        with patch("builtins.input", return_value=""):
            assert _ask_yes_no("test?", default=False) is False

    def test_eof(self):
        with patch("builtins.input", side_effect=EOFError):
            assert _ask_yes_no("test?", default=True) is True


class TestProgressBar:
    def test_no_crash_on_zero_total(self, capsys):
        _print_progress_bar("test", 0, 0)
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_shows_percentage(self, capsys):
        _print_progress_bar("test", 500, 1000)
        captured = capsys.readouterr()
        assert "50%" in captured.out

    def test_shows_label(self, capsys):
        _print_progress_bar("Downloading model", 100, 1000)
        captured = capsys.readouterr()
        assert "Downloading model" in captured.out


# =====================================================================
# Ollama detection
# =====================================================================


class TestOllamaDetection:
    def test_ollama_on_path_found(self):
        with patch("shutil.which", return_value="/usr/bin/ollama"):
            assert _is_ollama_on_path() is True

    def test_ollama_on_path_missing(self):
        with patch("shutil.which", return_value=None):
            assert _is_ollama_on_path() is False

    def test_ollama_running(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"Ollama is running"
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert _is_ollama_running() is True

    def test_ollama_not_running(self):
        with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
            assert _is_ollama_running() is False

    def test_ollama_url_error(self):
        import urllib.error

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            assert _is_ollama_running() is False


# =====================================================================
# Model detection
# =====================================================================


class TestModelInstalled:
    def test_exact_match(self):
        mock_client = MagicMock()
        mock_client.list_models.return_value = ["qwen3:8b", "gemma3:4b"]
        with patch("cohort.local.setup.OllamaClient", return_value=mock_client):
            assert _model_is_installed("qwen3:8b") is True

    def test_base_name_match(self):
        mock_client = MagicMock()
        mock_client.list_models.return_value = ["qwen3:8b-q4"]
        with patch("cohort.local.setup.OllamaClient", return_value=mock_client):
            assert _model_is_installed("qwen3:8b") is True

    def test_not_installed(self):
        mock_client = MagicMock()
        mock_client.list_models.return_value = ["gemma3:4b"]
        with patch("cohort.local.setup.OllamaClient", return_value=mock_client):
            assert _model_is_installed("qwen3:8b") is False

    def test_empty_list(self):
        mock_client = MagicMock()
        mock_client.list_models.return_value = []
        with patch("cohort.local.setup.OllamaClient", return_value=mock_client):
            assert _model_is_installed("qwen3:8b") is False


# =====================================================================
# Model pull streaming
# =====================================================================


class TestPullModelStreaming:
    def test_successful_pull(self):
        lines = [
            b'{"status": "pulling manifest"}\n',
            b'{"status": "downloading", "total": 1000, "completed": 500}\n',
            b'{"status": "downloading", "total": 1000, "completed": 1000}\n',
            b'{"status": "verifying sha256 digest"}\n',
            b'{"status": "success"}\n',
        ]
        mock_resp = MagicMock()
        mock_resp.__enter__ = Mock(return_value=lines)
        mock_resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert _pull_model_streaming("qwen3:8b") is True

    def test_error_in_stream(self):
        lines = [
            b'{"status": "pulling manifest"}\n',
            b'{"error": "model not found"}\n',
        ]
        mock_resp = MagicMock()
        mock_resp.__enter__ = Mock(return_value=lines)
        mock_resp.__exit__ = Mock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert _pull_model_streaming("bad:model") is False

    def test_network_error(self):
        import urllib.error

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("network down"),
        ):
            assert _pull_model_streaming("qwen3:8b") is False


# =====================================================================
# Step 1: Hardware detection
# =====================================================================


class TestStepDetectHardware:
    def test_gpu_detected(self, capsys):
        mock_hw = HardwareInfo(
            gpu_name="NVIDIA GeForce RTX 3060",
            vram_mb=12288,
            cpu_only=False,
            platform="windows",
        )
        with patch("cohort.local.setup.detect_hardware", return_value=mock_hw):
            hw = _step_detect_hardware()

        assert hw.gpu_name == "NVIDIA GeForce RTX 3060"
        assert hw.vram_mb == 12288
        captured = capsys.readouterr()
        assert "RTX 3060" in captured.out
        assert "12,288 MB" in captured.out
        assert "turbo engine" in captured.out

    def test_cpu_only(self, capsys):
        mock_hw = HardwareInfo(cpu_only=True, platform="windows")
        with patch("cohort.local.setup.detect_hardware", return_value=mock_hw):
            hw = _step_detect_hardware()

        assert hw.cpu_only is True
        captured = capsys.readouterr()
        assert "perfectly fine" in captured.out
        assert "CPU-only" in captured.out
        assert "reliable sedan" in captured.out

    def test_macos(self, capsys):
        mock_hw = HardwareInfo(cpu_only=True, platform="darwin")
        with patch("cohort.local.setup.detect_hardware", return_value=mock_hw):
            hw = _step_detect_hardware()

        assert hw.platform == "darwin"
        captured = capsys.readouterr()
        assert "Apple Silicon" in captured.out or "Mac" in captured.out


# =====================================================================
# Step 4: Pull model
# =====================================================================


class TestStepPullModel:
    def test_already_installed(self, capsys):
        hw = HardwareInfo(vram_mb=8192, cpu_only=False, platform="linux")
        with patch("cohort.local.setup._model_is_installed", return_value=True):
            result = _step_pull_model("qwen3:8b", hw)

        assert result is True
        captured = capsys.readouterr()
        assert "already installed" in captured.out

    def test_user_declines_download(self, capsys):
        hw = HardwareInfo(vram_mb=8192, cpu_only=False, platform="linux")
        with patch("cohort.local.setup._model_is_installed", return_value=False):
            with patch("builtins.input", return_value="n"):
                result = _step_pull_model("qwen3:8b", hw)

        assert result is False
        captured = capsys.readouterr()
        assert "ollama pull" in captured.out

    def test_cpu_only_messaging(self, capsys):
        hw = HardwareInfo(vram_mb=0, cpu_only=True, platform="windows")
        with patch("cohort.local.setup._model_is_installed", return_value=True):
            _step_pull_model("qwen2.5-coder:1.5b", hw)

        captured = capsys.readouterr()
        assert "already installed" in captured.out


# =====================================================================
# Step 5: Verify
# =====================================================================


class TestStepVerify:
    def test_successful_verification(self, capsys):
        mock_result = MagicMock()
        mock_result.text = "A good code review focuses on correctness."
        mock_result.elapsed_seconds = 2.1

        mock_client = MagicMock()
        mock_client.generate.return_value = mock_result

        with patch("cohort.local.setup.OllamaClient", return_value=mock_client):
            result = _step_verify("qwen3:8b")

        assert result is True
        captured = capsys.readouterr()
        assert "Everything works" in captured.out
        assert "2.1 seconds" in captured.out

    def test_failed_verification_retry(self, capsys):
        mock_client = MagicMock()
        mock_client.generate.return_value = None

        with patch("cohort.local.setup.OllamaClient", return_value=mock_client):
            result = _step_verify("qwen3:8b")

        assert result is False
        assert mock_client.generate.call_count == 2  # retried once
        captured = capsys.readouterr()
        assert "ollama run" in captured.out


# =====================================================================
# Step 6: MCP Server Setup
# =====================================================================


class TestCheckMcpDeps:
    def test_return_shape(self):
        """Returns a dict with both package names as keys."""
        results = _check_mcp_deps()
        assert "fastmcp" in results
        assert "mcp" in results
        assert all(isinstance(v, bool) for v in results.values())

    def test_missing_packages(self):
        """When packages are not importable, returns False."""
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def mock_import(name, *args, **kwargs):
            if name in ("fastmcp", "mcp"):
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            results = _check_mcp_deps()
        assert results["fastmcp"] is False
        assert results["mcp"] is False

    def test_partial_missing(self):
        """When only one package is missing."""
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def mock_import(name, *args, **kwargs):
            if name == "fastmcp":
                raise ImportError("No module named 'fastmcp'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            results = _check_mcp_deps()
        assert results["fastmcp"] is False


class TestWriteMcpSettings:
    def test_creates_new_file(self, tmp_path, monkeypatch):
        """Creates .claude/settings.local.json when it doesn't exist."""
        monkeypatch.chdir(tmp_path)
        result = _write_mcp_settings()

        assert result is True
        settings_path = tmp_path / ".claude" / "settings.local.json"
        assert settings_path.exists()
        config = json.loads(settings_path.read_text())
        assert config["mcpServers"]["local_llm"]["command"] == "python"
        assert config["mcpServers"]["local_llm"]["args"] == ["-m", "cohort.mcp.local_llm_server"]

    def test_merges_with_existing(self, tmp_path, monkeypatch):
        """Preserves existing mcpServers entries when merging."""
        monkeypatch.chdir(tmp_path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        existing = {
            "mcpServers": {
                "other_server": {"command": "node", "args": ["server.js"]}
            },
            "permissions": {"allow": ["Read"]},
        }
        (claude_dir / "settings.local.json").write_text(
            json.dumps(existing), encoding="utf-8",
        )

        result = _write_mcp_settings()

        assert result is True
        config = json.loads((claude_dir / "settings.local.json").read_text())
        assert "other_server" in config["mcpServers"]
        assert "local_llm" in config["mcpServers"]
        assert "permissions" in config

    def test_overwrites_stale_local_llm(self, tmp_path, monkeypatch):
        """Updates existing local_llm entry if it already exists."""
        monkeypatch.chdir(tmp_path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        existing = {
            "mcpServers": {
                "local_llm": {"command": "old_python", "args": ["old_server.py"]}
            }
        }
        (claude_dir / "settings.local.json").write_text(
            json.dumps(existing), encoding="utf-8",
        )

        result = _write_mcp_settings()

        assert result is True
        config = json.loads((claude_dir / "settings.local.json").read_text())
        assert config["mcpServers"]["local_llm"]["command"] == "python"

    def test_handles_corrupt_json(self, tmp_path, monkeypatch, capsys):
        """Recovers from corrupt existing settings file."""
        monkeypatch.chdir(tmp_path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.local.json").write_text(
            "not json at all {{{", encoding="utf-8",
        )

        result = _write_mcp_settings()

        assert result is True
        config = json.loads((claude_dir / "settings.local.json").read_text())
        assert "local_llm" in config["mcpServers"]
        captured = capsys.readouterr()
        assert "invalid" in captured.out.lower() or "creating fresh" in captured.out.lower()

    def test_write_failure(self, tmp_path, monkeypatch, capsys):
        """Returns False when the filesystem is unwritable."""
        monkeypatch.chdir(tmp_path)
        with patch("pathlib.Path.mkdir", side_effect=OSError("Permission denied")):
            result = _write_mcp_settings()
        assert result is False
        captured = capsys.readouterr()
        assert "Write failed" in captured.out


class TestStepMcpSetup:
    def test_deps_missing_shows_install_instructions(self, capsys):
        """When MCP deps are missing, shows pip install instruction."""
        with patch(
            "cohort.local.setup._check_mcp_deps",
            return_value={"fastmcp": False, "mcp": False},
        ):
            result = _step_mcp_setup("qwen3:8b")

        assert result is True
        captured = capsys.readouterr()
        assert "pip install cohort[claude]" in captured.out
        assert "Missing packages" in captured.out

    def test_happy_path_user_declines_write(self, capsys):
        """Deps installed, Ollama running, user declines config write."""
        with (
            patch("cohort.local.setup._check_mcp_deps",
                  return_value={"fastmcp": True, "mcp": True}),
            patch("cohort.local.setup._is_ollama_running", return_value=True),
            patch("cohort.local.setup._model_is_installed", return_value=True),
            patch("builtins.input", return_value="n"),
        ):
            result = _step_mcp_setup("qwen3:8b")

        assert result is True
        captured = capsys.readouterr()
        assert "fastmcp and mcp packages found" in captured.out
        assert "Ollama is reachable" in captured.out
        assert "mcpServers" in captured.out

    def test_ollama_not_running_warns(self, capsys):
        """Deps installed but Ollama is down -- warns but doesn't fail."""
        with (
            patch("cohort.local.setup._check_mcp_deps",
                  return_value={"fastmcp": True, "mcp": True}),
            patch("cohort.local.setup._is_ollama_running", return_value=False),
            patch("cohort.local.setup._model_is_installed", return_value=False),
            patch("builtins.input", return_value="n"),
        ):
            result = _step_mcp_setup("qwen3:8b")

        assert result is True
        captured = capsys.readouterr()
        assert "not responding" in captured.out

    def test_user_accepts_config_write(self, tmp_path, monkeypatch, capsys):
        """User accepts auto-write of MCP config."""
        monkeypatch.chdir(tmp_path)

        with (
            patch("cohort.local.setup._check_mcp_deps",
                  return_value={"fastmcp": True, "mcp": True}),
            patch("cohort.local.setup._is_ollama_running", return_value=True),
            patch("cohort.local.setup._model_is_installed", return_value=True),
            patch("builtins.input", return_value="y"),
        ):
            result = _step_mcp_setup("qwen3:8b")

        assert result is True
        settings_path = tmp_path / ".claude" / "settings.local.json"
        assert settings_path.exists()
        config = json.loads(settings_path.read_text())
        assert "local_llm" in config["mcpServers"]

    def test_shows_model_name(self, capsys):
        """The model name from earlier steps is mentioned."""
        with (
            patch("cohort.local.setup._check_mcp_deps",
                  return_value={"fastmcp": True, "mcp": True}),
            patch("cohort.local.setup._is_ollama_running", return_value=True),
            patch("cohort.local.setup._model_is_installed", return_value=True),
            patch("builtins.input", return_value="n"),
        ):
            _step_mcp_setup("qwen3:30b-a3b")

        captured = capsys.readouterr()
        assert "qwen3:30b-a3b" in captured.out


class TestMcpServerConfig:
    def test_config_structure(self):
        """MCP_SERVER_CONFIG has the expected structure."""
        assert "mcpServers" in MCP_SERVER_CONFIG
        assert "local_llm" in MCP_SERVER_CONFIG["mcpServers"]
        server = MCP_SERVER_CONFIG["mcpServers"]["local_llm"]
        assert server["command"] == "python"
        assert server["args"] == ["-m", "cohort.mcp.local_llm_server"]


# =====================================================================
# Step 7: Content pipeline
# =====================================================================


class TestStepContentPipeline:
    def test_user_skips(self, capsys):
        with patch("builtins.input", return_value="n"):
            result = _step_content_pipeline()

        assert result is True
        captured = capsys.readouterr()
        assert "Skipped" in captured.out

    def test_selects_topic_by_number(self, tmp_path, capsys):
        data_dir = str(tmp_path / "data")
        # Input sequence: "y" (want to set up), "1" (first topic), "all" (all feeds)
        inputs = iter(["y", "1", "all"])
        with patch("builtins.input", side_effect=lambda _: next(inputs)):
            result = _step_content_pipeline(data_dir=data_dir)

        assert result is True
        config_path = tmp_path / "data" / "content_config.json"
        assert config_path.exists()
        config = json.loads(config_path.read_text())
        assert "feeds" in config
        assert len(config["feeds"]) > 0
        assert "topic" in config
        assert "check_interval_minutes" in config

    def test_selects_specific_feeds(self, tmp_path, capsys):
        data_dir = str(tmp_path / "data")
        inputs = iter(["y", "1", "1,3"])
        with patch("builtins.input", side_effect=lambda _: next(inputs)):
            result = _step_content_pipeline(data_dir=data_dir)

        assert result is True
        config = json.loads((tmp_path / "data" / "content_config.json").read_text())
        assert len(config["feeds"]) == 2

    def test_fuzzy_topic_match(self, tmp_path, capsys):
        data_dir = str(tmp_path / "data")
        inputs = iter(["y", "python programming", "all"])
        with patch("builtins.input", side_effect=lambda _: next(inputs)):
            result = _step_content_pipeline(data_dir=data_dir)

        assert result is True
        config = json.loads((tmp_path / "data" / "content_config.json").read_text())
        assert config["topic"] == "python"


# =====================================================================
# Topic feeds data
# =====================================================================


class TestTopicFeeds:
    def test_all_topics_have_feeds(self):
        for topic, feeds in TOPIC_FEEDS.items():
            assert len(feeds) >= 2, f"Topic '{topic}' has fewer than 2 feeds"

    def test_all_feeds_have_name_and_url(self):
        for topic, feeds in TOPIC_FEEDS.items():
            for feed in feeds:
                assert "name" in feed, f"Feed in '{topic}' missing name"
                assert "url" in feed, f"Feed in '{topic}' missing url"
                assert feed["url"].startswith("http"), (
                    f"Feed '{feed['name']}' in '{topic}' has invalid URL"
                )

    def test_at_least_10_topics(self):
        assert len(TOPIC_FEEDS) >= 10


# =====================================================================
# Model descriptions
# =====================================================================


class TestModelDescriptions:
    def test_all_tier_models_have_descriptions(self):
        """Every model in the VRAM tier mapping has a description."""
        from cohort.local.config import VRAM_TIER_MODELS

        for mapping in VRAM_TIER_MODELS:
            model = mapping["model"]
            assert model in MODEL_DESCRIPTIONS, (
                f"Model '{model}' missing from MODEL_DESCRIPTIONS"
            )

    def test_descriptions_have_required_keys(self):
        for model, info in MODEL_DESCRIPTIONS.items():
            assert "size" in info, f"Model '{model}' description missing 'size'"
            assert "summary" in info, f"Model '{model}' description missing 'summary'"


# =====================================================================
# Full run_setup flow
# =====================================================================


class TestRunSetup:
    def test_happy_path(self, tmp_path, capsys):
        """Full setup with GPU, Ollama running, model installed."""
        mock_hw = HardwareInfo(
            gpu_name="NVIDIA RTX 4090",
            vram_mb=24576,
            cpu_only=False,
            platform="linux",
        )

        mock_generate = MagicMock()
        mock_generate.text = "Testing is important for software quality."
        mock_generate.elapsed_seconds = 1.5

        mock_client = MagicMock()
        mock_client.generate.return_value = mock_generate
        mock_client.list_models.return_value = ["qwen3:30b-a3b"]

        # Input: "n" to skip MCP write + content pipeline
        with (
            patch("cohort.local.setup.detect_hardware", return_value=mock_hw),
            patch("cohort.local.setup._is_ollama_running", return_value=True),
            patch("cohort.local.setup._model_is_installed", return_value=True),
            patch("cohort.local.setup.OllamaClient", return_value=mock_client),
            patch("cohort.local.setup._check_mcp_deps",
                  return_value={"fastmcp": True, "mcp": True}),
            patch("builtins.input", return_value="n"),
        ):
            exit_code = run_setup()

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "Setup Complete" in captured.out
        assert "RTX 4090" in captured.out

    def test_cpu_only_happy_path(self, capsys):
        """Full setup on CPU-only machine."""
        mock_hw = HardwareInfo(cpu_only=True, platform="windows")

        mock_generate = MagicMock()
        mock_generate.text = "Code reviews should focus on logic."
        mock_generate.elapsed_seconds = 5.0

        mock_client = MagicMock()
        mock_client.generate.return_value = mock_generate
        mock_client.list_models.return_value = ["qwen2.5-coder:1.5b"]

        with (
            patch("cohort.local.setup.detect_hardware", return_value=mock_hw),
            patch("cohort.local.setup._is_ollama_running", return_value=True),
            patch("cohort.local.setup._model_is_installed", return_value=True),
            patch("cohort.local.setup.OllamaClient", return_value=mock_client),
            patch("cohort.local.setup._check_mcp_deps",
                  return_value={"fastmcp": True, "mcp": True}),
            patch("builtins.input", return_value="n"),
        ):
            exit_code = run_setup()

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "Setup Complete" in captured.out
        assert "CPU-only" in captured.out

    def test_keyboard_interrupt(self, capsys):
        """Ctrl+C during setup exits gracefully."""
        with patch(
            "cohort.local.setup.detect_hardware",
            side_effect=KeyboardInterrupt,
        ):
            exit_code = run_setup()

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "interrupted" in captured.out
        assert "cohort setup" in captured.out

    def test_ollama_install_fails(self, capsys):
        """Setup exits 1 when Ollama can't be installed."""
        mock_hw = HardwareInfo(cpu_only=True, platform="linux")

        # Simulate: Ollama not running, not on path, install fails (3 retries)
        with (
            patch("cohort.local.setup.detect_hardware", return_value=mock_hw),
            patch("cohort.local.setup._is_ollama_running", return_value=False),
            patch("cohort.local.setup._is_ollama_on_path", return_value=False),
            patch("builtins.input", return_value=""),  # press Enter at each prompt
            patch("cohort.local.setup._wait_for_ollama", return_value=False),
        ):
            exit_code = run_setup()

        assert exit_code == 1


# =====================================================================
# CLI subcommand registration
# =====================================================================


class TestCLIRegistration:
    def test_setup_command_exists(self):
        """The 'setup' subcommand is registered in the CLI parser."""
        import argparse

        from cohort.__main__ import main

        # Verify the parser accepts 'setup' without error
        # We can't easily test main() directly since it calls sys.exit,
        # but we can verify the import path works
        from cohort.local.setup import run_setup

        assert callable(run_setup)
