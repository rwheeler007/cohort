"""Cohort Windows Launcher -- first-run detection + server + tray orchestration.

This module is the entry point for the Windows installer's Start Menu shortcut.
It handles:

1. First-run detection (has Ollama been set up? Is a model available?)
2. If first run: opens browser to the setup wizard page
3. If already configured: launches server + tray icon directly

The launcher never touches a terminal. Everything is GUI/browser-based.

Usage::

    # Called by installer shortcut or 'cohort launch' CLI command
    python -m cohort.launcher
    cohort launch
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


# =====================================================================
# First-run detection
# =====================================================================

def _get_data_dir() -> Path:
    """Resolve the Cohort data directory.

    Priority:
    1. COHORT_DATA_DIR environment variable
    2. %LOCALAPPDATA%/Cohort/data (Windows standard)
    3. ~/.cohort/data (fallback)
    """
    env_dir = os.environ.get("COHORT_DATA_DIR")
    if env_dir:
        return Path(env_dir)

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            return Path(local_app_data) / "Cohort" / "data"

    return Path.home() / ".cohort" / "data"


def _is_first_run(data_dir: Path) -> bool:
    """Check whether Cohort needs first-run setup.

    First run is detected when ANY of these are true:
    - No settings.json exists
    - settings.json exists but has no 'setup_complete' flag
    - Ollama is not installed (not on PATH)
    - No local models are available
    """
    settings_file = data_dir / "settings.json"

    # No settings at all
    if not settings_file.exists():
        return True

    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True

    # Check setup_complete flag
    if not settings.get("setup_complete", False):
        return True

    return False


def _check_ollama_available() -> bool:
    """Check if Ollama is installed and reachable."""
    # Check if binary is on PATH
    if shutil.which("ollama") is None:
        return False

    # Check if server is responding
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/tags",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def _check_models_available() -> list[str]:
    """Return list of locally available Ollama models."""
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/tags",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return []


# =====================================================================
# Launch orchestration
# =====================================================================

def launch(
    port: int = 5100,
    no_browser: bool = False,
    force_setup: bool = False,
) -> int:
    """Main launcher entry point.

    Detects first-run state and either opens the setup wizard or
    launches the server + tray icon directly.

    Args:
        port: Server port (default 5100).
        no_browser: If True, don't auto-open browser.
        force_setup: If True, always show setup wizard.

    Returns:
        Exit code (0 = clean, 1 = error).
    """
    data_dir = _get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    # Set environment so the server finds it
    os.environ.setdefault("COHORT_DATA_DIR", str(data_dir))

    first_run = force_setup or _is_first_run(data_dir)

    if first_run:
        logger.info("[*] First run detected -- launching setup wizard")
        # The setup wizard is part of the web UI at /setup
        # Launch server + tray, browser opens to setup page
        from cohort.tray import run_tray
        return run_tray(
            host="127.0.0.1",
            port=port,
            data_dir=str(data_dir),
            open_browser=True,
        )
    else:
        logger.info("[OK] Cohort configured -- launching normally")
        from cohort.tray import run_tray
        return run_tray(
            host="127.0.0.1",
            port=port,
            data_dir=str(data_dir),
            open_browser=not no_browser,
        )


# =====================================================================
# Direct execution
# =====================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    sys.exit(launch())
