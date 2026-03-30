"""Configuration loader for Desktop Computer Use.

Loads from config/desktop_computer_use.yaml with path resolution
relative to the cohort data directory.
"""

import logging
import os
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

# Resolve paths relative to the cohort repo root
_THIS_DIR = Path(__file__).resolve().parent
COHORT_ROOT = _THIS_DIR.parent.parent  # cohort/cohort/desktop -> cohort/

# Config lives alongside other cohort configs
CONFIG_PATH = COHORT_ROOT / "config" / "desktop_computer_use.yaml"

# Data dir can be overridden via env var (same as cohort server)
_DATA_DIR = Path(os.environ.get("COHORT_DATA_DIR", str(COHORT_ROOT / "data")))


# ---------------------------------------------------------------------------
# Pydantic config models
# ---------------------------------------------------------------------------

class VirtualDisplayConfig(BaseModel):
    enabled: bool = True
    width: int = 1024
    height: int = 768
    refresh_rate: int = 60


class DesktopConfig(BaseModel):
    """Validated configuration for the desktop computer use server."""
    enabled: bool = False
    permission_tier: str = "desktop_advanced"

    virtual_display: VirtualDisplayConfig = Field(
        default_factory=VirtualDisplayConfig,
    )

    screenshot_dir: Path = Path("desktop_computer_use/screenshots")
    max_screenshot_rate_ms: int = 1000
    max_dimension: int = 1568
    max_screenshots_retained: int = 500

    window_allowlist: List[str] = Field(default_factory=list)
    allowed_apps: List[str] = Field(default_factory=list)
    blocked_key_combos: List[str] = Field(
        default_factory=lambda: ["ctrl+alt+delete", "win+l", "win+r"],
    )

    audit_log: Path = Path("desktop_computer_use/audit.jsonl")
    pre_action_screenshot: bool = True

    max_sessions: int = 2
    session_timeout_seconds: int = 600

    run_command_enabled: bool = False
    allowed_commands: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_config(config_path: Optional[Path] = None) -> DesktopConfig:
    """Load and validate config from YAML.

    Paths in the config are resolved relative to the cohort data directory.
    """
    path = config_path or CONFIG_PATH

    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    else:
        log.warning("Config not found at %s -- using defaults", path)
        raw = {}

    config = DesktopConfig(**raw)

    # Resolve relative paths against data dir
    if not config.screenshot_dir.is_absolute():
        config.screenshot_dir = _DATA_DIR / config.screenshot_dir
    if not config.audit_log.is_absolute():
        config.audit_log = _DATA_DIR / config.audit_log

    # Ensure directories exist
    config.screenshot_dir.mkdir(parents=True, exist_ok=True)
    config.audit_log.parent.mkdir(parents=True, exist_ok=True)

    return config
